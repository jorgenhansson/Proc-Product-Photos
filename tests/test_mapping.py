"""Tests for mapping loader and validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from process_images.mapping import load_mapping
from process_images.models import Flag


class TestLoadMapping:
    def test_load_csv(self, sample_mapping_csv: Path):
        result = load_mapping(sample_mapping_csv)
        assert len(result.rows_by_sku) == 3
        # Keys are case-folded (lowercase)
        assert "img001" in result.rows_by_sku
        # Original case preserved in MappingRow
        assert result.rows_by_sku["img001"][0].supplier_sku == "IMG001"
        assert result.rows_by_sku["img001"][0].category == "CLUB_LONG"

    def test_lookup_existing(self, sample_mapping_csv: Path):
        result = load_mapping(sample_mapping_csv)
        rows = result.lookup("IMG002")
        assert len(rows) == 1
        assert rows[0].store_article == "100002"

    def test_lookup_case_insensitive(self, sample_mapping_csv: Path):
        """Lookup should match regardless of case (#30)."""
        result = load_mapping(sample_mapping_csv)
        assert len(result.lookup("IMG001")) == 1
        assert len(result.lookup("img001")) == 1
        assert len(result.lookup("Img001")) == 1
        # All return the same row with original case
        assert result.lookup("img001")[0].supplier_sku == "IMG001"

    def test_lookup_missing(self, sample_mapping_csv: Path):
        result = load_mapping(sample_mapping_csv)
        assert result.lookup("NONEXISTENT") == []

    def test_missing_columns(self, tmp_path: Path):
        csv = tmp_path / "bad.csv"
        csv.write_text("sku,article\nABC,123\n", encoding="utf-8")
        result = load_mapping(csv)
        assert any(f == Flag.IMAGE_READ_ERROR for f, _ in result.issues)

    def test_empty_sku_warning(self, tmp_path: Path):
        csv = tmp_path / "empty_sku.csv"
        csv.write_text(
            "supplier_sku,store_article,suffix,category\n"
            ",100001,front,BALL\n",
            encoding="utf-8",
        )
        result = load_mapping(csv)
        assert any(f == Flag.MISSING_MAPPING for f, _ in result.issues)

    def test_missing_category_warning(self, tmp_path: Path):
        csv = tmp_path / "no_cat.csv"
        csv.write_text(
            "supplier_sku,store_article,suffix,category\n"
            "IMG001,100001,front,\n",
            encoding="utf-8",
        )
        result = load_mapping(csv)
        assert any(f == Flag.MISSING_MAPPING for f, _ in result.issues)

    def test_duplicate_output_filename(self, tmp_path: Path):
        csv = tmp_path / "dupes.csv"
        csv.write_text(
            "supplier_sku,store_article,suffix,category\n"
            "IMG001,100001,front,BALL\n"
            "IMG002,100001,front,BALL\n",
            encoding="utf-8",
        )
        result = load_mapping(csv)
        assert any(f == Flag.NAMING_CONFLICT for f, _ in result.issues)

    def test_unsafe_filename(self, tmp_path: Path):
        csv = tmp_path / "unsafe.csv"
        csv.write_text(
            "supplier_sku,store_article,suffix,category\n"
            "IMG001,100/001,fr ont,BALL\n",
            encoding="utf-8",
        )
        result = load_mapping(csv)
        assert any(f == Flag.NAMING_CONFLICT for f, _ in result.issues)

    def test_load_nonexistent_file(self, tmp_path: Path):
        result = load_mapping(tmp_path / "nonexistent.csv")
        assert any(f == Flag.IMAGE_READ_ERROR for f, _ in result.issues)

    def test_xlsx_format(self, tmp_path: Path):
        """Test XLSX loading if openpyxl is available."""
        import pandas as pd

        xlsx_path = tmp_path / "mapping.xlsx"
        df = pd.DataFrame(
            {
                "supplier_sku": ["IMG001"],
                "store_article": ["200001"],
                "suffix": ["front"],
                "category": ["SHOE"],
            }
        )
        df.to_excel(xlsx_path, index=False)
        result = load_mapping(xlsx_path)
        assert "img001" in result.rows_by_sku
        assert result.rows_by_sku["img001"][0].category == "SHOE"
