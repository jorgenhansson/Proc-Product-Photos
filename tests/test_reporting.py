"""Tests for HTML report and review manifest generation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from process_images.models import (
    CropMetrics,
    Flag,
    ProcessingResult,
    ProcessingStatus,
)
from process_images.reporting import (
    generate_side_by_side,
    write_html_report,
    write_review_manifest,
)
from process_images.statistics import StatsAccumulator


def _acc_with_results() -> StatsAccumulator:
    """Build a StatsAccumulator with a mix of OK, flagged, recovered results."""
    acc = StatsAccumulator()
    acc.total_discovered = 5
    acc.record(
        ProcessingResult(
            source_path=Path("a.png"),
            status=ProcessingStatus.OK,
            category="BALL",
            crop_metrics=CropMetrics(fill_ratio=0.5),
        )
    )
    acc.record(
        ProcessingResult(
            source_path=Path("b.png"),
            status=ProcessingStatus.OK,
            category="BALL",
            crop_metrics=CropMetrics(fill_ratio=0.6),
        )
    )
    acc.record(
        ProcessingResult(
            source_path=Path("c.png"),
            status=ProcessingStatus.FLAGGED,
            category="BALL",
            flags=[Flag.FILL_RATIO_TOO_LOW],
            crop_metrics=CropMetrics(fill_ratio=0.1),
        )
    )
    acc.record(
        ProcessingResult(
            source_path=Path("d.png"),
            status=ProcessingStatus.RECOVERED,
            category="SHOE",
            crop_metrics=CropMetrics(fill_ratio=0.55),
            fallback_attempted=True,
        )
    )
    acc.record(
        ProcessingResult(
            source_path=Path("e.png"),
            status=ProcessingStatus.FAILED,
            category="SHOE",
            flags=[Flag.IMAGE_READ_ERROR],
            error_message="corrupt file",
        )
    )
    return acc


class TestHtmlReport:
    def test_html_report_is_valid_html(self, tmp_path: Path):
        acc = _acc_with_results()
        path = tmp_path / "report.html"
        write_html_report(acc.to_dict(), acc.results, path)
        html = path.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_html_report_category_totals_correct(self, tmp_path: Path):
        """Regression test for #11: sum(counts.values()) included floats."""
        acc = _acc_with_results()
        path = tmp_path / "report.html"
        write_html_report(acc.to_dict(), acc.results, path)
        html = path.read_text(encoding="utf-8")

        # BALL: 2 OK + 1 FLAGGED = 3 total
        ball_match = re.search(r"BALL</td><td>(\d+)</td>", html)
        assert ball_match is not None, "BALL row not found in HTML"
        assert ball_match.group(1) == "3", (
            f"BALL total should be 3, got {ball_match.group(1)}"
        )

        # SHOE: 1 RECOVERED + 1 FAILED = 2 total
        shoe_match = re.search(r"SHOE</td><td>(\d+)</td>", html)
        assert shoe_match is not None, "SHOE row not found in HTML"
        assert shoe_match.group(1) == "2", (
            f"SHOE total should be 2, got {shoe_match.group(1)}"
        )

    def test_html_report_contains_success_rate(self, tmp_path: Path):
        acc = _acc_with_results()
        path = tmp_path / "report.html"
        write_html_report(acc.to_dict(), acc.results, path)
        html = path.read_text(encoding="utf-8")
        assert "Success Rate" in html

    def test_html_report_lists_flagged_images(self, tmp_path: Path):
        acc = _acc_with_results()
        path = tmp_path / "report.html"
        write_html_report(acc.to_dict(), acc.results, path)
        html = path.read_text(encoding="utf-8")
        assert "c.png" in html  # flagged
        assert "e.png" in html  # failed


class TestReviewManifest:
    def test_manifest_only_includes_flagged_and_failed(self, tmp_path: Path):
        acc = _acc_with_results()
        path = tmp_path / "manifest.json"
        write_review_manifest(acc.results, path)
        items = json.loads(path.read_text())
        sources = [item["source"] for item in items]
        assert "c.png" in sources  # flagged
        assert "e.png" in sources  # failed
        assert "a.png" not in sources  # OK
        assert "d.png" not in sources  # recovered

    def test_manifest_has_required_fields(self, tmp_path: Path):
        acc = _acc_with_results()
        path = tmp_path / "manifest.json"
        write_review_manifest(acc.results, path)
        items = json.loads(path.read_text())
        for item in items:
            assert "source" in item
            assert "source_size_bytes" in item
            assert "source_dimensions" in item
            assert "proposed_outputs" in item
            assert "primary_metrics" in item
            assert "fallback_metrics" in item
            assert "fallback_succeeded" in item
            assert "flags" in item
            assert "flag_descriptions" in item


class TestSideBySide:
    def test_generates_labeled_preview(self, tmp_path: Path):
        """Side-by-side preview should produce a PNG with 4 panels + labels."""
        original = np.full((100, 100, 3), 128, dtype=np.uint8)
        mask = np.zeros((100, 100), dtype=np.uint8)
        mask[20:80, 20:80] = 255
        cropped = np.full((60, 60, 3), 64, dtype=np.uint8)
        final = np.full((200, 200, 3), 255, dtype=np.uint8)
        final[50:150, 50:150] = [64, 64, 64]

        path = tmp_path / "preview.png"
        generate_side_by_side(original, mask, cropped, final, path, panel_size=100)

        assert path.exists()
        img = Image.open(path)
        # 4 panels of 100px wide = 400px, plus label strip height
        assert img.width == 400
        assert img.height > 100  # panel + label strip

    def test_handles_none_panels(self, tmp_path: Path):
        """None panels should be rendered as gray placeholders."""
        original = np.full((50, 50, 3), 100, dtype=np.uint8)
        path = tmp_path / "partial.png"
        generate_side_by_side(original, None, None, None, path, panel_size=80)
        assert path.exists()
        img = Image.open(path)
        assert img.width == 320  # 4 * 80

    def test_handles_rgba_panels(self, tmp_path: Path):
        """RGBA images should be handled without crashing."""
        original = np.zeros((50, 50, 4), dtype=np.uint8)
        original[:, :, 3] = 255
        path = tmp_path / "rgba.png"
        generate_side_by_side(original, None, None, None, path, panel_size=80)
        assert path.exists()
