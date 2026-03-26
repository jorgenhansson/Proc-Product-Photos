"""Tests for output filename generation."""

from __future__ import annotations

import pytest

from process_images.models import MappingRow


class TestOutputFilename:
    def test_basic_filename(self):
        row = MappingRow(
            supplier_sku="IMG001",
            store_article="123456",
            suffix="front",
            category="BALL",
        )
        assert row.output_filename == "123456_front.jpg"

    def test_filename_with_alt_suffix(self):
        row = MappingRow(
            supplier_sku="IMG002",
            store_article="789012",
            suffix="alt1",
            category="SHOE",
        )
        assert row.output_filename == "789012_alt1.jpg"

    def test_filename_with_side(self):
        row = MappingRow(
            supplier_sku="X",
            store_article="A100",
            suffix="side",
            category="BAG",
        )
        assert row.output_filename == "A100_side.jpg"

    def test_filename_is_always_jpg(self):
        row = MappingRow(
            supplier_sku="X",
            store_article="999",
            suffix="back",
            category="CLUB_LONG",
        )
        assert row.output_filename.endswith(".jpg")

    def test_filename_contains_article_and_suffix(self):
        row = MappingRow(
            supplier_sku="X",
            store_article="ART",
            suffix="SUF",
            category="BALL",
        )
        name = row.output_filename
        assert "ART" in name
        assert "SUF" in name
