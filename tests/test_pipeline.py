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
        rows_by_sku.setdefault(sku, []).append(row)
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
        assert (output_dir / "ART100_front.jpg").exists()

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

        mapping = _make_mapping(("SKU001", "ART200", "front", "CLUB_LONG"))
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)

        successful = sum(
            1
            for r in stats.results
            if r.status in (ProcessingStatus.OK, ProcessingStatus.RECOVERED)
        )
        assert successful == 1

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
