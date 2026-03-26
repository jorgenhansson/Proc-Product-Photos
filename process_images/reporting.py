"""Review manifest generation, side-by-side previews, and HTML reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .models import CropMetrics, FLAG_DESCRIPTIONS, ProcessingResult, ProcessingStatus


def write_review_manifest(
    results: list[ProcessingResult],
    path: Path,
) -> None:
    """Write JSON manifest of all flagged/failed images for manual review."""
    review_items = []
    for r in results:
        if r.status in (ProcessingStatus.FLAGGED, ProcessingStatus.FAILED):
            item: dict[str, Any] = {
                "source": str(r.source_path),
                "source_size_bytes": r.source_size_bytes,
                "source_dimensions": list(r.source_dimensions),
                "category": r.category,
                "status": r.status.value,
                "proposed_outputs": r.proposed_filenames,
                "flags": [f.value for f in r.flags],
                "flag_descriptions": [
                    FLAG_DESCRIPTIONS.get(f, f.value) for f in r.flags
                ],
                "fallback_attempted": r.fallback_attempted,
                "fallback_succeeded": r.status == ProcessingStatus.RECOVERED,
                "error": r.error_message,
                "primary_metrics": _metrics_dict(r.crop_metrics),
                "fallback_metrics": _metrics_dict(r.fallback_metrics),
            }
            review_items.append(item)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(review_items, f, indent=2, ensure_ascii=False)


def _metrics_dict(metrics: Optional[CropMetrics]) -> Optional[dict]:
    """Convert CropMetrics to a JSON-serializable dict."""
    if metrics is None:
        return None
    return {
        "fill_ratio": round(metrics.fill_ratio, 4),
        "crop_area_ratio": round(metrics.crop_area_ratio, 4),
        "object_pixel_count": metrics.object_pixel_count,
        "component_count": metrics.component_count,
        "margin_px": metrics.margin_px,
    }


_PANEL_LABELS = ["Original", "Mask", "Cropped", "Final"]
_LABEL_HEIGHT = 20


def generate_side_by_side(
    original: np.ndarray,
    mask: Optional[np.ndarray],
    cropped: Optional[np.ndarray],
    final: Optional[np.ndarray],
    path: Path,
    panel_size: int = 250,
) -> None:
    """Generate a labeled 4-panel preview image (original | mask | cropped | final)."""
    panels: list[np.ndarray] = []

    for img in [original, mask, cropped, final]:
        if img is None:
            panel = np.full(
                (panel_size, panel_size, 3), 200, dtype=np.uint8
            )
        else:
            if img.ndim == 2:
                panel_img = np.stack([img, img, img], axis=2)
            elif img.shape[2] == 4:
                panel_img = img[:, :, :3]
            else:
                panel_img = img

            pil = Image.fromarray(panel_img)
            pil.thumbnail((panel_size, panel_size), Image.LANCZOS)
            panel = np.array(pil)

            ph, pw = panel.shape[:2]
            padded = np.full(
                (panel_size, panel_size, 3), 240, dtype=np.uint8
            )
            y_off = (panel_size - ph) // 2
            x_off = (panel_size - pw) // 2
            padded[y_off : y_off + ph, x_off : x_off + pw] = panel[:, :, :3]
            panel = padded

        panels.append(panel)

    combined = np.concatenate(panels, axis=1)

    # Add label strip at top
    total_w = combined.shape[1]
    label_strip = np.full((_LABEL_HEIGHT, total_w, 3), 255, dtype=np.uint8)
    label_img = Image.fromarray(label_strip)
    draw = ImageDraw.Draw(label_img)
    for i, label in enumerate(_PANEL_LABELS):
        x = i * panel_size + panel_size // 2
        draw.text((x, 2), label, fill=(0, 0, 0), anchor="mt")
    label_arr = np.array(label_img)
    combined = np.concatenate([label_arr, combined], axis=0)

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(combined).save(path, format="PNG")


def write_html_report(
    stats_dict: dict[str, Any],
    results: list[ProcessingResult],
    path: Path,
) -> None:
    """Write a standalone HTML report with summary and flagged items."""
    g = stats_dict["general"]

    flag_rows = ""
    for flag_name, count in sorted(stats_dict.get("by_flag", {}).items()):
        flag_rows += f"<tr><td>{flag_name}</td><td>{count}</td></tr>\n"

    cat_rows = ""
    for cat, counts in sorted(stats_dict.get("by_category", {}).items()):
        total = sum(counts.values())
        ok = counts.get("ok", 0) + counts.get("recovered", 0)
        flagged = counts.get("flagged", 0) + counts.get("failed", 0)
        cat_rows += (
            f"<tr><td>{cat}</td><td>{total}</td>"
            f"<td>{ok}</td><td>{flagged}</td></tr>\n"
        )

    review_rows = ""
    for r in results:
        if r.status in (ProcessingStatus.FLAGGED, ProcessingStatus.FAILED):
            flags_str = ", ".join(f.value for f in r.flags)
            fb = "yes" if r.fallback_attempted else "no"
            review_rows += (
                f"<tr><td>{r.source_path.name}</td><td>{r.category}</td>"
                f"<td>{r.status.value}</td><td>{flags_str}</td>"
                f"<td>{fb}</td></tr>\n"
            )

    q = stats_dict.get("quality", {})
    p = stats_dict.get("performance", {})

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Image Processing Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         margin: 2rem; background: #fafafa; color: #333; }}
  h1 {{ color: #1a1a1a; }}
  h2 {{ color: #555; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; }}
  th {{ background: #f0f0f0; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  .summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1rem; margin: 1rem 0;
  }}
  .card {{
    background: white; border: 1px solid #ddd;
    border-radius: 6px; padding: 1rem; text-align: center;
  }}
  .card .num {{ font-size: 2rem; font-weight: bold; color: #2563eb; }}
  .card .label {{ color: #666; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>Image Processing Report</h1>

<div class="summary">
  <div class="card"><div class="num">{g['total_discovered']}</div><div class="label">Discovered</div></div>
  <div class="card"><div class="num">{g['total_attempted']}</div><div class="label">Attempted</div></div>
  <div class="card"><div class="num">{g['total_ok']}</div><div class="label">OK</div></div>
  <div class="card"><div class="num">{g['total_recovered']}</div><div class="label">Recovered</div></div>
  <div class="card"><div class="num">{g['total_flagged']}</div><div class="label">Flagged</div></div>
  <div class="card"><div class="num">{g['total_failed']}</div><div class="label">Failed</div></div>
</div>

<h2>By Category</h2>
<table>
<tr><th>Category</th><th>Total</th><th>OK + Recovered</th><th>Flagged + Failed</th></tr>
{cat_rows}</table>

<h2>By Flag</h2>
<table>
<tr><th>Flag</th><th>Count</th></tr>
{flag_rows}</table>

<h2>Quality Metrics</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Avg fill ratio</td><td>{q.get('avg_fill_ratio', 0):.3f}</td></tr>
<tr><td>Min fill ratio</td><td>{q.get('min_fill_ratio', 0):.3f}</td></tr>
<tr><td>Max fill ratio</td><td>{q.get('max_fill_ratio', 0):.3f}</td></tr>
<tr><td>Avg crop area ratio</td><td>{q.get('avg_crop_area_ratio', 0):.3f}</td></tr>
</table>

<h2>Performance</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total time</td><td>{p.get('total_time_s', 0):.1f}s</td></tr>
<tr><td>Avg per image</td><td>{p.get('avg_per_image_s', 0):.3f}s</td></tr>
<tr><td>Fallback invocations</td><td>{p.get('fallback_invocation_count', 0)}</td></tr>
<tr><td>Fallback rate</td><td>{p.get('fallback_invocation_rate', 0):.1%}</td></tr>
</table>

<h2>Images for Review</h2>
<table>
<tr><th>File</th><th>Category</th><th>Status</th><th>Flags</th><th>Fallback</th></tr>
{review_rows}</table>

</body>
</html>"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
