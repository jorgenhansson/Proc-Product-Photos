"""Tests for the CLI entry point."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from process_images.cli import app

runner = CliRunner()


def _setup_run(tmp_path: Path) -> dict[str, Path]:
    """Create minimal input dir, mapping, and rules for a CLI run."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    review_dir = tmp_path / "review"
    input_dir.mkdir()

    # One test image
    img = np.full((100, 100, 3), 255, dtype=np.uint8)
    img[30:70, 30:70] = [40, 40, 40]
    Image.fromarray(img).save(input_dir / "SKU001.png")

    # Mapping
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "supplier_sku,store_article,suffix,category\n"
        "SKU001,ART100,front,BALL\n",
        encoding="utf-8",
    )

    # Rules
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "global:\n  canvas_size: 100\nfallback:\n  enabled: false\n",
        encoding="utf-8",
    )

    return {
        "input": input_dir,
        "output": output_dir,
        "review": review_dir,
        "mapping": mapping,
        "rules": rules,
    }


class TestCli:
    def test_dry_run_exits_zero(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0

    def test_missing_input_dir_exits_nonzero(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        result = runner.invoke(
            app,
            [
                "--input", str(tmp_path / "nonexistent"),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
            ],
        )
        assert result.exit_code != 0

    def test_missing_mapping_exits_nonzero(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(tmp_path / "no.csv"),
                "--rules", str(paths["rules"]),
            ],
        )
        assert result.exit_code != 0

    def test_missing_rules_exits_nonzero(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(tmp_path / "no.yaml"),
            ],
        )
        assert result.exit_code != 0

    def test_full_run_produces_output(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
            ],
        )
        assert result.exit_code == 0
        assert (paths["output"] / "ART100_front.jpg").exists()

    def test_stats_file_written(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        stats_path = tmp_path / "stats.json"
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
                "--stats", str(stats_path),
            ],
        )
        assert result.exit_code == 0
        assert stats_path.exists()

    def test_html_report_written(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        report = tmp_path / "report.html"
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
                "--html-report", str(report),
            ],
        )
        assert result.exit_code == 0
        assert report.exists()
        assert "<!DOCTYPE html>" in report.read_text()

    def test_limit_flag(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        # Add a second image
        img2 = np.full((100, 100, 3), 255, dtype=np.uint8)
        img2[40:60, 40:60] = [80, 80, 80]
        Image.fromarray(img2).save(paths["input"] / "SKU002.png")
        # Add mapping for it
        with open(paths["mapping"], "a") as f:
            f.write("SKU002,ART200,front,SHOE\n")

        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
                "--limit", "1",
            ],
        )
        assert result.exit_code == 0
        # Only 1 image processed
        output_files = list(paths["output"].glob("*.jpg"))
        assert len(output_files) == 1

    def test_verbose_flag(self, tmp_path: Path):
        paths = _setup_run(tmp_path)
        result = runner.invoke(
            app,
            [
                "--input", str(paths["input"]),
                "--output", str(paths["output"]),
                "--review", str(paths["review"]),
                "--mapping", str(paths["mapping"]),
                "--rules", str(paths["rules"]),
                "--verbose",
            ],
        )
        assert result.exit_code == 0
