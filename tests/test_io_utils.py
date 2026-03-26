"""Tests for image I/O utilities."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.io_utils import discover_images, load_image, save_jpeg


class TestDiscoverImages:
    def test_finds_supported_extensions(self, tmp_path: Path):
        (tmp_path / "a.png").write_bytes(b"")
        (tmp_path / "b.jpg").write_bytes(b"")
        (tmp_path / "c.tif").write_bytes(b"")
        (tmp_path / "d.txt").write_bytes(b"")
        found = discover_images(tmp_path)
        names = [f.name for f in found]
        assert "a.png" in names
        assert "b.jpg" in names
        assert "c.tif" in names
        assert "d.txt" not in names

    def test_case_insensitive(self, tmp_path: Path):
        (tmp_path / "img.PNG").write_bytes(b"")
        (tmp_path / "img.JPEG").write_bytes(b"")
        found = discover_images(tmp_path)
        assert len(found) == 2

    def test_sorted_by_name(self, tmp_path: Path):
        for name in ["c.png", "a.png", "b.png"]:
            Image.fromarray(np.zeros((2, 2, 3), dtype=np.uint8)).save(
                tmp_path / name
            )
        found = discover_images(tmp_path)
        assert [f.name for f in found] == ["a.png", "b.png", "c.png"]

    def test_empty_directory(self, tmp_path: Path):
        assert discover_images(tmp_path) == []


class TestLoadImage:
    def test_loads_rgb_png(self, tmp_path: Path):
        img = np.full((50, 80, 3), 128, dtype=np.uint8)
        path = tmp_path / "test.png"
        Image.fromarray(img).save(path)
        loaded = load_image(path)
        assert loaded is not None
        assert loaded.shape == (50, 80, 3)

    def test_loads_rgba_png(self, tmp_path: Path):
        img = np.zeros((30, 40, 4), dtype=np.uint8)
        img[:, :, 3] = 200
        path = tmp_path / "test.png"
        Image.fromarray(img, mode="RGBA").save(path)
        loaded = load_image(path)
        assert loaded is not None
        assert loaded.shape[2] == 4

    def test_returns_none_for_corrupt(self, tmp_path: Path):
        path = tmp_path / "bad.png"
        path.write_bytes(b"not an image")
        assert load_image(path) is None

    def test_exif_rotation_applied(self, tmp_path: Path):
        """A 100x50 image with EXIF rotation 6 (90° CW) should be
        loaded as 50x100 after transpose."""
        piexif = pytest.importorskip("piexif")

        # Create a 100w x 50h image
        img = Image.fromarray(
            np.full((50, 100, 3), 128, dtype=np.uint8)
        )
        # Set EXIF orientation = 6 (90° CW rotation)
        exif_dict = {"0th": {piexif.ImageIFD.Orientation: 6}}
        exif_bytes = piexif.dump(exif_dict)
        path = tmp_path / "rotated.jpg"
        img.save(path, format="JPEG", exif=exif_bytes)

        loaded = load_image(path)
        assert loaded is not None
        # After 90° CW transpose: 100x50 → 50x100 (h=100, w=50)
        assert loaded.shape[0] == 100, f"Expected h=100, got {loaded.shape[0]}"
        assert loaded.shape[1] == 50, f"Expected w=50, got {loaded.shape[1]}"

    def test_no_exif_works_fine(self, tmp_path: Path):
        """Image without EXIF data should load normally."""
        img = np.full((60, 80, 3), 100, dtype=np.uint8)
        path = tmp_path / "no_exif.png"
        Image.fromarray(img).save(path)
        loaded = load_image(path)
        assert loaded is not None
        assert loaded.shape == (60, 80, 3)

    def test_multi_page_tiff_warns(self, tmp_path: Path, caplog):
        """Multi-page TIFF should load first page and log a warning."""
        import logging

        path = tmp_path / "multi.tif"
        frames = [
            Image.fromarray(np.full((20, 20, 3), c, dtype=np.uint8))
            for c in [100, 200]
        ]
        frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])

        with caplog.at_level(logging.WARNING, logger="process_images.io_utils"):
            loaded = load_image(path)

        assert loaded is not None
        assert loaded.shape == (20, 20, 3)
        assert "2 pages" in caplog.text


class TestSaveJpeg:
    def test_round_trip(self, tmp_path: Path):
        img = np.full((100, 100, 3), 128, dtype=np.uint8)
        path = tmp_path / "out.jpg"
        save_jpeg(img, path, quality=95)
        assert path.exists()
        loaded = Image.open(path)
        assert loaded.size == (100, 100)
        assert loaded.mode == "RGB"

    def test_rgba_composited_on_white(self, tmp_path: Path):
        img = np.zeros((50, 50, 4), dtype=np.uint8)
        img[:, :, 0] = 255  # red
        img[:, :, 3] = 128  # 50% alpha
        path = tmp_path / "rgba.jpg"
        save_jpeg(img, path)
        loaded = np.array(Image.open(path))
        # Should be blended, not pure red or black
        assert loaded[25, 25, 0] > 100  # red present
        assert loaded[25, 25, 1] > 50  # white bleed-through

    def test_creates_parent_dirs(self, tmp_path: Path):
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        path = tmp_path / "sub" / "dir" / "img.jpg"
        save_jpeg(img, path)
        assert path.exists()
