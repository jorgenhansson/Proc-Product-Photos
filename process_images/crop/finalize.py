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

    # Expand bbox with category-aware margins (0 if zero-margin mode)
    expanded = expand_bbox(object_bbox, w, h, cat_config)

    # Guard against degenerate crop region (e.g. bbox fully outside image)
    if expanded.w < 1 or expanded.h < 1:
        flags = list(flags)
        flags.append(Flag.NO_OBJECT_FOUND)
        return CropResult(
            mask=mask,
            object_bbox=object_bbox,
            flags=flags,
            background_type=background_type,
            metrics=CropMetrics(),
        )

    # Crop — preserve all channels (including alpha for transparent bg)
    cropped = crop_region(image, expanded)

    # Resize — use target_fill_ratio_max so the product fills the canvas
    # as much as possible.  In zero-margin mode (max=1.0) the longest
    # dimension matches canvas size exactly.
    target_fill = cat_config.target_fill_ratio_max
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
        margin_px=max(
            object_bbox.x - expanded.x,                        # left margin
            object_bbox.y - expanded.y,                        # top margin
            (expanded.x + expanded.w) - (object_bbox.x + object_bbox.w),  # right
            (expanded.y + expanded.h) - (object_bbox.y + object_bbox.h),  # bottom
            0,
        ),
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
    """Expand bounding box using asymmetric, category-aware margin rules.

    Each side (top, bottom, left, right) can have its own margin
    percentage.  The reference dimension depends on margin_mode:
    - "image": percentage of max(img_w, img_h)  — consistent whitespace
    - "object": percentage of the object bbox dimension on that axis

    For thin objects (clubs), the narrow dimension gets extra margin
    to prevent the object from becoming a 1px line after resize.
    """
    m_top, m_bottom, m_left, m_right = config.resolve_margins()

    if config.margin_mode == "object":
        ref_x = bbox.w
        ref_y = bbox.h
    else:  # "image" (default)
        ref_x = max(img_w, img_h)
        ref_y = ref_x

    px_top = int(ref_y * m_top)
    px_bottom = int(ref_y * m_bottom)
    px_left = int(ref_x * m_left)
    px_right = int(ref_x * m_right)

    is_thin = config.thin_object_protection and detect_thin_object(
        bbox, threshold=3.0
    )

    # Thin-object protection: boost margin in the narrow dimension
    if is_thin:
        if bbox.w < bbox.h:
            px_left = int(px_left * 1.5)
            px_right = int(px_right * 1.5)
        else:
            px_top = int(px_top * 1.5)
            px_bottom = int(px_bottom * 1.5)

    expanded = BBox(
        x=bbox.x - px_left,
        y=bbox.y - px_top,
        w=bbox.w + px_left + px_right,
        h=bbox.h + px_top + px_bottom,
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
