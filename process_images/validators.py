"""Post-crop validation: sanity checks independent of crop strategy.

All checks run independently and return additional flags.  The validator
does not modify the CropResult -- it only inspects it.
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
) -> list[Flag]:
    """Run all validation checks on a crop result.

    Returns a list of *additional* flags not already in result.flags.
    """
    cat_config = resolve_category(context.category, config.categories)
    gc = config.global_config
    flags: list[Flag] = []

    h, w = image_shape[:2]

    # -- Mask size --
    if result.mask is not None:
        mask_ratio = np.count_nonzero(result.mask) / max(1, h * w)
        if mask_ratio < gc.min_object_ratio:
            flags.append(Flag.MASK_TOO_SMALL)

    # -- Bounding box (check object extent, not the expanded crop region) --
    if result.object_bbox is not None:
        bbox_ratio = result.object_bbox.area / max(1, h * w)
        if bbox_ratio > gc.max_bbox_ratio:
            flags.append(Flag.BBOX_TOO_LARGE)
        if bbox_ratio < gc.min_bbox_ratio:
            flags.append(Flag.BBOX_TOO_SMALL)

    # -- Fill ratio --
    if result.metrics.fill_ratio > 0:
        if result.metrics.fill_ratio < cat_config.target_fill_ratio_min:
            flags.append(Flag.FILL_RATIO_TOO_LOW)
        if result.metrics.fill_ratio > cat_config.target_fill_ratio_max:
            flags.append(Flag.FILL_RATIO_TOO_HIGH)

    # -- Edge proximity on final canvas --
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
                    prox = cat_config.edge_proximity_px
                    if (
                        row_idx[0] < prox
                        or row_idx[-1] > canvas_h - prox - 1
                        or col_idx[0] < prox
                        or col_idx[-1] > canvas_w - prox - 1
                    ):
                        flags.append(Flag.OBJECT_TOO_CLOSE_TO_EDGE)

    # -- Category consistency --
    if result.metrics.fill_ratio > 0:
        expected_mid = (
            cat_config.target_fill_ratio_min + cat_config.target_fill_ratio_max
        ) / 2
        deviation = abs(result.metrics.fill_ratio - expected_mid) / max(
            0.01, expected_mid
        )
        if deviation > 0.5:
            flags.append(Flag.CROP_CATEGORY_INCONSISTENT)

    # Deduplicate against flags already in the result
    existing = set(result.flags)
    return [f for f in dict.fromkeys(flags) if f not in existing]
