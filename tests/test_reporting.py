"""Tests for HTML report and review manifest generation."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

from process_images.models import (
    CropMetrics,
    Flag,
    ProcessingResult,
    ProcessingStatus,
)
from process_images.reporting import write_html_report, write_review_manifest
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
