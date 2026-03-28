"""Tests for checkpoint/resume support."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.checkpoint import (
    Checkpoint,
    hash_file,
    load_checkpoint,
    new_checkpoint,
)
from process_images.config import GlobalConfig, PipelineConfig
from process_images.crop.ai_fallback import AIFallbackCropStrategy
from process_images.crop.classical import ClassicalCropStrategy
from process_images.mapping import MappingLookup
from process_images.models import Flag, MappingRow, ProcessingStatus
from process_images.pipeline import Pipeline


def _save_test_image(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(path, format="PNG")


def _make_dark_square_image() -> np.ndarray:
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[60:140, 60:140] = [40, 40, 40]
    return img


def _make_mapping(*entries) -> MappingLookup:
    rows_by_sku: dict[str, list[MappingRow]] = {}
    for sku, article, suffix, cat in entries:
        row = MappingRow(
            supplier_sku=sku, store_article=article,
            suffix=suffix, category=cat,
        )
        rows_by_sku.setdefault(sku.lower(), []).append(row)
    return MappingLookup(rows_by_sku=rows_by_sku)


class TestCheckpointBasics:
    def test_new_checkpoint_empty(self, tmp_path):
        cp = new_checkpoint(tmp_path / "cp.json", "abc123")
        assert cp.skip_count == 0
        assert not cp.is_done("test.png")

    def test_record_and_check(self, tmp_path):
        cp = new_checkpoint(tmp_path / "cp.json", "abc123")
        cp.record("img1.png", ProcessingStatus.OK, ["img1-cropped.jpg"], [])
        assert cp.is_done("img1.png")
        assert not cp.is_done("img2.png")
        assert cp.skip_count == 1

    def test_flush_creates_file(self, tmp_path):
        cp = new_checkpoint(tmp_path / "cp.json", "abc123")
        cp.record("img1.png", ProcessingStatus.OK, ["out.jpg"], [])
        cp.flush()

        assert (tmp_path / "cp.json").exists()
        data = json.loads((tmp_path / "cp.json").read_text())
        assert data["total_completed"] == 1
        assert "img1.png" in data["completed"]
        assert data["completed"]["img1.png"]["status"] == "ok"

    def test_flush_atomic_no_partial(self, tmp_path):
        """Flush uses atomic rename — no .tmp file left behind."""
        cp = new_checkpoint(tmp_path / "cp.json", "abc123")
        cp.record("img1.png", ProcessingStatus.OK, [], [])
        cp.flush()
        assert not (tmp_path / "cp.json.tmp").exists()

    def test_record_with_flags(self, tmp_path):
        cp = new_checkpoint(tmp_path / "cp.json", "abc123")
        cp.record(
            "bad.png", ProcessingStatus.FLAGGED, [],
            [Flag.MASK_TOO_SMALL, Flag.NO_OBJECT_FOUND],
        )
        cp.flush()

        data = json.loads((tmp_path / "cp.json").read_text())
        entry = data["completed"]["bad.png"]
        assert entry["status"] == "flagged"
        assert "mask_too_small" in entry["flags"]
        assert "no_object_found" in entry["flags"]


class TestCheckpointLoad:
    def test_load_existing(self, tmp_path):
        # Create a checkpoint file
        cp = new_checkpoint(tmp_path / "cp.json", "hash1")
        cp.record("a.png", ProcessingStatus.OK, ["a-cropped.jpg"], [])
        cp.record("b.png", ProcessingStatus.FLAGGED, [], [Flag.BBOX_TOO_LARGE])
        cp.flush()

        # Load it back
        loaded = load_checkpoint(tmp_path / "cp.json", "hash1")
        assert loaded.is_done("a.png")
        assert loaded.is_done("b.png")
        assert not loaded.is_done("c.png")
        assert loaded.skip_count == 2

    def test_load_mismatched_hash_raises(self, tmp_path):
        cp = new_checkpoint(tmp_path / "cp.json", "hash_old")
        cp.record("a.png", ProcessingStatus.OK, [], [])
        cp.flush()

        with pytest.raises(ValueError, match="Rules YAML changed"):
            load_checkpoint(tmp_path / "cp.json", "hash_new")

    def test_load_mismatched_hash_with_force(self, tmp_path):
        cp = new_checkpoint(tmp_path / "cp.json", "hash_old")
        cp.record("a.png", ProcessingStatus.OK, [], [])
        cp.flush()

        loaded = load_checkpoint(tmp_path / "cp.json", "hash_new", force=True)
        assert loaded.is_done("a.png")

    def test_load_nonexistent_returns_fresh(self, tmp_path):
        loaded = load_checkpoint(tmp_path / "missing.json", "abc")
        assert loaded.skip_count == 0


class TestHashFile:
    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.yaml"
        f2 = tmp_path / "b.yaml"
        f1.write_text("content: 42\n")
        f2.write_text("content: 42\n")
        assert hash_file(f1) == hash_file(f2)

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.yaml"
        f2 = tmp_path / "b.yaml"
        f1.write_text("content: 42\n")
        f2.write_text("content: 99\n")
        assert hash_file(f1) != hash_file(f2)


class TestPipelineResume:
    def test_resume_skips_completed_images(self, tmp_path):
        """Pipeline with checkpoint should skip already-processed images."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        review_dir = tmp_path / "review"
        input_dir.mkdir()

        # Create 3 test images
        for i in range(3):
            _save_test_image(input_dir / f"IMG{i:03d}.png", _make_dark_square_image())

        mapping = _make_mapping(
            ("IMG000", "A0", "front", "BALL"),
            ("IMG001", "A1", "front", "BALL"),
            ("IMG002", "A2", "front", "BALL"),
        )
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        # First run: process all 3
        cp1 = new_checkpoint(output_dir / ".checkpoint.json", "testhash")
        pipeline1 = Pipeline(config, mapping, ClassicalCropStrategy())
        stats1 = pipeline1.run(input_dir, output_dir, review_dir, checkpoint=cp1)
        assert stats1.total_attempted == 3

        # Second run with resume: should skip all 3
        cp2 = load_checkpoint(output_dir / ".checkpoint.json", "testhash")
        pipeline2 = Pipeline(config, mapping, ClassicalCropStrategy())
        stats2 = pipeline2.run(input_dir, output_dir, review_dir, checkpoint=cp2)
        assert stats2.total_attempted == 0

    def test_resume_processes_new_images(self, tmp_path):
        """Resume should process images not in checkpoint."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        review_dir = tmp_path / "review"
        input_dir.mkdir()

        # First run: 2 images
        for i in range(2):
            _save_test_image(input_dir / f"IMG{i:03d}.png", _make_dark_square_image())

        mapping = _make_mapping(
            ("IMG000", "A0", "front", "BALL"),
            ("IMG001", "A1", "front", "BALL"),
            ("IMG002", "A2", "front", "BALL"),
        )
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        cp1 = new_checkpoint(output_dir / ".checkpoint.json", "testhash")
        pipeline1 = Pipeline(config, mapping, ClassicalCropStrategy())
        pipeline1.run(input_dir, output_dir, review_dir, checkpoint=cp1)

        # Add a third image
        _save_test_image(input_dir / "IMG002.png", _make_dark_square_image())

        # Resume: should only process IMG002
        cp2 = load_checkpoint(output_dir / ".checkpoint.json", "testhash")
        pipeline2 = Pipeline(config, mapping, ClassicalCropStrategy())
        stats2 = pipeline2.run(input_dir, output_dir, review_dir, checkpoint=cp2)
        assert stats2.total_attempted == 1

    def test_no_checkpoint_processes_all(self, tmp_path):
        """Without checkpoint, all images are processed."""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        review_dir = tmp_path / "review"
        input_dir.mkdir()

        for i in range(2):
            _save_test_image(input_dir / f"IMG{i:03d}.png", _make_dark_square_image())

        mapping = _make_mapping(
            ("IMG000", "A0", "front", "BALL"),
            ("IMG001", "A1", "front", "BALL"),
        )
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=200))

        pipeline = Pipeline(config, mapping, ClassicalCropStrategy())
        stats = pipeline.run(input_dir, output_dir, review_dir, checkpoint=None)
        assert stats.total_attempted == 2
