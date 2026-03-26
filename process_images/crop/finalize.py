"""Shared post-mask finalization: expand bbox, crop, resize, canvas, metrics.

Used by both ClassicalCropStrategy and AIFallbackCropStrategy to avoid
duplicating the crop-to-canvas pipeline.  All category-aware margin
logic (thin-object protection, min-narrow enforcement) lives here.
"""

from __future__ import annotations

import numpy as np

from ..config import CategoryConfig, GlobalConfig
from ..models import (
    BackgroundType,
    BBox,
    CropMetrics,
    CropResult,
    Flag,
)
from .canvas import compute_fill_ratio, crop_region, place_on_canvas, resize_to_fit
from .morphology import detect_thin_object


def finalize_crop(
    image: np.ndarray,
    mask: np.ndarray,
    object_bbox: BBox,
    background_type: BackgroundType,
    flags: list[Flag],
    cat_config: CategoryConfig,
    global_config: GlobalConfig,
    object_pixel_count: int,
    component_count: int,
) -> CropResult:
    """Expand bbox, crop, resize, place on canvas, compute metrics.

    This is the shared final stage after a strategy has produced a
    mask and object bounding box.  All category-aware margin rules
    (thin-object protection, min-narrow enforcement) are applied here.

    Args:
        image: Full source image (RGB or RGBA).
        mask: Binary foreground mask.
        object_bbox: Raw detected object bounding box.
        background_type: Detected background type.
        flags: Flags accumulated so far (not modified, copied into result).
        cat_config: Category-specific configuration.
        global_config: Global pipeline configuration.
        object_pixel_count: Number of foreground pixels in mask.
        component_count: Number of significant connected components.

    Returns:
        Complete CropResult with cropped image, final canvas image, and metrics.
    """
    h, w = image.shape[:2]
    gc = global_config

    # Expand bbox with category-aware margins
    expanded = expand_bbox(object_bbox, w, h, cat_config)

    # Crop — preserve all channels (including alpha for transparent bg)
    cropped = crop_region(image, expanded)

    # Resize
    target_fill = (
        cat_config.target_fill_ratio_min + cat_config.target_fill_ratio_max
    ) / 2
    resized = resize_to_fit(
        cropped, gc.canvas_size, target_fill, cat_config.min_output_px
    )

    # Place on canvas
    final = place_on_canvas(
        resized,
        gc.canvas_size,
        gc.background_color,
        cat_config.centering_bias_x,
        cat_config.centering_bias_y,
    )

    # Compute metrics
    fill = compute_fill_ratio(
        (resized.shape[1], resized.shape[0]), gc.canvas_size
    )

    metrics = CropMetrics(
        fill_ratio=fill,
        crop_area_ratio=expanded.area / max(1, h * w),
        margin_px=int(cat_config.margin_pct * max(w, h)),
        object_bbox=object_bbox,
        crop_bbox=expanded,
        object_pixel_count=object_pixel_count,
        component_count=component_count,
    )

    return CropResult(
        mask=mask,
        object_bbox=object_bbox,
        crop_bbox=expanded,
        cropped_image=cropped,
        final_image=final,
        metrics=metrics,
        flags=list(flags),
        background_type=background_type,
    )


def expand_bbox(
    bbox: BBox,
    img_w: int,
    img_h: int,
    config: CategoryConfig,
) -> BBox:
    """Expand bounding box using category-aware margin rules.

    Margins are relative to image dimensions (not object dimensions)
    to ensure consistent professional whitespace regardless of how
    large the detected object is.
    """
    img_max = max(img_w, img_h)
    margin_px = int(img_max * config.margin_pct)
    margin_x = margin_px
    margin_y = margin_px

    is_thin = config.thin_object_protection and detect_thin_object(
        bbox, threshold=3.0
    )

    # Thin-object protection: extra margin in the narrow dimension
    if is_thin:
        if bbox.w < bbox.h:
            margin_x = int(margin_x * 1.5)
        else:
            margin_y = int(margin_y * 1.5)

    expanded = BBox(
        x=bbox.x - margin_x,
        y=bbox.y - margin_y,
        w=bbox.w + 2 * margin_x,
        h=bbox.h + 2 * margin_y,
    )

    # Enforce minimum narrow dimension for thin objects so the
    # product remains visible after resize (not a 1px line)
    if is_thin:
        min_narrow = max(bbox.w, bbox.h) // 4
        if expanded.w < min_narrow:
            extra = (min_narrow - expanded.w) // 2
            expanded = BBox(
                expanded.x - extra,
                expanded.y,
                expanded.w + 2 * extra,
                expanded.h,
            )
        elif expanded.h < min_narrow:
            extra = (min_narrow - expanded.h) // 2
            expanded = BBox(
                expanded.x,
                expanded.y - extra,
                expanded.w,
                expanded.h + 2 * extra,
            )

    return expanded.clamp(img_w, img_h)
