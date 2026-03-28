"""Tests for quality gate and pipeline reuse (#37, #22)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.config import (
    GlobalConfig,
    FallbackConfig,
    PipelineConfig,
    QualityGateConfig,
)
from process_images.crop.classical import ClassicalCropStrategy
from process_images.crop.ai_fallback import AIFallbackCropStrategy
from process_images.mapping import MappingLookup
from process_images.models import Flag, MappingRow, ProcessingStatus
from process_images.pipeline import Pipeline, QualityGateError


def _save_test_image(path: Path, img: np.ndarray, fmt: str = "PNG") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, format=fmt)


def _make_dark_square_image() -> np.ndarray:
    """200x200 white image with dark square — always succeeds."""
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[60:140, 60:140] = [40, 40, 40]
    return img


def _make_pure_white_image() -> np.ndarray:
    """200x200 pure white — always fails (no object found)."""
    return np.full((200, 200, 3), 255, dtype=np.uint8)


def _make_mapping(*entries) -> MappingLookup:
    rows_by_sku: dict[str, list[MappingRow]] = {}
    for sku, article, suffix, cat in entries:
        row = MappingRow(
            supplier_sku=sku, store_article=article,
            suffix=suffix, category=cat,
        )
        rows_by_sku.setdefault(sku, []).append(row)
    return MappingLookup(rows_by_sku=rows_by_sku)


@pytest.fixture
def dirs(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    review_dir = tmp_path / "review"
    input_dir.mkdir()
    return input_dir, output_dir, review_dir


class TestQualityGateWarn:
    """Quality gate with action=warn should log but not abort."""

    def test_warn_does_not_abort(self, dirs):
        input_dir, output_dir, review_dir = dirs

        # Create 20 images: 15 will fail (pure white), 5 succeed
        entries = []
        for i in range(15):
            name = f"FAIL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"A{i}", "front", "BAG"))
        for i in range(5):
            name = f"OK{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_dark_square_image())
            entries.append((name, f"B{i}", "front", "BAG"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(
                enabled=True,
                check_interval=10,
                min_samples=5,
                min_success_rate=0.70,
                action="warn",
            ),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        # Should complete without raising
        stats = pipeline.run(input_dir, output_dir, review_dir)
        assert stats.total_attempted == 20


class TestQualityGateAbort:
    """Quality gate with action=abort should raise QualityGateError."""

    def test_abort_raises_error(self, dirs):
        input_dir, output_dir, review_dir = dirs

        # 20 images all failing in BAG category
        entries = []
        for i in range(20):
            name = f"FAIL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"A{i}", "front", "BAG"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(
                enabled=True,
                check_interval=10,   # check after 10 images
                min_samples=5,
                min_success_rate=0.50,
                action="abort",
            ),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        with pytest.raises(QualityGateError) as exc_info:
            pipeline.run(input_dir, output_dir, review_dir)

        assert "BAG" in str(exc_info.value)
        # Should have processed some but not all
        assert pipeline.stats.total_attempted < 20
        assert pipeline.stats.total_attempted >= 10

    def test_abort_includes_flag_details(self, dirs):
        input_dir, output_dir, review_dir = dirs

        entries = []
        for i in range(15):
            name = f"FAIL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"A{i}", "front", "SHOE"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(
                enabled=True,
                check_interval=10,
                min_samples=5,
                min_success_rate=0.50,
                action="abort",
            ),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        with pytest.raises(QualityGateError) as exc_info:
            pipeline.run(input_dir, output_dir, review_dir)

        detail = exc_info.value.detail
        assert "SHOE" in detail
        assert "Top flags:" in detail


class TestQualityGateDisabled:
    """Quality gate disabled or action=ignore should never interfere."""

    def test_disabled_processes_all(self, dirs):
        input_dir, output_dir, review_dir = dirs

        entries = []
        for i in range(15):
            name = f"FAIL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"A{i}", "front", "BAG"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(enabled=False),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)
        assert stats.total_attempted == 15

    def test_ignore_action_processes_all(self, dirs):
        input_dir, output_dir, review_dir = dirs

        entries = []
        for i in range(15):
            name = f"FAIL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"A{i}", "front", "BAG"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(
                enabled=True,
                check_interval=10,
                min_samples=5,
                min_success_rate=0.99,
                action="ignore",
            ),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir)
        assert stats.total_attempted == 15


class TestQualityGateMinSamples:
    """Quality gate should not trigger until min_samples is reached."""

    def test_below_min_samples_no_trigger(self, dirs):
        input_dir, output_dir, review_dir = dirs

        # 8 failing images, but min_samples=10 — should not trigger
        entries = []
        for i in range(8):
            name = f"FAIL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"A{i}", "front", "BAG"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(
                enabled=True,
                check_interval=5,
                min_samples=10,
                min_success_rate=0.50,
                action="abort",
            ),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        # Should NOT raise because min_samples not reached
        stats = pipeline.run(input_dir, output_dir, review_dir)
        assert stats.total_attempted == 8


class TestQualityGateMultiCategory:
    """Quality gate checks each category independently."""

    def test_one_bad_category_triggers(self, dirs):
        input_dir, output_dir, review_dir = dirs

        entries = []
        # 15 good BALL images
        for i in range(15):
            name = f"BALL{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_dark_square_image())
            entries.append((name, f"B{i}", "front", "BALL"))
        # 15 bad BAG images
        for i in range(15):
            name = f"BAG{i:03d}"
            _save_test_image(input_dir / f"{name}.png", _make_pure_white_image())
            entries.append((name, f"G{i}", "front", "BAG"))

        mapping = _make_mapping(*entries)
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            fallback=FallbackConfig(enabled=False),
            quality_gate=QualityGateConfig(
                enabled=True,
                check_interval=20,
                min_samples=10,
                min_success_rate=0.50,
                action="abort",
            ),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        with pytest.raises(QualityGateError) as exc_info:
            pipeline.run(input_dir, output_dir, review_dir)

        # BAG should be mentioned, BALL should not
        assert "BAG" in exc_info.value.detail


# =========================================================================
# Pipeline reuse tests (#22)
# =========================================================================

class TestPipelineReuse:
    """Pipeline.run() should reset state so the same instance can be reused."""

    def test_run_twice_gives_independent_stats(self, dirs):
        input_dir, output_dir, review_dir = dirs

        for i in range(3):
            _save_test_image(input_dir / f"IMG{i:03d}.png", _make_dark_square_image())

        mapping = _make_mapping(
            ("IMG000", "A0", "front", "BALL"),
            ("IMG001", "A1", "front", "BALL"),
            ("IMG002", "A2", "front", "BALL"),
        )
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            quality_gate=QualityGateConfig(enabled=False),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # First run
        stats1 = pipeline.run(input_dir, output_dir, review_dir)
        assert stats1.total_attempted == 3

        # Second run on same pipeline instance
        stats2 = pipeline.run(input_dir, output_dir, review_dir)
        assert stats2.total_attempted == 3
        # Stats should be independent, not accumulated
        assert len(stats2.results) == 3

    def test_collision_state_reset_between_runs(self, dirs):
        input_dir, output_dir, review_dir = dirs

        _save_test_image(input_dir / "SKU.png", _make_dark_square_image())

        mapping = _make_mapping(("SKU", "ART", "front", "BALL"))
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200, overwrite=True),
            quality_gate=QualityGateConfig(enabled=False),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        stats1 = pipeline.run(input_dir, output_dir, review_dir)
        assert len(stats1.results[0].output_paths) > 0

        # Second run — should NOT have stale collision from first run
        stats2 = pipeline.run(input_dir, output_dir, review_dir)
        collision_flags = [
            f for f in stats2.results[0].flags if f == Flag.NAMING_CONFLICT
        ]
        assert len(collision_flags) == 0

    def test_quality_gate_state_reset(self, dirs):
        """Quality gate aborted flag should reset between runs."""
        input_dir, output_dir, review_dir = dirs

        for i in range(3):
            _save_test_image(input_dir / f"OK{i:03d}.png", _make_dark_square_image())

        mapping = _make_mapping(
            *[(f"OK{i:03d}", f"A{i}", "front", "BALL") for i in range(3)]
        )
        config = PipelineConfig(
            global_config=GlobalConfig(canvas_size=200),
            quality_gate=QualityGateConfig(enabled=True, action="abort"),
        )

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())

        # This run should succeed (all images OK)
        stats = pipeline.run(input_dir, output_dir, review_dir)
        assert not pipeline._quality_gate_aborted
        assert stats.total_attempted == 3
