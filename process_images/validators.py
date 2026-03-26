"""Post-crop validation: sanity checks independent of crop strategy.

All checks run independently and return additional flags.  The validator
does not modify the CropResult -- it only inspects it.

The ``tolerance`` parameter (default 1.0) relaxes thresholds for
fallback validation.  A value of 0.8 widens acceptable fill-ratio
range by 20%, loosens bbox limits, and increases edge proximity
tolerance — giving the fallback a better chance of recovering images
that just barely missed primary thresholds.
"""

from __future__ import annotations

import numpy as np

from .config import PipelineConfig
from .crop.categories import resolve_category
from .models import CropResult, Flag, ImageContext


def validate_crop_result(
    result: CropResult,
    image_shape: tuple[int, ...],
    context: ImageContext,
    config: PipelineConfig,
    tolerance: float = 1.0,
) -> list[Flag]:
    """Run all validation checks on a crop result.

    Args:
        tolerance: Threshold relaxation factor (0..1].  1.0 = strict
            (primary pipeline), <1.0 = relaxed (fallback).  Affects
            fill ratio range, bbox limits, edge proximity, and
            category consistency.

    Returns a list of *additional* flags not already in result.flags.
    """
    cat_config = resolve_category(context.category, config.categories)
    gc = config.global_config
    flags: list[Flag] = []

    h, w = image_shape[:2]

    # -- Mask size (tolerance relaxes the minimum) --
    if result.mask is not None:
        mask_ratio = np.count_nonzero(result.mask) / max(1, h * w)
        effective_min_object = gc.min_object_ratio * tolerance
        if mask_ratio < effective_min_object:
            flags.append(Flag.MASK_TOO_SMALL)

    # -- Bounding box (check object extent, not the expanded crop region) --
    if result.object_bbox is not None:
        bbox_ratio = result.object_bbox.area / max(1, h * w)
        if bbox_ratio > gc.max_bbox_ratio:
            flags.append(Flag.BBOX_TOO_LARGE)
        effective_min_bbox = gc.min_bbox_ratio * tolerance
        if bbox_ratio < effective_min_bbox:
            flags.append(Flag.BBOX_TOO_SMALL)

    # -- Fill ratio (tolerance widens the acceptable range) --
    if result.metrics.fill_ratio > 0:
        effective_fill_min = cat_config.target_fill_ratio_min * tolerance
        effective_fill_max = min(
            1.0, cat_config.target_fill_ratio_max / tolerance
        )
        if result.metrics.fill_ratio < effective_fill_min:
            flags.append(Flag.FILL_RATIO_TOO_LOW)
        if result.metrics.fill_ratio > effective_fill_max:
            flags.append(Flag.FILL_RATIO_TOO_HIGH)

    # -- Edge proximity on final canvas (tolerance increases allowed proximity) --
    if result.final_image is not None:
        canvas_h, canvas_w = result.final_image.shape[:2]
        if result.final_image.ndim == 3:
            bg = np.array(gc.background_color, dtype=np.uint8)
            non_bg = np.any(result.final_image != bg, axis=2)
            if np.any(non_bg):
                rows = np.any(non_bg, axis=1)
                cols = np.any(non_bg, axis=0)
                row_idx = np.where(rows)[0]
                col_idx = np.where(cols)[0]
                if len(row_idx) > 0 and len(col_idx) > 0:
                    prox = max(
                        1, int(cat_config.edge_proximity_px * tolerance)
                    )
                    if (
                        row_idx[0] < prox
                        or row_idx[-1] > canvas_h - prox - 1
                        or col_idx[0] < prox
                        or col_idx[-1] > canvas_w - prox - 1
                    ):
                        flags.append(Flag.OBJECT_TOO_CLOSE_TO_EDGE)

    # -- Category consistency (tolerance widens acceptable deviation) --
    if result.metrics.fill_ratio > 0:
        expected_mid = (
            cat_config.target_fill_ratio_min + cat_config.target_fill_ratio_max
        ) / 2
        deviation = abs(result.metrics.fill_ratio - expected_mid) / max(
            0.01, expected_mid
        )
        consistency_threshold = 0.5 / tolerance  # relaxed = higher allowed deviation
        if deviation > consistency_threshold:
            flags.append(Flag.CROP_CATEGORY_INCONSISTENT)

    # Deduplicate against flags already in the result
    existing = set(result.flags)
    return [f for f in dict.fromkeys(flags) if f not in existing]
