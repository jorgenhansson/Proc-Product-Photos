"""Diff report: compare two pipeline runs to identify improvements and regressions.

Standalone CLI tool that compares two per-image results files (from --results)
and produces a summary showing what changed, per-category deltas, and detailed
lists of improved/regressed images.

Usage:
    python -m process_images.diff \
        --before run_v1/results.json \
        --after run_v2/results.json \
        --output diff_report.html

Or console-only:
    python -m process_images.diff \
        --before run_v1/results.json \
        --after run_v2/results.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="diff",
    help="Compare two pipeline runs and report changes.",
    add_completion=False,
)


# -- Data models --

_OK_STATUSES = {"ok", "recovered"}
_BAD_STATUSES = {"flagged", "failed"}


@dataclass
class ImageDelta:
    """Change record for a single image between two runs."""

    name: str
    category: str = ""
    before_status: str = ""
    after_status: str = ""
    before_flags: list[str] = field(default_factory=list)
    after_flags: list[str] = field(default_factory=list)
    before_fill: float = 0.0
    after_fill: float = 0.0
    change_type: str = ""  # improved, regressed, unchanged, new, removed


@dataclass
class DiffReport:
    """Complete diff between two runs."""

    before_count: int = 0
    after_count: int = 0
    compared: int = 0
    improved: list[ImageDelta] = field(default_factory=list)
    regressed: list[ImageDelta] = field(default_factory=list)
    unchanged_ok: int = 0
    unchanged_bad: int = 0
    new_images: list[ImageDelta] = field(default_factory=list)
    removed_images: list[ImageDelta] = field(default_factory=list)
    # Per-category
    category_deltas: dict[str, dict] = field(default_factory=dict)
    # Flag changes
    flags_added: Counter = field(default_factory=Counter)
    flags_removed: Counter = field(default_factory=Counter)
    # Fill ratio
    fill_ratio_changes: list[tuple[str, float, float]] = field(default_factory=list)


def compute_diff(before: dict, after: dict) -> DiffReport:
    """Compare two results dicts and compute the diff report."""
    report = DiffReport(
        before_count=len(before),
        after_count=len(after),
    )

    all_names = set(before.keys()) | set(after.keys())

    # Per-category accumulators
    cat_before: dict[str, dict] = defaultdict(lambda: {"ok": 0, "bad": 0, "total": 0})
    cat_after: dict[str, dict] = defaultdict(lambda: {"ok": 0, "bad": 0, "total": 0})

    for name in sorted(all_names):
        b = before.get(name)
        a = after.get(name)

        if b is None:
            # New image in after
            delta = ImageDelta(
                name=name,
                category=a.get("category", ""),
                after_status=a.get("status", ""),
                after_flags=a.get("flags", []),
                after_fill=a.get("fill_ratio", 0.0),
                change_type="new",
            )
            report.new_images.append(delta)
            cat = a.get("category", "UNKNOWN")
            cat_after[cat]["total"] += 1
            if a.get("status", "") in _OK_STATUSES:
                cat_after[cat]["ok"] += 1
            else:
                cat_after[cat]["bad"] += 1
            continue

        if a is None:
            # Image removed in after
            delta = ImageDelta(
                name=name,
                category=b.get("category", ""),
                before_status=b.get("status", ""),
                before_flags=b.get("flags", []),
                before_fill=b.get("fill_ratio", 0.0),
                change_type="removed",
            )
            report.removed_images.append(delta)
            cat = b.get("category", "UNKNOWN")
            cat_before[cat]["total"] += 1
            if b.get("status", "") in _OK_STATUSES:
                cat_before[cat]["ok"] += 1
            else:
                cat_before[cat]["bad"] += 1
            continue

        # Both exist — compare
        report.compared += 1
        b_status = b.get("status", "")
        a_status = a.get("status", "")
        b_ok = b_status in _OK_STATUSES
        a_ok = a_status in _OK_STATUSES
        cat = a.get("category", "") or b.get("category", "UNKNOWN")

        # Category stats
        cat_before[cat]["total"] += 1
        cat_after[cat]["total"] += 1
        if b_ok:
            cat_before[cat]["ok"] += 1
        else:
            cat_before[cat]["bad"] += 1
        if a_ok:
            cat_after[cat]["ok"] += 1
        else:
            cat_after[cat]["bad"] += 1

        delta = ImageDelta(
            name=name,
            category=cat,
            before_status=b_status,
            after_status=a_status,
            before_flags=b.get("flags", []),
            after_flags=a.get("flags", []),
            before_fill=b.get("fill_ratio", 0.0),
            after_fill=a.get("fill_ratio", 0.0),
        )

        if not b_ok and a_ok:
            delta.change_type = "improved"
            report.improved.append(delta)
        elif b_ok and not a_ok:
            delta.change_type = "regressed"
            report.regressed.append(delta)
        elif a_ok:
            report.unchanged_ok += 1
        else:
            report.unchanged_bad += 1

        # Flag tracking
        b_flags = set(b.get("flags", []))
        a_flags = set(a.get("flags", []))
        for f in a_flags - b_flags:
            report.flags_added[f] += 1
        for f in b_flags - a_flags:
            report.flags_removed[f] += 1

        # Fill ratio change tracking
        bf = b.get("fill_ratio", 0.0)
        af = a.get("fill_ratio", 0.0)
        if bf > 0 and af > 0 and abs(bf - af) > 0.01:
            report.fill_ratio_changes.append((name, bf, af))

    # Build category deltas
    all_cats = set(cat_before.keys()) | set(cat_after.keys())
    for cat in sorted(all_cats):
        cb = cat_before[cat]
        ca = cat_after[cat]
        b_rate = cb["ok"] / max(1, cb["total"])
        a_rate = ca["ok"] / max(1, ca["total"])
        improved_in_cat = sum(1 for d in report.improved if d.category == cat)
        regressed_in_cat = sum(1 for d in report.regressed if d.category == cat)
        report.category_deltas[cat] = {
            "before_total": cb["total"],
            "before_ok": cb["ok"],
            "before_rate": round(b_rate, 4),
            "after_total": ca["total"],
            "after_ok": ca["ok"],
            "after_rate": round(a_rate, 4),
            "delta_pct": round((a_rate - b_rate) * 100, 1),
            "improved": improved_in_cat,
            "regressed": regressed_in_cat,
        }

    return report


def format_console(report: DiffReport) -> str:
    """Format diff report as console text."""
    lines = [
        "=" * 60,
        "DIFF REPORT",
        "=" * 60,
        f"  Before: {report.before_count} images",
        f"  After:  {report.after_count} images",
        f"  Compared: {report.compared}",
        "",
        f"  Improved:      {len(report.improved):4d}  (flagged/failed → ok/recovered)",
        f"  Regressed:     {len(report.regressed):4d}  (ok/recovered → flagged/failed)",
        f"  Unchanged OK:  {report.unchanged_ok:4d}",
        f"  Unchanged bad: {report.unchanged_bad:4d}",
    ]

    if report.new_images:
        lines.append(f"  New images:    {len(report.new_images):4d}")
    if report.removed_images:
        lines.append(f"  Removed:       {len(report.removed_images):4d}")

    # Category deltas
    lines.append("")
    lines.append("BY CATEGORY:")
    for cat, d in sorted(report.category_deltas.items()):
        delta = d["delta_pct"]
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "="
        lines.append(
            f"  {cat:25s}  {d['before_rate']:.0%} → {d['after_rate']:.0%}  "
            f"({arrow}{abs(delta):.1f}%)  "
            f"[+{d['improved']} improved, -{d['regressed']} regressed]"
        )

    # Flag changes
    if report.flags_added or report.flags_removed:
        lines.append("")
        lines.append("FLAG CHANGES:")
        for flag, count in report.flags_added.most_common():
            lines.append(f"  NEW    {flag:40s}  +{count}")
        for flag, count in report.flags_removed.most_common():
            lines.append(f"  GONE   {flag:40s}  -{count}")

    # Improved details
    if report.improved:
        lines.append("")
        lines.append(f"IMPROVED ({len(report.improved)}):")
        for d in report.improved[:30]:
            before_flags = ", ".join(d.before_flags) if d.before_flags else "—"
            lines.append(f"  {d.name:40s}  {d.category:18s}  was: {before_flags}")

    # Regressed details
    if report.regressed:
        lines.append("")
        lines.append(f"REGRESSED ({len(report.regressed)}):")
        for d in report.regressed[:30]:
            after_flags = ", ".join(d.after_flags) if d.after_flags else "—"
            lines.append(f"  {d.name:40s}  {d.category:18s}  now: {after_flags}")

    # Fill ratio changes
    big_changes = [(n, b, a) for n, b, a in report.fill_ratio_changes if abs(b - a) > 0.05]
    if big_changes:
        lines.append("")
        lines.append(f"FILL RATIO CHANGES (>{5}%, showing {min(20, len(big_changes))}):")
        for name, bf, af in sorted(big_changes, key=lambda x: abs(x[1] - x[2]), reverse=True)[:20]:
            arrow = "↑" if af > bf else "↓"
            lines.append(f"  {name:40s}  {bf:.3f} → {af:.3f}  ({arrow}{abs(af-bf):.3f})")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_html(report: DiffReport) -> str:
    """Format diff report as standalone HTML."""
    improved_rows = ""
    for d in report.improved:
        before_flags = ", ".join(d.before_flags) or "—"
        improved_rows += (
            f"<tr><td>{d.name}</td><td>{d.category}</td>"
            f"<td>{d.before_status}</td><td>{d.after_status}</td>"
            f"<td>{before_flags}</td></tr>\n"
        )

    regressed_rows = ""
    for d in report.regressed:
        after_flags = ", ".join(d.after_flags) or "—"
        regressed_rows += (
            f"<tr class='regressed'><td>{d.name}</td><td>{d.category}</td>"
            f"<td>{d.before_status}</td><td>{d.after_status}</td>"
            f"<td>{after_flags}</td></tr>\n"
        )

    cat_rows = ""
    for cat, cd in sorted(report.category_deltas.items()):
        delta = cd["delta_pct"]
        cls = "improved" if delta > 0 else "regressed" if delta < 0 else ""
        cat_rows += (
            f"<tr class='{cls}'><td>{cat}</td>"
            f"<td>{cd['before_rate']:.0%}</td><td>{cd['after_rate']:.0%}</td>"
            f"<td>{delta:+.1f}%</td>"
            f"<td>+{cd['improved']}</td><td>-{cd['regressed']}</td></tr>\n"
        )

    flag_rows = ""
    for flag, count in report.flags_added.most_common():
        flag_rows += f"<tr><td>NEW</td><td>{flag}</td><td>+{count}</td></tr>\n"
    for flag, count in report.flags_removed.most_common():
        flag_rows += f"<tr><td>GONE</td><td>{flag}</td><td>-{count}</td></tr>\n"

    net_change = len(report.improved) - len(report.regressed)
    net_class = "improved" if net_change > 0 else "regressed" if net_change < 0 else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Diff Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         margin: 2rem; background: #fafafa; color: #333; }}
  h1 {{ color: #1a1a1a; }}
  h2 {{ color: #555; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
  th {{ background: #f0f0f0; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  tr.improved {{ background: #e8f5e9; }}
  tr.regressed {{ background: #ffebee; }}
  .summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1rem; margin: 1rem 0;
  }}
  .card {{
    background: white; border: 1px solid #ddd;
    border-radius: 6px; padding: 1rem; text-align: center;
  }}
  .card .num {{ font-size: 2rem; font-weight: bold; }}
  .card .label {{ color: #666; font-size: 0.85rem; }}
  .green {{ color: #2e7d32; }}
  .red {{ color: #c62828; }}
  .blue {{ color: #1565c0; }}
</style>
</head>
<body>
<h1>Diff Report</h1>

<div class="summary">
  <div class="card"><div class="num blue">{report.compared}</div><div class="label">Compared</div></div>
  <div class="card"><div class="num green">{len(report.improved)}</div><div class="label">Improved</div></div>
  <div class="card"><div class="num red">{len(report.regressed)}</div><div class="label">Regressed</div></div>
  <div class="card"><div class="num {net_class}">{net_change:+d}</div><div class="label">Net change</div></div>
  <div class="card"><div class="num">{report.unchanged_ok}</div><div class="label">Unchanged OK</div></div>
  <div class="card"><div class="num">{report.unchanged_bad}</div><div class="label">Unchanged bad</div></div>
</div>

<h2>By Category</h2>
<table>
<tr><th>Category</th><th>Before</th><th>After</th><th>Delta</th><th>Improved</th><th>Regressed</th></tr>
{cat_rows}</table>

<h2>Flag Changes</h2>
<table>
<tr><th>Change</th><th>Flag</th><th>Count</th></tr>
{flag_rows}</table>

{"<h2>Improved (" + str(len(report.improved)) + ")</h2>" if report.improved else ""}
{"<table><tr><th>File</th><th>Category</th><th>Before</th><th>After</th><th>Was flagged for</th></tr>" + improved_rows + "</table>" if report.improved else ""}

{"<h2>Regressed (" + str(len(report.regressed)) + ")</h2>" if report.regressed else ""}
{"<table><tr><th>File</th><th>Category</th><th>Before</th><th>After</th><th>Now flagged for</th></tr>" + regressed_rows + "</table>" if report.regressed else ""}

</body>
</html>"""


# -- CLI --

@app.command()
def main(
    before: Path = typer.Option(
        ..., "--before", "-b", help="Results JSON from the first (baseline) run"
    ),
    after: Path = typer.Option(
        ..., "--after", "-a", help="Results JSON from the second (new) run"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="HTML diff report output path"
    ),
) -> None:
    """Compare two pipeline runs and report improvements/regressions."""
    if not before.exists():
        typer.echo(f"Error: {before} not found", err=True)
        raise typer.Exit(1)
    if not after.exists():
        typer.echo(f"Error: {after} not found", err=True)
        raise typer.Exit(1)

    with open(before, "r") as f:
        before_data = json.load(f)
    with open(after, "r") as f:
        after_data = json.load(f)

    report = compute_diff(before_data, after_data)

    # Console output always
    typer.echo(format_console(report))

    # Optional HTML
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(format_html(report), encoding="utf-8")
        typer.echo(f"\nHTML report written to {output}")


if __name__ == "__main__":
    app()
