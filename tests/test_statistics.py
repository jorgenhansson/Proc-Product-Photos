"""Tests for statistics accumulation and output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from process_images.models import (
    BackgroundType,
    CropMetrics,
    Flag,
    ProcessingResult,
    ProcessingStatus,
)
from process_images.statistics import StatsAccumulator


def _make_result(
    status: ProcessingStatus = ProcessingStatus.OK,
    category: str = "BALL",
    flags: list[Flag] | None = None,
    fill_ratio: float = 0.5,
    time_s: float = 0.1,
    crop_time: float = 0.05,
    bg_type: BackgroundType = BackgroundType.WHITE_BG,
    fallback: bool = False,
) -> ProcessingResult:
    return ProcessingResult(
        source_path=Path("test.png"),
        status=status,
        category=category,
        flags=flags or [],
        crop_metrics=CropMetrics(fill_ratio=fill_ratio, crop_area_ratio=0.6),
        background_type=bg_type,
        processing_time_s=time_s,
        crop_time_s=crop_time,
        fallback_attempted=fallback,
        fallback_time_s=0.05 if fallback else 0.0,
    )


class TestStatsAccumulator:
    def test_empty_accumulator(self):
        acc = StatsAccumulator()
        d = acc.to_dict()
        assert d["general"]["total_attempted"] == 0
        assert d["general"]["total_ok"] == 0

    def test_record_increments_count(self):
        acc = StatsAccumulator()
        acc.record(_make_result())
        assert acc.total_attempted == 1

    def test_status_counts(self):
        acc = StatsAccumulator()
        acc.record(_make_result(ProcessingStatus.OK))
        acc.record(_make_result(ProcessingStatus.FLAGGED))
        acc.record(_make_result(ProcessingStatus.FAILED))
        acc.record(_make_result(ProcessingStatus.RECOVERED))
        d = acc.to_dict()
        assert d["general"]["total_ok"] == 1
        assert d["general"]["total_flagged"] == 1
        assert d["general"]["total_failed"] == 1
        assert d["general"]["total_recovered"] == 1

    def test_by_category(self):
        acc = StatsAccumulator()
        acc.record(_make_result(category="BALL"))
        acc.record(_make_result(category="BALL"))
        acc.record(_make_result(category="SHOE"))
        d = acc.to_dict()
        assert "BALL" in d["by_category"]
        assert d["by_category"]["BALL"]["ok"] == 2
        assert d["by_category"]["SHOE"]["ok"] == 1

    def test_by_flag(self):
        acc = StatsAccumulator()
        acc.record(
            _make_result(
                status=ProcessingStatus.FLAGGED,
                flags=[Flag.MASK_TOO_SMALL, Flag.FILL_RATIO_TOO_LOW],
            )
        )
        d = acc.to_dict()
        assert d["by_flag"]["mask_too_small"] == 1
        assert d["by_flag"]["fill_ratio_too_low"] == 1

    def test_quality_metrics(self):
        acc = StatsAccumulator()
        acc.record(_make_result(fill_ratio=0.4))
        acc.record(_make_result(fill_ratio=0.6))
        d = acc.to_dict()
        assert abs(d["quality"]["avg_fill_ratio"] - 0.5) < 0.01
        assert d["quality"]["min_fill_ratio"] == 0.4
        assert d["quality"]["max_fill_ratio"] == 0.6

    def test_fallback_stats(self):
        acc = StatsAccumulator()
        acc.record(_make_result(fallback=True))
        acc.record(_make_result(fallback=False))
        d = acc.to_dict()
        assert d["performance"]["fallback_invocation_count"] == 1
        assert d["performance"]["fallback_invocation_rate"] == 0.5

    def test_to_json(self, tmp_path: Path):
        acc = StatsAccumulator()
        acc.record(_make_result())
        out = tmp_path / "stats.json"
        acc.to_json(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["general"]["total_ok"] == 1

    def test_to_console(self):
        acc = StatsAccumulator()
        acc.total_discovered = 5
        acc.record(_make_result())
        acc.record(
            _make_result(
                status=ProcessingStatus.FLAGGED, flags=[Flag.MASK_TOO_SMALL]
            )
        )
        text = acc.to_console()
        assert "PROCESSING SUMMARY" in text
        assert "Discovered:  5" in text
        assert "OK:" in text

    def test_background_type_counts(self):
        acc = StatsAccumulator()
        acc.record(_make_result(bg_type=BackgroundType.WHITE_BG))
        acc.record(_make_result(bg_type=BackgroundType.TRANSPARENT))
        acc.record(_make_result(bg_type=BackgroundType.WHITE_BG))
        d = acc.to_dict()
        assert d["by_background_type"]["white_bg"] == 2
        assert d["by_background_type"]["transparent"] == 1

    def test_mapping_issue_counts(self):
        acc = StatsAccumulator()
        acc.record(
            _make_result(
                status=ProcessingStatus.FLAGGED, flags=[Flag.MISSING_MAPPING]
            )
        )
        acc.record(
            _make_result(
                status=ProcessingStatus.FLAGGED, flags=[Flag.NAMING_CONFLICT]
            )
        )
        d = acc.to_dict()
        assert d["mapping"]["missing_mapping_count"] == 1
        assert d["mapping"]["naming_conflict_count"] == 1

    def test_per_category_success_rate(self):
        acc = StatsAccumulator()
        acc.record(_make_result(category="CLUB_LONG", status=ProcessingStatus.OK))
        acc.record(_make_result(category="CLUB_LONG", status=ProcessingStatus.OK))
        acc.record(_make_result(category="CLUB_LONG", status=ProcessingStatus.RECOVERED))
        acc.record(_make_result(category="CLUB_LONG", status=ProcessingStatus.FLAGGED))
        acc.record(_make_result(category="CLUB_LONG", status=ProcessingStatus.FAILED))
        d = acc.to_dict()
        cat = d["by_category"]["CLUB_LONG"]
        assert cat["total"] == 5
        assert cat["ok"] == 2
        assert cat["recovered"] == 1
        assert cat["success_rate"] == 0.6  # (2+1)/5
        assert cat["fallback_recovery_rate"] > 0

    def test_fill_ratio_percentiles(self):
        acc = StatsAccumulator()
        # 10 values: 0.1, 0.2, ..., 1.0
        for i in range(1, 11):
            acc.record(_make_result(fill_ratio=i / 10))
        d = acc.to_dict()
        q = d["quality"]
        assert q["fill_ratio_p10"] > 0
        assert q["fill_ratio_p50"] > 0
        assert q["fill_ratio_p90"] > 0
        # p10 < p50 < p90
        assert q["fill_ratio_p10"] < q["fill_ratio_p50"] < q["fill_ratio_p90"]

    def test_timing_percentiles(self):
        acc = StatsAccumulator()
        for t in [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.5, 5.0]:
            acc.record(_make_result(time_s=t, crop_time=t * 0.8))
        d = acc.to_dict()
        p = d["performance"]
        assert p["per_image_p95_s"] > p["avg_per_image_s"]
        assert p["primary_crop_avg_s"] > 0
        assert p["primary_crop_p95_s"] > 0

    def test_crop_time_tracked(self):
        acc = StatsAccumulator()
        acc.record(_make_result(time_s=1.0, crop_time=0.3))
        acc.record(_make_result(time_s=2.0, crop_time=0.7))
        d = acc.to_dict()
        assert abs(d["performance"]["primary_crop_avg_s"] - 0.5) < 0.01

    def test_console_shows_success_rate(self):
        acc = StatsAccumulator()
        acc.total_discovered = 2
        acc.record(_make_result(category="BALL"))
        acc.record(
            _make_result(category="BALL", status=ProcessingStatus.FLAGGED)
        )
        text = acc.to_console()
        assert "success_rate=" in text
        assert "p50=" in text or "p10=" in text
        assert "Primary crop:" in text
