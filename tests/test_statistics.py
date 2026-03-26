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
