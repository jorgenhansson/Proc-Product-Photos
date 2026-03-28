"""Tests for incremental mode (#38)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.config import (
    FallbackConfig,
    GlobalConfig,
    PipelineConfig,
    QualityGateConfig,
)
from process_images.crop.classical import ClassicalCropStrategy
from process_images.mapping import MappingLookup
from process_images.models import MappingRow, ProcessingStatus
from process_images.pipeline import Pipeline


def _save_test_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[60:140, 60:140] = [40, 40, 40]
    Image.fromarray(img).save(path, format="PNG")


def _make_mapping(*entries) -> MappingLookup:
    rows_by_sku: dict[str, list[MappingRow]] = {}
    for sku, article, suffix, cat in entries:
        row = MappingRow(
            supplier_sku=sku, store_article=article,
            suffix=suffix, category=cat,
        )
        rows_by_sku.setdefault(sku.lower(), []).append(row)
    return MappingLookup(rows_by_sku=rows_by_sku)


def _config() -> PipelineConfig:
    return PipelineConfig(
        global_config=GlobalConfig(canvas_size=200),
        fallback=FallbackConfig(enabled=False),
        quality_gate=QualityGateConfig(enabled=False),
    )


@pytest.fixture
def dirs(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    review_dir = tmp_path / "review"
    input_dir.mkdir()
    return input_dir, output_dir, review_dir


class TestIncrementalBasic:
    def test_first_run_processes_all(self, dirs):
        """Without existing output, incremental processes everything."""
        input_dir, output_dir, review_dir = dirs
        for i in range(3):
            _save_test_image(input_dir / f"IMG{i:03d}.png")

        mapping = _make_mapping(
            *[(f"IMG{i:03d}", f"A{i}", "front", "BALL") for i in range(3)]
        )
        pipeline = Pipeline(_config(), mapping, ClassicalCropStrategy())
        stats = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
        )
        assert stats.total_attempted == 3

    def test_second_run_skips_all(self, dirs):
        """After a full run, incremental skips everything."""
        input_dir, output_dir, review_dir = dirs
        for i in range(3):
            _save_test_image(input_dir / f"IMG{i:03d}.png")

        mapping = _make_mapping(
            *[(f"IMG{i:03d}", f"A{i}", "front", "BALL") for i in range(3)]
        )
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run
        pipeline.run(input_dir, output_dir, review_dir)

        # Second run incremental
        stats2 = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
        )
        assert stats2.total_attempted == 0

    def test_new_image_processed(self, dirs):
        """Incremental processes new images not yet in output."""
        input_dir, output_dir, review_dir = dirs
        _save_test_image(input_dir / "IMG000.png")
        _save_test_image(input_dir / "IMG001.png")

        mapping = _make_mapping(
            ("IMG000", "A0", "front", "BALL"),
            ("IMG001", "A1", "front", "BALL"),
            ("IMG002", "A2", "front", "BALL"),
        )
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run: 2 images
        pipeline.run(input_dir, output_dir, review_dir)

        # Add third image
        _save_test_image(input_dir / "IMG002.png")

        # Incremental: only IMG002
        stats2 = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
        )
        assert stats2.total_attempted == 1


class TestIncrementalMtime:
    def test_touched_input_reprocessed(self, dirs):
        """If input is newer than output, reprocess."""
        input_dir, output_dir, review_dir = dirs
        _save_test_image(input_dir / "IMG000.png")

        mapping = _make_mapping(("IMG000", "A0", "front", "BALL"))
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run
        pipeline.run(input_dir, output_dir, review_dir)

        # Touch input file to make it newer
        time.sleep(0.05)
        _save_test_image(input_dir / "IMG000.png")

        # Incremental: should reprocess
        stats2 = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
        )
        assert stats2.total_attempted == 1

    def test_config_change_reprocesses(self, dirs):
        """If reference mtime (rules/mapping) is newer than output, reprocess."""
        input_dir, output_dir, review_dir = dirs
        _save_test_image(input_dir / "IMG000.png")

        mapping = _make_mapping(("IMG000", "A0", "front", "BALL"))
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run
        pipeline.run(input_dir, output_dir, review_dir)

        # Simulate rules change: reference mtime in the future
        future_mtime = time.time() + 10

        stats2 = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
            reference_mtimes=[future_mtime],
        )
        assert stats2.total_attempted == 1


class TestIncrementalForceCategory:
    def test_force_category_reprocesses(self, dirs):
        """--force-category BAG reprocesses all BAG images regardless."""
        input_dir, output_dir, review_dir = dirs
        _save_test_image(input_dir / "BALL1.png")
        _save_test_image(input_dir / "BAG1.png")

        mapping = _make_mapping(
            ("BALL1", "A0", "front", "BALL"),
            ("BAG1", "A1", "front", "BAG"),
        )
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run
        pipeline.run(input_dir, output_dir, review_dir)

        # Incremental with force BAG: BAG reprocessed, BALL skipped
        stats2 = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
            force_categories={"BAG"},
        )
        assert stats2.total_attempted == 1
        assert stats2.results[0].category == "BAG"

    def test_force_category_case_insensitive(self, dirs):
        """Force category matching should be case-insensitive."""
        input_dir, output_dir, review_dir = dirs
        _save_test_image(input_dir / "BALL1.png")
        _save_test_image(input_dir / "BALL2.png")

        mapping = _make_mapping(
            ("BALL1", "A0", "front", "BALL"),
            ("BALL2", "A1", "front", "BALL"),
        )
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run — both should produce output
        stats1 = pipeline.run(input_dir, output_dir, review_dir)
        ok_count = sum(
            1 for r in stats1.results
            if r.status in (ProcessingStatus.OK, ProcessingStatus.RECOVERED)
        )
        assert ok_count == 2, f"First run should succeed, got {ok_count}/2"

        # Incremental with lowercase force — should reprocess both
        stats2 = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
            force_categories={"ball"},  # lowercase — must still match BALL
        )
        assert stats2.total_attempted == 2


class TestIncrementalWithMissingMapping:
    def test_unmapped_image_always_processed(self, dirs):
        """Images with no mapping must always be processed (to flag them)."""
        input_dir, output_dir, review_dir = dirs
        _save_test_image(input_dir / "UNKNOWN.png")

        mapping = _make_mapping()  # empty
        config = _config()
        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # Incremental: should still process (no output to skip)
        stats = pipeline.run(
            input_dir, output_dir, review_dir,
            incremental=True,
        )
        assert stats.total_attempted == 1
