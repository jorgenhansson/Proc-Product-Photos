"""Tests for the diff report tool."""

from __future__ import annotations

import json
from pathlib import Path

from process_images.diff import DiffReport, compute_diff, format_console, format_html


def _make_results(**images) -> dict:
    """Build a results dict from keyword args.

    Each kwarg: name=("status", "category", ["flag1", ...], fill_ratio)
    """
    out = {}
    for name, vals in images.items():
        status, cat, flags, fill = vals
        out[name] = {
            "status": status,
            "category": cat,
            "flags": flags,
            "fill_ratio": fill,
        }
    return out


class TestComputeDiff:
    def test_identical_runs(self):
        data = _make_results(
            a=("ok", "BALL", [], 0.85),
            b=("ok", "SHOE", [], 0.90),
        )
        report = compute_diff(data, data)
        assert report.compared == 2
        assert len(report.improved) == 0
        assert len(report.regressed) == 0
        assert report.unchanged_ok == 2

    def test_improved_image(self):
        before = _make_results(
            img1=("flagged", "BAG", ["mask_too_small"], 0.0),
            img2=("ok", "BALL", [], 0.85),
        )
        after = _make_results(
            img1=("ok", "BAG", [], 0.90),
            img2=("ok", "BALL", [], 0.85),
        )
        report = compute_diff(before, after)
        assert len(report.improved) == 1
        assert report.improved[0].name == "img1"
        assert report.improved[0].category == "BAG"
        assert len(report.regressed) == 0

    def test_regressed_image(self):
        before = _make_results(
            img1=("ok", "SHOE", [], 0.90),
        )
        after = _make_results(
            img1=("flagged", "SHOE", ["bbox_too_large"], 0.0),
        )
        report = compute_diff(before, after)
        assert len(report.regressed) == 1
        assert report.regressed[0].name == "img1"
        assert "bbox_too_large" in report.regressed[0].after_flags

    def test_new_image_in_after(self):
        before = _make_results(
            img1=("ok", "BALL", [], 0.85),
        )
        after = _make_results(
            img1=("ok", "BALL", [], 0.85),
            img2=("ok", "SHOE", [], 0.90),
        )
        report = compute_diff(before, after)
        assert len(report.new_images) == 1
        assert report.new_images[0].name == "img2"

    def test_removed_image(self):
        before = _make_results(
            img1=("ok", "BALL", [], 0.85),
            img2=("ok", "SHOE", [], 0.90),
        )
        after = _make_results(
            img1=("ok", "BALL", [], 0.85),
        )
        report = compute_diff(before, after)
        assert len(report.removed_images) == 1
        assert report.removed_images[0].name == "img2"

    def test_recovered_counts_as_ok(self):
        before = _make_results(
            img1=("flagged", "BAG", ["multiple_large_components"], 0.0),
        )
        after = _make_results(
            img1=("recovered", "BAG", ["multiple_large_components"], 0.95),
        )
        report = compute_diff(before, after)
        assert len(report.improved) == 1

    def test_flag_tracking(self):
        before = _make_results(
            img1=("flagged", "BAG", ["mask_too_small", "bbox_too_small"], 0.0),
        )
        after = _make_results(
            img1=("flagged", "BAG", ["mask_too_small", "no_object_found"], 0.0),
        )
        report = compute_diff(before, after)
        assert report.flags_added["no_object_found"] == 1
        assert report.flags_removed["bbox_too_small"] == 1

    def test_category_deltas(self):
        before = _make_results(
            a=("ok", "BALL", [], 0.85),
            b=("flagged", "BALL", ["mask_too_small"], 0.0),
            c=("ok", "SHOE", [], 0.90),
        )
        after = _make_results(
            a=("ok", "BALL", [], 0.85),
            b=("ok", "BALL", [], 0.80),
            c=("ok", "SHOE", [], 0.90),
        )
        report = compute_diff(before, after)
        assert report.category_deltas["BALL"]["before_rate"] == 0.5
        assert report.category_deltas["BALL"]["after_rate"] == 1.0
        assert report.category_deltas["BALL"]["improved"] == 1
        assert report.category_deltas["SHOE"]["delta_pct"] == 0.0

    def test_fill_ratio_change_tracked(self):
        before = _make_results(
            img1=("ok", "BALL", [], 0.50),
        )
        after = _make_results(
            img1=("ok", "BALL", [], 0.95),
        )
        report = compute_diff(before, after)
        assert len(report.fill_ratio_changes) == 1
        name, bf, af = report.fill_ratio_changes[0]
        assert name == "img1"
        assert bf == 0.50
        assert af == 0.95

    def test_empty_runs(self):
        report = compute_diff({}, {})
        assert report.compared == 0
        assert len(report.improved) == 0
        assert len(report.regressed) == 0

    def test_mixed_scenario(self):
        """Realistic scenario with multiple change types."""
        before = _make_results(
            ok1=("ok", "BALL", [], 0.85),
            ok2=("ok", "SHOE", [], 0.90),
            bad1=("flagged", "BAG", ["bbox_too_large"], 0.0),
            bad2=("flagged", "CLUB_LONG", ["crop_category_inconsistent"], 0.0),
            removed=("ok", "BALL", [], 0.80),
        )
        after = _make_results(
            ok1=("ok", "BALL", [], 0.85),
            ok2=("flagged", "SHOE", ["mask_too_small"], 0.0),  # regressed
            bad1=("ok", "BAG", [], 0.92),  # improved
            bad2=("flagged", "CLUB_LONG", ["crop_category_inconsistent"], 0.0),  # unchanged bad
            new1=("ok", "APPAREL_FOLDED", [], 0.88),  # new
        )
        report = compute_diff(before, after)
        assert len(report.improved) == 1
        assert len(report.regressed) == 1
        assert report.unchanged_ok == 1
        assert report.unchanged_bad == 1
        assert len(report.new_images) == 1
        assert len(report.removed_images) == 1


class TestFormatConsole:
    def test_console_output_has_summary(self):
        before = _make_results(
            img1=("flagged", "BAG", ["mask_too_small"], 0.0),
        )
        after = _make_results(
            img1=("ok", "BAG", [], 0.90),
        )
        report = compute_diff(before, after)
        text = format_console(report)
        assert "Improved:" in text
        assert "1" in text
        assert "BAG" in text

    def test_console_output_empty_diff(self):
        report = compute_diff({}, {})
        text = format_console(report)
        assert "DIFF REPORT" in text
        assert "Compared: 0" in text


class TestFormatHtml:
    def test_html_has_structure(self):
        before = _make_results(
            img1=("flagged", "BAG", ["mask_too_small"], 0.0),
        )
        after = _make_results(
            img1=("ok", "BAG", [], 0.90),
        )
        report = compute_diff(before, after)
        html = format_html(report)
        assert "<!DOCTYPE html>" in html
        assert "Diff Report" in html
        assert "Improved" in html
        assert "BAG" in html

    def test_html_write_to_file(self, tmp_path):
        report = compute_diff(
            _make_results(a=("ok", "BALL", [], 0.85)),
            _make_results(a=("ok", "BALL", [], 0.85)),
        )
        path = tmp_path / "report.html"
        path.write_text(format_html(report))
        assert path.exists()
        assert path.stat().st_size > 100
