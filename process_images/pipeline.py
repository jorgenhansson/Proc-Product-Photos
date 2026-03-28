"""Main pipeline orchestrator: discovers, processes, and routes images.

Supports both sequential and parallel execution.  In parallel mode,
the CPU-heavy work (load → crop → validate → fallback → encode) runs
in a process pool while the main thread handles I/O, stats, and
review artifacts sequentially.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .checkpoint import Checkpoint
from .config import PipelineConfig
from .crop.base import CropStrategy
from .io_utils import discover_images, load_image, save_image
from .mapping import MappingLookup
from .models import (
    Flag,
    ImageContext,
    ProcessingResult,
    ProcessingStatus,
)
from .reporting import generate_side_by_side, write_review_manifest
from .statistics import StatsAccumulator
from .validators import validate_crop_result

logger = logging.getLogger(__name__)


class QualityGateError(RuntimeError):
    """Raised when quality gate action is 'abort' and a category breaches."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.detail = message


# ---------------------------------------------------------------------------
# Top-level worker function (must be picklable → module-level, not a method)
# ---------------------------------------------------------------------------

def _process_worker(
    img_path: Path,
    config: PipelineConfig,
    mapping_rows_by_sku: dict,
    fallback_enabled: bool,
) -> dict:
    """Self-contained worker: load → crop → validate → fallback → encode.

    Runs in a child process.  Returns a plain dict (no numpy arrays)
    with everything the main thread needs to write files and record stats.

    The encoded JPEG/PNG bytes are returned so the main thread only needs
    to write them to disk — no image processing on the main thread.
    """
    import time as _time
    import numpy as np
    from .config import PipelineConfig
    from .crop.classical import ClassicalCropStrategy
    from .crop.ai_fallback import AIFallbackCropStrategy
    from .io_utils import load_image, encode_image
    from .models import (
        Flag, ImageContext, CropMetrics, ProcessingStatus, BackgroundType,
    )
    from .validators import validate_crop_result

    t0 = _time.perf_counter()
    sku = img_path.stem

    out: dict = {
        "source_path": img_path,
        "status": ProcessingStatus.FAILED,
        "flags": [],
        "category": "",
        "background_type": None,
        "source_dimensions": (0, 0),
        "source_size_bytes": 0,
        "crop_metrics": None,
        "fallback_metrics": None,
        "fallback_attempted": False,
        "fallback_time_s": 0.0,
        "crop_time_s": 0.0,
        "processing_time_s": 0.0,
        "error_message": "",
        "proposed_filenames": [],
        # Encoded image bytes (JPEG/PNG) ready for disk write
        "final_image_bytes": None,
        # Review artifacts (only for flagged/failed)
        "review_original_copy": False,
        "review_preview_bytes": None,
    }

    try:
        out["source_size_bytes"] = img_path.stat().st_size
    except OSError:
        pass

    # -- Mapping lookup --
    rows_data = mapping_rows_by_sku.get(sku) or mapping_rows_by_sku.get(img_path.name)
    if not rows_data:
        out["flags"] = [Flag.MISSING_MAPPING]
        out["status"] = ProcessingStatus.FLAGGED
        out["review_original_copy"] = True
        out["processing_time_s"] = _time.perf_counter() - t0
        return out

    # Reconstruct MappingRow objects
    from .models import MappingRow
    rows = [MappingRow(**rd) for rd in rows_data]

    category = rows[0].category
    out["category"] = category

    gc = config.global_config
    fn_pattern = gc.filename_pattern
    out_ext = gc.output_format.lower().replace("jpeg", "jpg")
    source_stem = img_path.stem
    out["proposed_filenames"] = [
        row.output_filename_for_source(source_stem, fn_pattern, out_ext)
        for row in rows
    ]

    # -- Load image --
    image = load_image(img_path)
    if image is None:
        out["flags"] = [Flag.IMAGE_READ_ERROR]
        out["status"] = ProcessingStatus.FAILED
        out["error_message"] = "Failed to load image"
        out["processing_time_s"] = _time.perf_counter() - t0
        return out

    out["source_dimensions"] = (image.shape[1], image.shape[0])

    context = ImageContext(
        source_path=img_path,
        mapping_rows=rows,
        category=category,
    )

    # -- Primary crop --
    primary = ClassicalCropStrategy()
    t_crop = _time.perf_counter()
    crop_result = primary.crop(image, context, config)
    out["crop_time_s"] = _time.perf_counter() - t_crop
    out["background_type"] = crop_result.background_type
    context.background_type = crop_result.background_type

    # -- Validate --
    validation_flags = validate_crop_result(
        crop_result, image.shape, context, config
    )
    all_flags = list(dict.fromkeys(
        crop_result.flags + validation_flags
    ))
    out["crop_metrics"] = crop_result.metrics

    blocking_flags = [f for f in all_flags if f != Flag.NAMING_CONFLICT]
    primary_ok = len(blocking_flags) == 0 and crop_result.final_image is not None

    if primary_ok:
        out["status"] = ProcessingStatus.OK
        out["flags"] = all_flags
        out["final_image_bytes"] = encode_image(
            crop_result.final_image, quality=gc.jpeg_quality, output_format=out_ext
        )
    elif fallback_enabled:
        # -- Fallback --
        out["fallback_attempted"] = True
        context.prior_mask = crop_result.mask
        context.primary_flags = blocking_flags
        context.primary_result = crop_result

        fallback = AIFallbackCropStrategy()
        t_fb = _time.perf_counter()
        fb_result = fallback.crop(image, context, config)
        out["fallback_time_s"] = _time.perf_counter() - t_fb
        out["fallback_metrics"] = fb_result.metrics

        fb_validation = validate_crop_result(
            fb_result, image.shape, context, config,
            tolerance=config.fallback.validation_tolerance,
        )
        fb_all_flags = list(dict.fromkeys(fb_result.flags + fb_validation))

        if len(fb_all_flags) == 0 and fb_result.final_image is not None:
            out["status"] = ProcessingStatus.RECOVERED
            out["flags"] = all_flags
            out["final_image_bytes"] = encode_image(
                fb_result.final_image, quality=gc.jpeg_quality, output_format=out_ext
            )
        else:
            out["status"] = ProcessingStatus.FLAGGED
            out["flags"] = all_flags + [f for f in fb_all_flags if f not in all_flags]
            out["review_original_copy"] = True
            # Generate preview in-process (avoids shipping numpy arrays)
            try:
                from .reporting import encode_side_by_side
                out["review_preview_bytes"] = encode_side_by_side(
                    image[:, :, :3] if image.ndim == 3 and image.shape[2] >= 3 else image,
                    crop_result.mask, crop_result.cropped_image, crop_result.final_image,
                )
            except Exception:
                pass
    else:
        out["status"] = ProcessingStatus.FLAGGED
        out["flags"] = all_flags
        out["review_original_copy"] = True
        try:
            from .reporting import encode_side_by_side
            out["review_preview_bytes"] = encode_side_by_side(
                image[:, :, :3] if image.ndim == 3 and image.shape[2] >= 3 else image,
                crop_result.mask, crop_result.cropped_image, crop_result.final_image,
            )
        except Exception:
            pass

    out["processing_time_s"] = _time.perf_counter() - t0
    return out


def _serialize_mapping(mapping: MappingLookup) -> dict[str, list[dict]]:
    """Convert MappingLookup to a plain dict of dicts for pickling.

    Workers receive this instead of the full MappingLookup object.
    Keys are both SKU stem and full filename for flexible matching.
    """
    from dataclasses import asdict
    result: dict[str, list[dict]] = {}
    for sku, rows in mapping.rows_by_sku.items():
        result[sku] = [asdict(row) for row in rows]
    return result


class Pipeline:
    """Orchestrates the full image processing pipeline.

    Supports sequential (default) and parallel execution modes.
    In parallel mode, CPU-heavy work runs in a process pool.
    """

    def __init__(
        self,
        config: PipelineConfig,
        mapping: MappingLookup,
        primary: CropStrategy,
        fallback: Optional[CropStrategy] = None,
    ) -> None:
        self.config = config
        self.mapping = mapping
        self.primary = primary
        self.fallback = fallback
        # Mutable per-run state — reset in run() (#22)
        self.stats = StatsAccumulator()
        self._seen_outputs: set[str] = set()
        self._quality_gate_aborted: bool = False

    def run(
        self,
        input_dir: Path,
        output_dir: Path,
        review_dir: Path,
        limit: Optional[int] = None,
        workers: int = 0,
        checkpoint: Optional[Checkpoint] = None,
    ) -> StatsAccumulator:
        """Discover and process all images in input_dir.

        Args:
            input_dir: Directory containing supplier images.
            output_dir: Directory for successfully processed images.
            review_dir: Directory for flagged/failed images and review data.
            limit: If set, process only the first N images.
            workers: Number of parallel workers.  0 = sequential (default).
                     Use os.cpu_count() for max parallelism.
            checkpoint: Optional Checkpoint for resume support.

        Returns:
            StatsAccumulator with all results.

        Raises:
            QualityGateError: If quality gate action is 'abort' and a
                category drops below the minimum success rate.
        """
        # -- Reset per-run state (#22) --
        self.stats = StatsAccumulator()
        self._seen_outputs = set()
        self._quality_gate_aborted = False

        images = discover_images(input_dir)
        self.stats.total_discovered = len(images)
        logger.info("Discovered %d images in %s", len(images), input_dir)

        if limit is not None and limit > 0:
            images = images[:limit]
            logger.info("Limiting to first %d images", limit)

        # Filter out already-completed images if resuming
        if checkpoint is not None:
            before = len(images)
            images = [
                img for img in images
                if not checkpoint.is_done(img.name)
            ]
            skipped = before - len(images)
            if skipped > 0:
                logger.info(
                    "Resuming: skipping %d already-processed images, %d remaining",
                    skipped, len(images),
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)

        if workers > 1:
            self._run_parallel(images, output_dir, review_dir, workers, checkpoint)
        else:
            self._run_sequential(images, output_dir, review_dir, checkpoint)

        # Final checkpoint flush
        if checkpoint is not None:
            checkpoint.flush()

        # Write review manifest
        write_review_manifest(self.stats.results, review_dir / "manifest.json")

        return self.stats

    def _run_sequential(
        self,
        images: list[Path],
        output_dir: Path,
        review_dir: Path,
        checkpoint: Optional[Checkpoint] = None,
    ) -> None:
        """Process images one by one (original behavior)."""
        flush_interval = 10  # flush checkpoint every N images
        qg = self.config.quality_gate

        for i, img_path in enumerate(images, 1):
            logger.info(
                "[%d/%d] Processing %s", i, len(images), img_path.name
            )
            result = self._process_one(img_path, output_dir, review_dir)
            self.stats.record(result)

            if checkpoint is not None:
                checkpoint.record(
                    img_path.name,
                    result.status,
                    [str(p.name) for p in result.output_paths],
                    result.flags,
                )
                if i % flush_interval == 0:
                    checkpoint.flush()

            # Quality gate check
            if qg.enabled and i % qg.check_interval == 0:
                breach = self._check_quality_gate()
                if breach:
                    if qg.action == "abort":
                        self._quality_gate_aborted = True
                        raise QualityGateError(breach)
                    # "warn" — log and continue

    def _run_parallel(
        self,
        images: list[Path],
        output_dir: Path,
        review_dir: Path,
        workers: int,
        checkpoint: Optional[Checkpoint] = None,
    ) -> None:
        """Process images in parallel using a process pool.

        The CPU-heavy work (load, crop, validate, fallback, encode) runs
        in child processes.  The main thread handles file writes, stats
        recording, and collision detection sequentially.
        """
        logger.info(
            "Parallel mode: %d workers for %d images", workers, len(images)
        )

        # Serialize mapping for workers (dicts of dicts, fully picklable)
        mapping_data = _serialize_mapping(self.mapping)
        fallback_enabled = bool(self.fallback and self.config.fallback.enabled)
        gc = self.config.global_config
        fn_pattern = gc.filename_pattern
        out_ext = gc.output_format.lower().replace("jpeg", "jpg")

        completed = 0
        total = len(images)

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _process_worker,
                    img_path,
                    self.config,
                    mapping_data,
                    fallback_enabled,
                ): img_path
                for img_path in images
            }

            for future in as_completed(futures):
                completed += 1
                img_path = futures[future]
                try:
                    worker_out = future.result()
                except Exception as exc:
                    logger.error(
                        "[%d/%d] Worker crashed for %s: %s",
                        completed, total, img_path.name, exc,
                    )
                    result = ProcessingResult(source_path=img_path)
                    result.status = ProcessingStatus.FAILED
                    result.flags = [Flag.IMAGE_READ_ERROR]
                    result.error_message = str(exc)
                    self.stats.record(result)
                    if checkpoint is not None:
                        checkpoint.record(
                            img_path.name, result.status,
                            [], result.flags,
                        )
                    continue

                # -- Main-thread I/O: write files, record stats --
                result = self._materialize_result(
                    worker_out, output_dir, review_dir, fn_pattern, out_ext
                )

                if checkpoint is not None:
                    checkpoint.record(
                        img_path.name, result.status,
                        [str(p.name) for p in result.output_paths],
                        result.flags,
                    )
                    if completed % 10 == 0:
                        checkpoint.flush()

                if completed % 50 == 0 or completed == total:
                    logger.info(
                        "[%d/%d] completed (ok=%d flagged=%d failed=%d)",
                        completed, total,
                        self.stats.total_ok,
                        self.stats.total_flagged,
                        self.stats.total_failed,
                    )

                # Quality gate check
                qg = self.config.quality_gate
                if qg.enabled and completed % qg.check_interval == 0:
                    breach = self._check_quality_gate()
                    if breach and qg.action == "abort":
                        self._quality_gate_aborted = True
                        # Cancel remaining futures
                        for f in futures:
                            f.cancel()
                        raise QualityGateError(breach)

    def _materialize_result(
        self,
        wo: dict,
        output_dir: Path,
        review_dir: Path,
        fn_pattern: str,
        out_ext: str,
    ) -> ProcessingResult:
        """Convert worker output dict to ProcessingResult and write files.

        Runs on the main thread — handles file I/O and collision detection.
        """
        gc = self.config.global_config
        result = ProcessingResult(source_path=wo["source_path"])
        result.status = wo["status"]
        result.flags = wo["flags"]
        result.category = wo["category"]
        result.background_type = wo["background_type"]
        result.source_dimensions = wo["source_dimensions"]
        result.source_size_bytes = wo["source_size_bytes"]
        result.crop_metrics = wo["crop_metrics"]
        result.fallback_metrics = wo["fallback_metrics"]
        result.fallback_attempted = wo["fallback_attempted"]
        result.fallback_time_s = wo["fallback_time_s"]
        result.crop_time_s = wo["crop_time_s"]
        result.processing_time_s = wo["processing_time_s"]
        result.error_message = wo["error_message"]
        result.proposed_filenames = wo["proposed_filenames"]

        img_path = wo["source_path"]
        source_stem = img_path.stem

        # -- Write output image --
        if wo["final_image_bytes"] is not None:
            for fname in wo["proposed_filenames"]:
                if fname in self._seen_outputs:
                    if Flag.NAMING_CONFLICT not in result.flags:
                        result.flags.append(Flag.NAMING_CONFLICT)
                    logger.warning("Skipping duplicate output: %s", fname)
                    continue
                elif (output_dir / fname).exists() and not gc.overwrite:
                    if Flag.NAMING_CONFLICT not in result.flags:
                        result.flags.append(Flag.NAMING_CONFLICT)
                    logger.warning("Pre-existing conflict: %s", fname)
                    continue
                out_path = output_dir / fname
                out_path.write_bytes(wo["final_image_bytes"])
                self._seen_outputs.add(fname)
                result.output_paths.append(out_path)

        # -- Write review artifacts --
        if wo["review_original_copy"]:
            try:
                shutil.copy2(img_path, review_dir / img_path.name)
            except Exception:
                pass

        if wo["review_preview_bytes"] is not None:
            preview_path = review_dir / f"{img_path.stem}_preview.png"
            try:
                preview_path.write_bytes(wo["review_preview_bytes"])
            except Exception:
                pass

        self.stats.record(result)
        return result

    def _process_one(
        self,
        img_path: Path,
        output_dir: Path,
        review_dir: Path,
    ) -> ProcessingResult:
        """Process a single image through the full pipeline."""
        t0 = time.perf_counter()
        sku = img_path.stem

        result = ProcessingResult(source_path=img_path)

        # -- Mapping lookup --
        rows = self.mapping.lookup(sku)
        if not rows:
            result.flags.append(Flag.MISSING_MAPPING)
            result.status = ProcessingStatus.FLAGGED
            result.processing_time_s = time.perf_counter() - t0
            logger.warning("No mapping for SKU: %s", sku)
            shutil.copy2(img_path, review_dir / img_path.name)
            return result

        category = rows[0].category
        result.category = category
        gc = self.config.global_config
        fn_pattern = gc.filename_pattern
        out_ext = gc.output_format.lower().replace("jpeg", "jpg")
        source_stem = img_path.stem
        result.proposed_filenames = [
            row.output_filename_for_source(source_stem, fn_pattern, out_ext)
            for row in rows
        ]

        # -- Filename conflict check (pre-existing files + same-batch) --
        for row in rows:
            fname = row.output_filename_for_source(source_stem, fn_pattern, out_ext)
            if fname in self._seen_outputs:
                result.flags.append(Flag.NAMING_CONFLICT)
                logger.warning("Same-batch output conflict: %s", fname)
            elif (output_dir / fname).exists() and not gc.overwrite:
                result.flags.append(Flag.NAMING_CONFLICT)
                logger.warning("Pre-existing output conflict: %s", fname)

        # -- Source metadata --
        try:
            result.source_size_bytes = img_path.stat().st_size
        except OSError:
            pass

        # -- Load image --
        image = load_image(img_path)
        if image is None:
            result.flags.append(Flag.IMAGE_READ_ERROR)
            result.status = ProcessingStatus.FAILED
            result.error_message = "Failed to load image"
            result.processing_time_s = time.perf_counter() - t0
            return result

        result.source_dimensions = (image.shape[1], image.shape[0])

        context = ImageContext(
            source_path=img_path,
            mapping_rows=rows,
            category=category,
        )

        # -- Primary crop (timed separately for stats) --
        t_crop = time.perf_counter()
        crop_result = self.primary.crop(image, context, self.config)
        result.crop_time_s = time.perf_counter() - t_crop
        result.background_type = crop_result.background_type
        context.background_type = crop_result.background_type

        # -- Validate --
        validation_flags = validate_crop_result(
            crop_result, image.shape, context, self.config
        )
        # Merge: early flags (NAMING_CONFLICT) + crop flags + validation flags
        all_flags = list(dict.fromkeys(
            result.flags + crop_result.flags + validation_flags
        ))
        result.crop_metrics = crop_result.metrics

        # NAMING_CONFLICT is informational — don't block processing
        blocking_flags = [f for f in all_flags if f != Flag.NAMING_CONFLICT]
        primary_ok = len(blocking_flags) == 0 and crop_result.final_image is not None

        if primary_ok:
            # Success — save outputs
            result.status = ProcessingStatus.OK
            result.flags = all_flags
            self._save_outputs(crop_result.final_image, rows, output_dir, result, sku)

        elif self.fallback and self.config.fallback.enabled:
            # Attempt fallback — seed with classical mask and failure context
            result.fallback_attempted = True
            context.prior_mask = crop_result.mask
            context.primary_flags = blocking_flags
            context.primary_result = crop_result
            t_fb = time.perf_counter()

            fb_result = self.fallback.crop(image, context, self.config)
            result.fallback_time_s = time.perf_counter() - t_fb

            fb_validation = validate_crop_result(
                fb_result, image.shape, context, self.config,
                tolerance=self.config.fallback.validation_tolerance,
            )
            fb_all_flags = list(
                dict.fromkeys(fb_result.flags + fb_validation)
            )

            result.fallback_metrics = fb_result.metrics

            if len(fb_all_flags) == 0 and fb_result.final_image is not None:
                # Fallback recovered the image
                result.status = ProcessingStatus.RECOVERED
                result.flags = all_flags  # keep original flags for traceability
                self._save_outputs(
                    fb_result.final_image, rows, output_dir, result, sku
                )
            else:
                # Still flagged after fallback
                result.status = ProcessingStatus.FLAGGED
                combined = all_flags + [
                    f for f in fb_all_flags if f not in all_flags
                ]
                result.flags = combined
                self._save_review(img_path, image, crop_result, review_dir)
        else:
            # No fallback — flag for review
            result.status = ProcessingStatus.FLAGGED
            result.flags = all_flags
            self._save_review(img_path, image, crop_result, review_dir)

        result.processing_time_s = time.perf_counter() - t0
        return result

    def _save_outputs(
        self, final_image, rows, output_dir, result, source_stem: str = ""
    ) -> None:
        """Save the final image for each mapping row.

        Skips writing if the output filename was already written in this
        batch (prevents silent overwrite on same-batch collisions).
        """
        gc = self.config.global_config
        quality = gc.jpeg_quality
        fn_pattern = gc.filename_pattern
        out_ext = gc.output_format.lower().replace("jpeg", "jpg")
        for row in rows:
            fname = row.output_filename_for_source(source_stem, fn_pattern, out_ext)
            if fname in self._seen_outputs:
                logger.warning(
                    "Skipping write — already written in this batch: %s",
                    fname,
                )
                continue
            out_path = output_dir / fname
            save_image(final_image, out_path, quality=quality, output_format=out_ext)
            self._seen_outputs.add(fname)
            result.output_paths.append(out_path)

    # ------------------------------------------------------------------
    # Quality gate
    # ------------------------------------------------------------------

    def _check_quality_gate(self) -> str:
        """Check per-category success rates against quality gate thresholds.

        Returns an empty string if all categories pass, or a detailed
        warning message if any category breaches the threshold.
        """
        qg = self.config.quality_gate
        if not qg.enabled:
            return ""

        from collections import Counter

        cat_ok: Counter[str] = Counter()
        cat_total: Counter[str] = Counter()
        cat_flags: dict[str, Counter[str]] = {}

        for r in self.stats.results:
            cat = r.category or "UNKNOWN"
            cat_total[cat] += 1
            if r.status in (ProcessingStatus.OK, ProcessingStatus.RECOVERED):
                cat_ok[cat] += 1
            else:
                if cat not in cat_flags:
                    cat_flags[cat] = Counter()
                for f in r.flags:
                    cat_flags[cat][f.value] += 1

        breaches: list[str] = []
        for cat, total in cat_total.items():
            if total < qg.min_samples:
                continue
            ok = cat_ok[cat]
            rate = ok / total
            if rate < qg.min_success_rate:
                top_flags = ""
                if cat in cat_flags:
                    top_3 = cat_flags[cat].most_common(3)
                    top_flags = ", ".join(f"{name} ({cnt})" for name, cnt in top_3)
                breaches.append(
                    f"  {cat}: {total - ok}/{total} flagged "
                    f"({rate:.0%} success, threshold: {qg.min_success_rate:.0%})\n"
                    f"    Top flags: {top_flags}"
                )

        if not breaches:
            return ""

        message = (
            "Quality gate triggered!\n"
            + "\n".join(breaches)
            + "\n  Suggestion: check category rules in rules.yaml"
        )
        logger.warning("WARNING: %s", message)
        return message

    # ------------------------------------------------------------------
    # Sequential-mode methods
    # ------------------------------------------------------------------

    def _save_review(
        self, img_path, original_image, crop_result, review_dir
    ) -> None:
        """Copy original to review dir and generate preview if possible."""
        shutil.copy2(img_path, review_dir / img_path.name)
        try:
            rgb = (
                original_image[:, :, :3]
                if original_image.ndim == 3 and original_image.shape[2] >= 3
                else original_image
            )
            generate_side_by_side(
                rgb,
                crop_result.mask,
                crop_result.cropped_image,
                crop_result.final_image,
                review_dir / f"{img_path.stem}_preview.png",
            )
        except Exception as e:
            logger.debug(
                "Failed to generate preview for %s: %s", img_path, e
            )
