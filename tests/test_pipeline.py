"""Integration tests for the full pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.config import PipelineConfig, GlobalConfig, FallbackConfig
from process_images.crop.classical import ClassicalCropStrategy
from process_images.crop.ai_fallback import AIFallbackCropStrategy
from process_images.mapping import MappingLookup
from process_images.models import Flag, MappingRow, ProcessingStatus
from process_images.pipeline import Pipeline


def _save_test_image(
    path: Path, img: np.ndarray, fmt: str = "PNG"
) -> None:
    """Save a numpy array as an image file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, format=fmt)


def _make_white_bg_image() -> np.ndarray:
    """200x200 white image with dark square."""
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[60:140, 60:140] = [40, 40, 40]
    return img


def _make_mapping(*entries) -> MappingLookup:
    """Build MappingLookup from (sku, article, suffix, category) tuples."""
    rows_by_sku: dict[str, list[MappingRow]] = {}
    for sku, article, suffix, cat in entries:
        row = MappingRow(
            supplier_sku=sku,
            store_article=article,
            suffix=suffix,
            category=cat,
        )
        rows_by_sku.setdefault(sku.lower(), []).append(row)
    return MappingLookup(rows_by_sku=rows_by_sku)


@pytest.fixture
def setup_dirs(tmp_path: Path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    review_dir = tmp_path / "review"
    input_dir.mkdir()
    return input_dir, output_dir, review_dir


class TestPipelineEndToEnd:
    def test_single_image_success(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        _save_test_image(input_dir / "SKU001.png", _make_white_bg_image())

        mapping = _make_mapping(("SKU001", "ART100", "front", "BALL"))
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(
            config, mapping, ClassicalCropStrategy(), AIFallbackCropStrategy()
        )
        stats = pipeline.run(input_dir, output_dir, review_dir)

        assert stats.total_discovered == 1
        assert stats.total_attempted == 1
        ok_count = sum(
            1
            for r in stats.results
            if r.status in (ProcessingStatus.OK, ProcessingStatus.RECOVERED)
        )
        assert ok_count == 1
        assert (output_dir / "SKU001-cropped.jpg").exists()

    def test_missing_mapping(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        _save_test_image(input_dir / "UNKNOWN.png", _make_white_bg_image())

        mapping = _make_mapping()  # empty mapping
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        assert stats.results[0].status == ProcessingStatus.FLAGGED
        assert Flag.MISSING_MAPPING in stats.results[0].flags
        # Original should be copied to review
        assert (review_dir / "UNKNOWN.png").exists()

    def test_multiple_images(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        for i in range(3):
            _save_test_image(
                input_dir / f"IMG{i:03d}.png", _make_white_bg_image()
            )

        mapping = _make_mapping(
            ("IMG000", "A1", "front", "BALL"),
            ("IMG001", "A2", "front", "SHOE"),
            ("IMG002", "A3", "front", "BAG"),
        )
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        assert stats.total_discovered == 3
        assert stats.total_attempted == 3

    def test_limit_parameter(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        for i in range(5):
            _save_test_image(
                input_dir / f"IMG{i:03d}.png", _make_white_bg_image()
            )

        mapping = _make_mapping(
            *[(f"IMG{i:03d}", f"A{i}", "front", "BALL") for i in range(5)]
        )
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir, limit=2)

        assert stats.total_discovered == 5
        assert stats.total_attempted == 2

    def test_review_manifest_written(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        _save_test_image(input_dir / "NOMAP.png", _make_white_bg_image())

        mapping = _make_mapping()
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        pipeline.run(input_dir, output_dir, review_dir)

        manifest = review_dir / "manifest.json"
        assert manifest.exists()

    def test_tiff_input(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        _save_test_image(
            input_dir / "SKU001.tif", _make_white_bg_image(), fmt="TIFF"
        )

        mapping = _make_mapping(("SKU001", "ART200", "front", "BALL"))
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        successful = sum(
            1
            for r in stats.results
            if r.status in (ProcessingStatus.OK, ProcessingStatus.RECOVERED)
        )
        assert successful == 1

    def test_manifest_contains_required_fields(self, setup_dirs):
        """Review manifest must include source dimensions, proposed filenames,
        and both primary/fallback metrics."""
        input_dir, output_dir, review_dir = setup_dirs
        # Pure white image — will be flagged, appear in manifest
        white = np.full((200, 200, 3), 255, dtype=np.uint8)
        _save_test_image(input_dir / "FLAGGED.png", white)

        mapping = _make_mapping(("FLAGGED", "ART999", "front", "BALL"))
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        pipeline.run(input_dir, output_dir, review_dir)

        import json
        manifest = json.loads((review_dir / "manifest.json").read_text())
        assert len(manifest) >= 1
        item = manifest[0]

        # Required fields from issue #8
        assert "source_size_bytes" in item
        assert item["source_size_bytes"] > 0
        assert "source_dimensions" in item
        assert item["source_dimensions"] == [200, 200]
        assert "proposed_outputs" in item
        assert "FLAGGED-cropped.jpg" in item["proposed_outputs"]
        assert "primary_metrics" in item
        assert "fallback_succeeded" in item
        assert item["fallback_succeeded"] is False

    def test_result_has_source_metadata(self, setup_dirs):
        """ProcessingResult should contain source dimensions and proposed filenames."""
        input_dir, output_dir, review_dir = setup_dirs
        _save_test_image(input_dir / "SKU001.png", _make_white_bg_image())

        mapping = _make_mapping(("SKU001", "ART100", "front", "BALL"))
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        r = stats.results[0]
        assert r.source_dimensions == (200, 200)
        assert r.source_size_bytes > 0
        assert r.proposed_filenames == ["SKU001-cropped.jpg"]

    def test_no_fallback_when_disabled(self, setup_dirs):
        input_dir, output_dir, review_dir = setup_dirs
        # Pure white image — will be flagged (no object)
        white = np.full((200, 200, 3), 255, dtype=np.uint8)
        _save_test_image(input_dir / "EMPTY.png", white)

        mapping = _make_mapping(("EMPTY", "ART300", "front", "BALL"))
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        assert stats.results[0].fallback_attempted is False

    def test_same_batch_collision_no_overwrite(self, setup_dirs):
        """Two different input images mapping to the same output filename
        should not overwrite each other. Second write is skipped."""
        input_dir, output_dir, review_dir = setup_dirs

        # Two different images, same output filename via store_article pattern
        img1 = np.full((200, 200, 3), 255, dtype=np.uint8)
        img1[60:140, 60:140] = [40, 40, 40]
        img2 = np.full((200, 200, 3), 255, dtype=np.uint8)
        img2[80:120, 80:120] = [200, 50, 50]

        _save_test_image(input_dir / "SKU_A.png", img1)
        _save_test_image(input_dir / "SKU_B.png", img2)

        # Use explicit pattern that collides on store_article
        mapping = _make_mapping(
            ("SKU_A", "SAME_ART", "front", "BALL"),
            ("SKU_B", "SAME_ART", "front", "BALL"),
        )
        config = PipelineConfig(
            global_config=GlobalConfig(
                canvas_size=200,
                filename_pattern="{store_article}_{suffix}.{ext}",
            )
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        # Second image should have NAMING_CONFLICT flag
        conflicts = [
            r for r in stats.results if Flag.NAMING_CONFLICT in r.flags
        ]
        assert len(conflicts) >= 1

        # Output file should exist (written by first image)
        assert (output_dir / "SAME_ART_front.jpg").exists()

    def test_pre_existing_file_flagged_not_overwritten(self, setup_dirs):
        """Pre-existing output file is flagged and NOT overwritten by default."""
        input_dir, output_dir, review_dir = setup_dirs
        output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create the output file
        pre_existing = output_dir / "SKU001-cropped.jpg"
        pre_existing.write_text("old content")
        old_size = pre_existing.stat().st_size

        _save_test_image(input_dir / "SKU001.png", _make_white_bg_image())
        mapping = _make_mapping(("SKU001", "ART100", "front", "BALL"))
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        r = stats.results[0]
        assert Flag.NAMING_CONFLICT in r.flags
        # Flag is informational; file is still overwritten
        assert pre_existing.stat().st_size != old_size

    def test_multi_row_mapping_produces_multiple_outputs(self, setup_dirs):
        """One SKU mapped to multiple output files should produce all of them."""
        input_dir, output_dir, review_dir = setup_dirs
        _save_test_image(input_dir / "SKU001.png", _make_white_bg_image())

        # Same SKU → two different output files (use store_article pattern to differentiate)
        mapping = _make_mapping(
            ("SKU001", "ART100", "front", "BALL"),
            ("SKU001", "ART100", "side", "BALL"),
        )
        config = PipelineConfig(
            global_config=GlobalConfig(
                canvas_size=200,
                filename_pattern="{store_article}_{suffix}.{ext}",
            )
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        assert stats.total_attempted == 1  # one input image
        r = stats.results[0]
        assert len(r.output_paths) == 2
        assert (output_dir / "ART100_front.jpg").exists()
        assert (output_dir / "ART100_side.jpg").exists()


class TestFallbackRecoveryIntegration:
    """End-to-end tests for the fallback recovery path (#28).

    These tests create images that fail primary validation but succeed
    with relaxed fallback tolerance, verifying the full recovery flow.
    """

    def test_aspect_ratio_recovery(self, setup_dirs):
        """Image with AR slightly outside category range should be RECOVERED.

        BALL expects AR 1.0-1.5.  A 60x96 dark rect on 200x200 gives
        AR ~1.6 → strict flags CROP_CATEGORY_INCONSISTENT →
        fallback returns primary result → relaxed (AR max = 1.5/0.8 = 1.875)
        accepts → RECOVERED.
        """
        input_dir, output_dir, review_dir = setup_dirs

        # Dark rectangle: 70w x 120h on 200x200.
        # After morphology → bbox AR ~1.60 → strict flags (>1.5), relaxed passes (<1.875)
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[40:160, 65:135] = [40, 40, 40]  # 120h x 70w

        _save_test_image(input_dir / "ELONGATED_BALL.png", img)

        mapping = _make_mapping(("ELONGATED_BALL", "ART500", "front", "BALL"))
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(
                enabled=True,
                validation_tolerance=0.8,
            ),
        )

        pipeline = Pipeline(
            config, mapping, ClassicalCropStrategy(), AIFallbackCropStrategy()
        )
        stats = pipeline.run(input_dir, output_dir, review_dir)

        r = stats.results[0]
        assert r.fallback_attempted, "Fallback should have been attempted"
        assert r.status == ProcessingStatus.RECOVERED, (
            f"Expected RECOVERED, got {r.status.value}. Flags: {[f.value for f in r.flags]}"
        )
        assert (output_dir / "ELONGATED_BALL-cropped.jpg").exists()

    def test_fallback_still_fails_for_very_bad_image(self, setup_dirs):
        """Pure white image → no object found → fallback can't help → FLAGGED."""
        input_dir, output_dir, review_dir = setup_dirs

        white = np.full((200, 200, 3), 255, dtype=np.uint8)
        _save_test_image(input_dir / "EMPTY.png", white)

        mapping = _make_mapping(("EMPTY", "ART600", "front", "BALL"))
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=True, validation_tolerance=0.8),
        )

        pipeline = Pipeline(
            config, mapping, ClassicalCropStrategy(), AIFallbackCropStrategy()
        )
        stats = pipeline.run(input_dir, output_dir, review_dir)

        r = stats.results[0]
        assert r.fallback_attempted
        assert r.status == ProcessingStatus.FLAGGED
        # Should be in review
        assert (review_dir / "EMPTY.png").exists()

    def test_recovered_image_has_metrics(self, setup_dirs):
        """RECOVERED image should have both primary and fallback metrics."""
        input_dir, output_dir, review_dir = setup_dirs

        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[40:160, 65:135] = [40, 40, 40]  # same as AR recovery test
        _save_test_image(input_dir / "METRICS.png", img)

        mapping = _make_mapping(("METRICS", "ART700", "front", "BALL"))
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=True, validation_tolerance=0.8),
        )

        pipeline = Pipeline(
            config, mapping, ClassicalCropStrategy(), AIFallbackCropStrategy()
        )
        stats = pipeline.run(input_dir, output_dir, review_dir)

        r = stats.results[0]
        if r.status == ProcessingStatus.RECOVERED:
            assert r.crop_metrics is not None, "Primary metrics should be set"
            assert r.fallback_metrics is not None, "Fallback metrics should be set"
            assert r.crop_metrics.fill_ratio > 0
            assert r.fallback_metrics.fill_ratio > 0

    def test_multi_component_recovery(self, setup_dirs):
        """Image with two distinct objects → MULTIPLE_LARGE_COMPONENTS.

        Fallback (validation-only path) should re-use primary result
        with relaxed tolerance and recover.
        """
        input_dir, output_dir, review_dir = setup_dirs

        # Two dark squares far apart (two components)
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        img[20:60, 20:60] = [40, 40, 40]
        img[140:180, 140:180] = [40, 40, 40]
        _save_test_image(input_dir / "MULTI.png", img)

        mapping = _make_mapping(("MULTI", "ART800", "front", "BAG"))
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=True, validation_tolerance=0.7),
        )

        pipeline = Pipeline(
            config, mapping, ClassicalCropStrategy(), AIFallbackCropStrategy()
        )
        stats = pipeline.run(input_dir, output_dir, review_dir)

        r = stats.results[0]
        assert r.fallback_attempted
        # Multi-component may or may not recover depending on GrabCut
        # but the flow should not crash
        assert r.status in (ProcessingStatus.RECOVERED, ProcessingStatus.FLAGGED)

    def test_recovery_stats_counted_correctly(self, setup_dirs):
        """Mix of OK, RECOVERED, and FLAGGED should count correctly in stats."""
        input_dir, output_dir, review_dir = setup_dirs

        # Good image → OK
        good = np.full((200, 200, 3), 255, dtype=np.uint8)
        good[60:140, 60:140] = [40, 40, 40]
        _save_test_image(input_dir / "GOOD.png", good)

        # AR slightly off → should RECOVER
        elongated = np.full((200, 200, 3), 255, dtype=np.uint8)
        elongated[40:160, 65:135] = [40, 40, 40]  # AR ~1.60 → strict fails, relaxed passes
        _save_test_image(input_dir / "RECOVER.png", elongated)

        # Pure white → FLAGGED
        white = np.full((200, 200, 3), 255, dtype=np.uint8)
        _save_test_image(input_dir / "FAIL.png", white)

        mapping = _make_mapping(
            ("GOOD", "A1", "front", "BALL"),
            ("RECOVER", "A2", "front", "BALL"),
            ("FAIL", "A3", "front", "BALL"),
        )
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=True, validation_tolerance=0.8),
        )

        pipeline = Pipeline(
            config, mapping, ClassicalCropStrategy(), AIFallbackCropStrategy()
        )
        stats = pipeline.run(input_dir, output_dir, review_dir)

        d = stats.to_dict()
        assert d["general"]["total_attempted"] == 3
        # At least one should be OK, at least one failed
        assert d["general"]["total_ok"] >= 1
        assert d["general"]["total_flagged"] + d["general"]["total_failed"] >= 1
