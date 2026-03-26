"""Main pipeline orchestrator: discovers, processes, and routes images."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from .config import PipelineConfig
from .crop.base import CropStrategy
from .io_utils import discover_images, load_image, save_jpeg
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


class Pipeline:
    """Orchestrates the full image processing pipeline.

    Runs the primary (classical) crop strategy on every image, validates
    the result, optionally invokes a fallback strategy for flagged images,
    and writes outputs, review artifacts, and statistics.
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
        self.stats = StatsAccumulator()

    def run(
        self,
        input_dir: Path,
        output_dir: Path,
        review_dir: Path,
        limit: Optional[int] = None,
    ) -> StatsAccumulator:
        """Discover and process all images in input_dir.

        Args:
            input_dir: Directory containing supplier images.
            output_dir: Directory for successfully processed images.
            review_dir: Directory for flagged/failed images and review data.
            limit: If set, process only the first N images.

        Returns:
            StatsAccumulator with all results.
        """
        images = discover_images(input_dir)
        self.stats.total_discovered = len(images)
        logger.info("Discovered %d images in %s", len(images), input_dir)

        if limit is not None and limit > 0:
            images = images[:limit]
            logger.info("Limiting to first %d images", limit)

        output_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)

        for i, img_path in enumerate(images, 1):
            logger.info(
                "[%d/%d] Processing %s", i, len(images), img_path.name
            )
            result = self._process_one(img_path, output_dir, review_dir)
            self.stats.record(result)

        # Write review manifest
        write_review_manifest(self.stats.results, review_dir / "manifest.json")

        return self.stats

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
        result.proposed_filenames = [row.output_filename for row in rows]

        # -- Filename conflict check --
        for row in rows:
            out_path = output_dir / row.output_filename
            if out_path.exists():
                result.flags.append(Flag.NAMING_CONFLICT)
                logger.warning("Output conflict: %s", out_path)

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
        all_flags = list(dict.fromkeys(crop_result.flags + validation_flags))
        result.crop_metrics = crop_result.metrics

        primary_ok = len(all_flags) == 0 and crop_result.final_image is not None

        if primary_ok:
            # Success — save outputs
            result.status = ProcessingStatus.OK
            result.flags = all_flags
            self._save_outputs(crop_result.final_image, rows, output_dir, result)

        elif self.fallback and self.config.fallback.enabled:
            # Attempt fallback — seed with classical mask for better results
            result.fallback_attempted = True
            context.prior_mask = crop_result.mask
            t_fb = time.perf_counter()

            fb_result = self.fallback.crop(image, context, self.config)
            result.fallback_time_s = time.perf_counter() - t_fb

            fb_validation = validate_crop_result(
                fb_result, image.shape, context, self.config
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
                    fb_result.final_image, rows, output_dir, result
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
        self, final_image, rows, output_dir, result
    ) -> None:
        """Save the final image for each mapping row."""
        quality = self.config.global_config.jpeg_quality
        for row in rows:
            out_path = output_dir / row.output_filename
            save_jpeg(final_image, out_path, quality)
            result.output_paths.append(out_path)

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
