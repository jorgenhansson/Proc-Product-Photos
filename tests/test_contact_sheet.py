"""Tests for contact sheet generation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.contact_sheet import generate_contact_sheet


def _make_test_image(path: Path, color: tuple = (40, 40, 40)) -> None:
    """Create a small test image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.full((100, 100, 3), 255, dtype=np.uint8)
    img[20:80, 20:80] = color
    Image.fromarray(img).save(path, format="JPEG")


class TestContactSheet:
    def test_basic_generation(self, tmp_path):
        img_dir = tmp_path / "output"
        img_dir.mkdir()
        for i in range(5):
            _make_test_image(img_dir / f"img{i}-cropped.jpg")

        results = {
            f"img{i}.png": {"status": "ok", "category": "BALL", "flags": []}
            for i in range(5)
        }

        out = tmp_path / "contact.jpg"
        generate_contact_sheet(img_dir, results, out, thumb_size=50, columns=5)

        assert out.exists()
        sheet = Image.open(out)
        assert sheet.size[0] > 0
        assert sheet.size[1] > 0

    def test_multiple_categories(self, tmp_path):
        img_dir = tmp_path / "output"
        img_dir.mkdir()

        results = {}
        for i in range(3):
            _make_test_image(img_dir / f"ball{i}-cropped.jpg", (200, 50, 50))
            results[f"ball{i}.png"] = {"status": "ok", "category": "BALL", "flags": []}
        for i in range(4):
            _make_test_image(img_dir / f"shoe{i}-cropped.jpg", (50, 50, 200))
            results[f"shoe{i}.png"] = {"status": "ok", "category": "SHOE", "flags": []}

        out = tmp_path / "contact.png"
        generate_contact_sheet(img_dir, results, out, thumb_size=50, columns=3)

        assert out.exists()
        sheet = Image.open(out)
        # Should be tall enough for 2 categories with headers
        assert sheet.size[1] > 100

    def test_recovered_and_flagged_borders(self, tmp_path):
        img_dir = tmp_path / "output"
        img_dir.mkdir()

        _make_test_image(img_dir / "ok-cropped.jpg")
        _make_test_image(img_dir / "recovered-cropped.jpg")
        _make_test_image(img_dir / "flagged-cropped.jpg")

        results = {
            "ok.png": {"status": "ok", "category": "BALL", "flags": []},
            "recovered.png": {"status": "recovered", "category": "BALL", "flags": ["multiple_large_components"]},
            "flagged.png": {"status": "flagged", "category": "BALL", "flags": ["mask_too_small"]},
        }

        out = tmp_path / "contact.jpg"
        generate_contact_sheet(img_dir, results, out, thumb_size=80, columns=3)
        assert out.exists()

    def test_empty_results(self, tmp_path):
        img_dir = tmp_path / "output"
        img_dir.mkdir()
        out = tmp_path / "contact.jpg"

        generate_contact_sheet(img_dir, {}, out)
        # Should not crash, just warn
        assert not out.exists()  # no images = no sheet

    def test_missing_image_shows_placeholder(self, tmp_path):
        img_dir = tmp_path / "output"
        img_dir.mkdir()

        # Result references an image that doesn't exist as output
        _make_test_image(img_dir / "exists-cropped.jpg")
        results = {
            "exists.png": {"status": "ok", "category": "BALL", "flags": []},
            "missing.png": {"status": "ok", "category": "BALL", "flags": []},
        }

        out = tmp_path / "contact.jpg"
        generate_contact_sheet(img_dir, results, out, thumb_size=50, columns=2)
        assert out.exists()

    def test_custom_columns_and_size(self, tmp_path):
        img_dir = tmp_path / "output"
        img_dir.mkdir()

        for i in range(10):
            _make_test_image(img_dir / f"img{i}-cropped.jpg")

        results = {
            f"img{i}.png": {"status": "ok", "category": "BAG", "flags": []}
            for i in range(10)
        }

        out = tmp_path / "contact.png"
        generate_contact_sheet(img_dir, results, out, thumb_size=80, columns=5)

        sheet = Image.open(out)
        expected_width = 5 * (80 + 4) + 8  # 5 cols * cell_size + padding
        assert abs(sheet.size[0] - expected_width) < 10

    def test_large_batch(self, tmp_path):
        """Simulate a larger batch to verify scaling."""
        img_dir = tmp_path / "output"
        img_dir.mkdir()

        results = {}
        for i in range(100):
            _make_test_image(img_dir / f"prod{i:03d}-cropped.jpg")
            cat = ["BALL", "SHOE", "BAG", "CLUB_LONG", "APPAREL_FOLDED"][i % 5]
            status = "ok" if i % 7 != 0 else "recovered"
            results[f"prod{i:03d}.png"] = {
                "status": status,
                "category": cat,
                "flags": ["multiple_large_components"] if status == "recovered" else [],
            }

        out = tmp_path / "contact.jpg"
        generate_contact_sheet(img_dir, results, out, thumb_size=60, columns=15)

        assert out.exists()
        sheet = Image.open(out)
        assert sheet.size[0] > 500
        assert sheet.size[1] > 200
