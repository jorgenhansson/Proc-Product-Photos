"""Statistics accumulation, serialization, and console reporting."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .models import BackgroundType, Flag, ProcessingResult, ProcessingStatus


@dataclass
class StatsAccumulator:
    """Accumulates processing statistics across all images in a run."""

    start_time: float = field(default_factory=time.perf_counter)
    total_discovered: int = 0
    total_attempted: int = 0
    results: list[ProcessingResult] = field(default_factory=list)

    @property
    def total_ok(self) -> int:
        return sum(1 for r in self.results if r.status == ProcessingStatus.OK)

    @property
    def total_flagged(self) -> int:
        return sum(1 for r in self.results if r.status == ProcessingStatus.FLAGGED)

    @property
    def total_failed(self) -> int:
        return sum(1 for r in self.results if r.status == ProcessingStatus.FAILED)

    def record(self, result: ProcessingResult) -> None:
        """Record the result of processing one image."""
        self.total_attempted += 1
        self.results.append(result)

    def to_dict(self) -> dict:
        """Build full statistics dictionary."""
        elapsed = time.perf_counter() - self.start_time

        status_counts: Counter[ProcessingStatus] = Counter()
        bg_counts: Counter[BackgroundType] = Counter()
        cat_counts: dict[str, Counter[ProcessingStatus]] = defaultdict(Counter)
        flag_counts: Counter[Flag] = Counter()
        fill_ratios: list[float] = []
        crop_ratios: list[float] = []
        proc_times: list[float] = []
        crop_times: list[float] = []
        fallback_times: list[float] = []
        fallback_count = 0

        for r in self.results:
            status_counts[r.status] += 1
            if r.background_type:
                bg_counts[r.background_type] += 1

            cat = r.category or "UNKNOWN"
            cat_counts[cat][r.status] += 1

            for f in r.flags:
                flag_counts[f] += 1

            if r.crop_metrics and r.crop_metrics.fill_ratio > 0:
                fill_ratios.append(r.crop_metrics.fill_ratio)
                crop_ratios.append(r.crop_metrics.crop_area_ratio)

            proc_times.append(r.processing_time_s)
            if r.crop_time_s > 0:
                crop_times.append(r.crop_time_s)

            if r.fallback_attempted:
                fallback_count += 1
                fallback_times.append(r.fallback_time_s)

        missing_mapping = sum(
            1 for r in self.results if Flag.MISSING_MAPPING in r.flags
        )
        naming_conflicts = sum(
            1 for r in self.results if Flag.NAMING_CONFLICT in r.flags
        )

        # Per-category success rates
        by_category = {}
        for cat, counts in sorted(cat_counts.items()):
            total = sum(counts.values())
            ok = counts.get(ProcessingStatus.OK, 0)
            recovered = counts.get(ProcessingStatus.RECOVERED, 0)
            flagged = counts.get(ProcessingStatus.FLAGGED, 0)
            failed = counts.get(ProcessingStatus.FAILED, 0)
            success = ok + recovered
            needs_review = flagged + failed + recovered
            by_category[cat] = {
                "ok": ok,
                "recovered": recovered,
                "flagged": flagged,
                "failed": failed,
                "total": total,
                "success_rate": round(success / max(1, total), 4),
                "fallback_recovery_rate": round(
                    recovered / max(1, needs_review), 4
                ) if needs_review > 0 else 0.0,
            }

        return {
            "general": {
                "total_discovered": self.total_discovered,
                "total_attempted": self.total_attempted,
                "total_ok": status_counts.get(ProcessingStatus.OK, 0),
                "total_failed": status_counts.get(ProcessingStatus.FAILED, 0),
                "total_flagged": status_counts.get(ProcessingStatus.FLAGGED, 0),
                "total_recovered": status_counts.get(
                    ProcessingStatus.RECOVERED, 0
                ),
            },
            "by_background_type": {
                bt.value: count for bt, count in bg_counts.items()
            },
            "by_category": by_category,
            "by_flag": {
                f.value: c
                for f, c in sorted(
                    flag_counts.items(), key=lambda x: x[0].value
                )
            },
            "quality": {
                "avg_fill_ratio": _safe_avg(fill_ratios),
                "min_fill_ratio": min(fill_ratios) if fill_ratios else 0.0,
                "max_fill_ratio": max(fill_ratios) if fill_ratios else 0.0,
                "fill_ratio_p10": _percentile(fill_ratios, 10),
                "fill_ratio_p50": _percentile(fill_ratios, 50),
                "fill_ratio_p90": _percentile(fill_ratios, 90),
                "avg_crop_area_ratio": _safe_avg(crop_ratios),
            },
            "mapping": {
                "missing_mapping_count": missing_mapping,
                "naming_conflict_count": naming_conflicts,
            },
            "performance": {
                "total_time_s": round(elapsed, 2),
                "avg_per_image_s": round(_safe_avg(proc_times), 4),
                "per_image_p95_s": round(_percentile(proc_times, 95), 4),
                "primary_crop_avg_s": round(_safe_avg(crop_times), 4),
                "primary_crop_p95_s": round(_percentile(crop_times, 95), 4),
                "avg_fallback_time_s": round(
                    _safe_avg(fallback_times), 4
                ),
                "fallback_invocation_count": fallback_count,
                "fallback_invocation_rate": round(
                    fallback_count / max(1, self.total_attempted), 4
                ),
            },
        }

    def to_json(self, path: Path) -> None:
        """Write statistics to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def results_to_json(self, path: Path) -> None:
        """Write per-image results to a JSON file for diff analysis.

        Each entry includes source filename, status, category, flags,
        fill ratio, and crop metrics — everything needed to compare
        two runs and identify improvements/regressions.
        """
        entries = {}
        for r in self.results:
            name = r.source_path.name
            entry: dict = {
                "status": r.status.value,
                "category": r.category,
                "flags": [f.value for f in r.flags],
                "fallback_attempted": r.fallback_attempted,
            }
            if r.crop_metrics:
                entry["fill_ratio"] = round(r.crop_metrics.fill_ratio, 4)
                entry["crop_area_ratio"] = round(r.crop_metrics.crop_area_ratio, 4)
                entry["component_count"] = r.crop_metrics.component_count
            if r.background_type:
                entry["background_type"] = r.background_type.value
            entries[name] = entry

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)

    def to_console(self) -> str:
        """Format statistics as a human-readable console summary."""
        d = self.to_dict()
        g = d["general"]
        lines = [
            "=" * 60,
            "PROCESSING SUMMARY",
            "=" * 60,
            f"  Discovered:  {g['total_discovered']}",
            f"  Attempted:   {g['total_attempted']}",
            f"  OK:          {g['total_ok']}",
            f"  Recovered:   {g['total_recovered']}",
            f"  Flagged:     {g['total_flagged']}",
            f"  Failed:      {g['total_failed']}",
            "",
            "BY CATEGORY:",
        ]
        for cat, info in sorted(d["by_category"].items()):
            sr = info["success_rate"]
            lines.append(
                f"  {cat}: total={info['total']}  ok={info['ok']}  "
                f"recovered={info['recovered']}  flagged={info['flagged']}  "
                f"failed={info['failed']}  success_rate={sr:.0%}"
            )

        if d["by_flag"]:
            lines.append("")
            lines.append("BY FLAG:")
            for flag_name, count in sorted(d["by_flag"].items()):
                lines.append(f"  {flag_name}: {count}")

        q = d["quality"]
        lines.extend(
            [
                "",
                "QUALITY:",
                f"  Fill ratio: avg={q['avg_fill_ratio']:.3f}"
                f"  p10={q['fill_ratio_p10']:.3f}"
                f"  p50={q['fill_ratio_p50']:.3f}"
                f"  p90={q['fill_ratio_p90']:.3f}",
            ]
        )

        p = d["performance"]
        lines.extend(
            [
                "",
                "PERFORMANCE:",
                f"  Total time:      {p['total_time_s']:.1f}s",
                f"  Per image:       avg={p['avg_per_image_s']:.3f}s  p95={p['per_image_p95_s']:.3f}s",
                f"  Primary crop:    avg={p['primary_crop_avg_s']:.3f}s  p95={p['primary_crop_p95_s']:.3f}s",
                f"  Fallback rate:   {p['fallback_invocation_rate']:.1%}",
                "=" * 60,
            ]
        )

        return "\n".join(lines)


def _safe_avg(values: list[float]) -> float:
    """Average that returns 0.0 for empty lists."""
    return sum(values) / len(values) if values else 0.0


def _percentile(values: list[float], pct: int) -> float:
    """Compute percentile without numpy dependency. Returns 0.0 for empty lists."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    f = int(k)
    c = f + 1
    if c >= len(s):
        return float(s[f])
    return float(s[f] + (k - f) * (s[c] - s[f]))
