"""AI/heuristic fallback crop strategy for flagged images.

Default implementation uses OpenCV GrabCut as a stronger-than-threshold
heuristic.  This is explicitly *not* presented as a real AI solution --
it is a practical refinement step.

EXTENSION POINT: to plug in a real segmentation model or API, either:
  1. Subclass AIFallbackCropStrategy and override ``_segment()``, or
  2. Pass an ``external_provider`` CropStrategy to ``__init__`` which
     will be used instead of GrabCut.
"""

from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from ..config import PipelineConfig
from ..models import (
    BackgroundType,
    BBox,
    CropMetrics,
    CropResult,
    Flag,
    ImageContext,
)
from .base import CropStrategy
from .canvas import compute_fill_ratio, crop_region, place_on_canvas, resize_to_fit
from .categories import resolve_category
from .morphology import compute_bbox, find_main_component

logger = logging.getLogger(__name__)


class AIFallbackCropStrategy(CropStrategy):
    """GrabCut-based heuristic fallback for images that failed classical crop.

    Attributes:
        _external: Optional external CropStrategy to delegate to instead
                   of the built-in GrabCut approach.
    """

    def __init__(
        self, external_provider: Optional[CropStrategy] = None
    ) -> None:
        self._external = external_provider

    def crop(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> CropResult:
        if self._external is not None:
            return self._external.crop(image, context, config)
        return self._grabcut_crop(image, context, config)

    def _grabcut_crop(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> CropResult:
        gc = config.global_config
        fc = config.fallback
        cat_config = resolve_category(context.category, config.categories)
        flags: list[Flag] = []

        rgb = image[:, :, :3].copy()
        h, w = rgb.shape[:2]

        mask_gc = np.zeros((h, w), dtype=np.uint8)
        bgd_model = np.zeros((1, 65), dtype=np.float64)
        fgd_model = np.zeros((1, 65), dtype=np.float64)

        # Choose initialization mode: prefer prior mask from classical
        # pipeline over a generic rectangle.
        prior = context.prior_mask
        if prior is not None and prior.shape == (h, w):
            # Seed GrabCut with the classical mask as probable fg/bg
            mask_gc[prior == 255] = cv2.GC_PR_FGD
            mask_gc[prior == 0] = cv2.GC_PR_BGD
            init_mode = cv2.GC_INIT_WITH_MASK
            rect = None
            logger.debug(
                "GrabCut init from prior mask for %s", context.source_path
            )
        else:
            # Fallback: generic inset rectangle
            inset = max(5, min(h, w) // 20)
            rect = (inset, inset, w - 2 * inset, h - 2 * inset)
            init_mode = cv2.GC_INIT_WITH_RECT

        try:
            cv2.grabCut(
                rgb,
                mask_gc,
                rect,
                bgd_model,
                fgd_model,
                fc.grabcut_iterations,
                init_mode,
            )
        except cv2.error as e:
            logger.warning("GrabCut failed for %s: %s", context.source_path, e)
            flags.append(Flag.NO_OBJECT_FOUND)
            return CropResult(
                flags=flags,
                background_type=BackgroundType.COMPLEX_BG,
            )

        # Extract foreground mask
        fg_mask = np.where(
            (mask_gc == cv2.GC_FGD) | (mask_gc == cv2.GC_PR_FGD),
            255,
            0,
        ).astype(np.uint8)

        # Find main component
        filtered, sig_count = find_main_component(
            fg_mask, cat_config.min_component_size
        )

        bbox = compute_bbox(filtered)
        if bbox is None:
            flags.append(Flag.NO_OBJECT_FOUND)
            return CropResult(
                mask=filtered,
                flags=flags,
                background_type=BackgroundType.COMPLEX_BG,
            )

        # Expand bbox with category margin (image-relative, not object-relative)
        img_max = max(w, h)
        mx = int(img_max * cat_config.margin_pct)
        my = mx
        expanded = BBox(
            x=bbox.x - mx,
            y=bbox.y - my,
            w=bbox.w + 2 * mx,
            h=bbox.h + 2 * my,
        ).clamp(w, h)

        # Crop, resize, place on canvas
        cropped = crop_region(rgb, expanded)
        target_fill = (
            cat_config.target_fill_ratio_min + cat_config.target_fill_ratio_max
        ) / 2
        resized = resize_to_fit(
            cropped, gc.canvas_size, target_fill, cat_config.min_output_px
        )
        final = place_on_canvas(
            resized,
            gc.canvas_size,
            gc.background_color,
            cat_config.centering_bias_x,
            cat_config.centering_bias_y,
        )

        fill = compute_fill_ratio(
            (resized.shape[1], resized.shape[0]), gc.canvas_size
        )

        metrics = CropMetrics(
            fill_ratio=fill,
            crop_area_ratio=expanded.area / max(1, h * w),
            margin_px=max(mx, my),
            object_bbox=bbox,
            crop_bbox=expanded,
            object_pixel_count=int(np.count_nonzero(filtered)),
            component_count=sig_count,
        )

        bg_type = context.background_type or BackgroundType.COMPLEX_BG

        return CropResult(
            mask=filtered,
            object_bbox=bbox,
            crop_bbox=expanded,
            cropped_image=cropped,
            final_image=final,
            metrics=metrics,
            flags=flags,
            background_type=bg_type,
        )
