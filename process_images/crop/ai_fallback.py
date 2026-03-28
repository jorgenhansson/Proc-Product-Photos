"""AI/heuristic fallback crop strategy for flagged images.

Flag-aware dispatch: instead of always running GrabCut, the fallback
selects a recovery strategy based on *why* the primary pipeline failed:

- CROP_CATEGORY_INCONSISTENT (alone): re-validate with relaxed tolerance
  — the crop was likely fine, the validator was too strict.
- MASK_TOO_SMALL / NO_OBJECT_FOUND: try edge-enhanced masking and/or
  lower thresholds — the mask generation missed the object.
- Other flags: GrabCut refinement using the classical mask as prior.

EXTENSION POINT: to plug in a real segmentation model or API, either:
  1. Subclass and override ``_grabcut_crop()``, or
  2. Pass an ``external_provider`` CropStrategy to ``__init__``.
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
from .categories import resolve_category
from .finalize import finalize_crop
from .masks import mask_from_white_bg, mask_from_white_bg_edge_enhanced, rgb_to_lab
from .morphology import (
    clean_mask,
    compute_bbox,
    find_main_component,
    merge_collinear_components,
)

logger = logging.getLogger(__name__)

# Flags that indicate the mask itself is the problem
_MASK_FAILURE_FLAGS = {
    Flag.MASK_TOO_SMALL,
    Flag.NO_OBJECT_FOUND,
    Flag.MASK_TOO_FRAGMENTED,
}

# Flags that indicate the crop result is fine but validation was too strict
_VALIDATION_ONLY_FLAGS = {
    Flag.CROP_CATEGORY_INCONSISTENT,
    Flag.FILL_RATIO_TOO_LOW,
    Flag.FILL_RATIO_TOO_HIGH,
    Flag.BBOX_TOO_SMALL,
}


class AIFallbackCropStrategy(CropStrategy):
    """Flag-aware heuristic fallback for images that failed classical crop.

    Attributes:
        _external: Optional external CropStrategy to delegate to instead
                   of the built-in approaches.
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
        return self._dispatch_by_flags(image, context, config)

    def _dispatch_by_flags(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> CropResult:
        """Select recovery strategy based on primary pipeline failure mode."""
        primary_flags = set(context.primary_flags)

        # Case 1: Only validation flags, no mask failure → primary crop was
        # likely fine. Return it directly — the pipeline will re-validate
        # with relaxed tolerance.
        if primary_flags and primary_flags.issubset(_VALIDATION_ONLY_FLAGS):
            if context.primary_result is not None and context.primary_result.final_image is not None:
                logger.debug(
                    "Fallback: re-using primary result for %s (validation-only flags)",
                    context.source_path,
                )
                # Return the primary result with no flags — let the relaxed
                # re-validation decide if it passes
                return CropResult(
                    mask=context.primary_result.mask,
                    object_bbox=context.primary_result.object_bbox,
                    crop_bbox=context.primary_result.crop_bbox,
                    cropped_image=context.primary_result.cropped_image,
                    final_image=context.primary_result.final_image,
                    metrics=context.primary_result.metrics,
                    flags=[],  # clear flags — re-validation will re-check
                    background_type=context.primary_result.background_type,
                )

        # Case 2: Mask failure → try different mask strategies
        if primary_flags & _MASK_FAILURE_FLAGS:
            result = self._remask_crop(image, context, config)
            if result is not None and not (set(result.flags) & _MASK_FAILURE_FLAGS):
                logger.debug(
                    "Fallback: edge-enhanced remask succeeded for %s",
                    context.source_path,
                )
                return result

        # Case 3: Default — GrabCut refinement with prior mask
        return self._grabcut_crop(image, context, config)

    def _remask_crop(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> Optional[CropResult]:
        """Retry mask generation with edge-enhanced strategy and lower thresholds.

        Returns None if this strategy is not applicable (e.g. non-white bg).
        """
        gc = config.global_config
        cat_config = resolve_category(context.category, config.categories)
        bg_type = context.background_type

        if bg_type != BackgroundType.WHITE_BG:
            return None

        rgb = image[:, :, :3]
        flags: list[Flag] = []

        # Pre-compute LAB once for the remask attempt
        lab = rgb_to_lab(rgb)

        # Try edge-enhanced mask with extra-low threshold
        extra_bias = cat_config.threshold_bias - 4.0  # even more sensitive
        mask = mask_from_white_bg_edge_enhanced(
            rgb,
            gc.white_distance_threshold,
            extra_bias,
            canny_low=20,
            canny_high=80,
            dilate_iterations=4,
            precomputed_lab=lab,
        )

        # Clean mask
        mask = clean_mask(
            mask,
            kernel_size=cat_config.morph_kernel_size,
            iterations=cat_config.morph_iterations,
            skip_open=cat_config.thin_object_protection,
        )

        # Find main component
        filtered, sig_count = find_main_component(
            mask, cat_config.min_component_size
        )

        if sig_count > 1 and cat_config.thin_object_protection:
            merged = merge_collinear_components(mask, cat_config.min_component_size)
            if int(np.count_nonzero(merged)) > np.count_nonzero(filtered):
                filtered = merged
                sig_count = 1

        if sig_count > 1:
            flags.append(Flag.MULTIPLE_LARGE_COMPONENTS)

        bbox = compute_bbox(filtered)
        if bbox is None:
            return None  # still can't find anything

        return finalize_crop(
            image=image,
            mask=filtered,
            object_bbox=bbox,
            background_type=bg_type,
            flags=flags,
            cat_config=cat_config,
            global_config=gc,
            object_pixel_count=int(np.count_nonzero(filtered)),
            component_count=sig_count,
        )

    def _grabcut_crop(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> CropResult:
        """GrabCut-based refinement using classical mask as prior."""
        gc = config.global_config
        fc = config.fallback
        cat_config = resolve_category(context.category, config.categories)
        flags: list[Flag] = []

        rgb = image[:, :, :3].copy()
        h, w = rgb.shape[:2]

        # Cap GrabCut input to avoid hanging on large images.
        # GrabCut is O(n*m*k) and becomes unusable above ~1500px.
        max_gc_dim = 1200
        gc_scale = 1.0
        if max(h, w) > max_gc_dim:
            gc_scale = max_gc_dim / max(h, w)
            rgb = cv2.resize(rgb, None, fx=gc_scale, fy=gc_scale, interpolation=cv2.INTER_AREA)
            h, w = rgb.shape[:2]

        mask_gc = np.zeros((h, w), dtype=np.uint8)
        bgd_model = np.zeros((1, 65), dtype=np.float64)
        fgd_model = np.zeros((1, 65), dtype=np.float64)

        # Choose initialization: prefer prior mask from classical pipeline
        prior = context.prior_mask
        if prior is not None and gc_scale != 1.0:
            prior = cv2.resize(prior, (w, h), interpolation=cv2.INTER_NEAREST)
        if prior is not None and prior.shape == (h, w):
            mask_gc[prior == 255] = cv2.GC_PR_FGD
            mask_gc[prior == 0] = cv2.GC_PR_BGD

            prior_bbox = compute_bbox(prior)
            if prior_bbox is not None and prior_bbox.w > 4 and prior_bbox.h > 4:
                ix = prior_bbox.x + prior_bbox.w // 4
                iy = prior_bbox.y + prior_bbox.h // 4
                iw = prior_bbox.w // 2
                ih = prior_bbox.h // 2
                # Only mark as definite foreground where prior mask agrees.
                # Prevents background pixels inside hollow objects (donuts,
                # hollow letters) from being forced to GC_FGD (#24).
                inner_gc = mask_gc[iy : iy + ih, ix : ix + iw]
                inner_prior = prior[iy : iy + ih, ix : ix + iw]
                inner_gc[inner_prior == 255] = cv2.GC_FGD

            init_mode = cv2.GC_INIT_WITH_MASK
            rect = None
        else:
            inset = max(5, min(h, w) // 20)
            rect = (inset, inset, w - 2 * inset, h - 2 * inset)
            init_mode = cv2.GC_INIT_WITH_RECT

        try:
            cv2.grabCut(
                rgb, mask_gc, rect, bgd_model, fgd_model,
                fc.grabcut_iterations, init_mode,
            )
        except cv2.error as e:
            logger.warning("GrabCut failed for %s: %s", context.source_path, e)
            flags.append(Flag.NO_OBJECT_FOUND)
            return CropResult(
                flags=flags,
                background_type=BackgroundType.COMPLEX_BG,
            )

        fg_mask = np.where(
            (mask_gc == cv2.GC_FGD) | (mask_gc == cv2.GC_PR_FGD),
            255, 0,
        ).astype(np.uint8)

        # Scale min_component_size for downscaled image
        scaled_min_comp = max(1, int(cat_config.min_component_size * gc_scale * gc_scale))
        filtered, sig_count = find_main_component(fg_mask, scaled_min_comp)

        bbox = compute_bbox(filtered)
        if bbox is None:
            flags.append(Flag.NO_OBJECT_FOUND)
            return CropResult(
                mask=filtered, flags=flags,
                background_type=BackgroundType.COMPLEX_BG,
            )

        # Scale bbox and mask back to original image dimensions
        if gc_scale != 1.0:
            orig_h, orig_w = image.shape[:2]
            inv_scale = 1.0 / gc_scale
            bbox = BBox(
                x=int(bbox.x * inv_scale),
                y=int(bbox.y * inv_scale),
                w=int(bbox.w * inv_scale),
                h=int(bbox.h * inv_scale),
            ).clamp(orig_w, orig_h)
            filtered = cv2.resize(
                filtered, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST
            )

        bg_type = context.background_type or BackgroundType.COMPLEX_BG

        return finalize_crop(
            image=image,
            mask=filtered,
            object_bbox=bbox,
            background_type=bg_type,
            flags=flags,
            cat_config=cat_config,
            global_config=gc,
            object_pixel_count=int(np.count_nonzero(filtered)),
            component_count=sig_count,
        )
