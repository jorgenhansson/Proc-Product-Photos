"""Classical (deterministic) crop strategy -- the primary pipeline."""

from __future__ import annotations

import logging

import numpy as np

from ..config import CategoryConfig, PipelineConfig
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
from .masks import (
    detect_background_type,
    mask_from_alpha,
    mask_from_complex_bg,
    mask_from_white_bg,
)
from .morphology import (
    clean_mask,
    compute_bbox,
    detect_thin_object,
    find_main_component,
)

logger = logging.getLogger(__name__)


class ClassicalCropStrategy(CropStrategy):
    """Deterministic, classical image-processing crop strategy.

    Pipeline:
    1. Detect background type
    2. Generate binary mask
    3. Clean mask with morphology
    4. Find main connected component
    5. Compute bounding box
    6. Expand bbox with category-aware margins
    7. Crop, resize, place on canvas
    """

    def crop(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> CropResult:
        gc = config.global_config
        cat_config = resolve_category(context.category, config.categories)
        flags: list[Flag] = []

        h, w = image.shape[:2]

        # 1. Detect background type
        bg_type = detect_background_type(image, gc)

        # 2. Generate mask
        mask = self._generate_mask(image, bg_type, gc, cat_config)

        # 3. Clean mask
        mask = clean_mask(
            mask,
            kernel_size=cat_config.morph_kernel_size,
            iterations=cat_config.morph_iterations,
        )

        # 4. Find main component
        filtered_mask, sig_count = find_main_component(
            mask, cat_config.min_component_size
        )

        if sig_count > 1:
            flags.append(Flag.MULTIPLE_LARGE_COMPONENTS)
        if sig_count == 0:
            flags.append(Flag.MASK_TOO_FRAGMENTED)

        # 5. Compute bounding box
        bbox = compute_bbox(filtered_mask)
        if bbox is None:
            flags.append(Flag.NO_OBJECT_FOUND)
            return CropResult(
                mask=filtered_mask,
                flags=flags,
                background_type=bg_type,
                metrics=CropMetrics(),
            )

        # Mask-size check
        object_pixels = int(np.count_nonzero(filtered_mask))
        mask_ratio = object_pixels / max(1, h * w)
        if mask_ratio < gc.min_object_ratio:
            flags.append(Flag.MASK_TOO_SMALL)

        # Bbox-ratio checks
        bbox_ratio = bbox.area / max(1, h * w)
        if bbox_ratio > gc.max_bbox_ratio:
            flags.append(Flag.BBOX_TOO_LARGE)
        if bbox_ratio < gc.min_bbox_ratio:
            flags.append(Flag.BBOX_TOO_SMALL)

        # 6. Expand bbox with category-aware margins
        expanded = self._expand_bbox(bbox, w, h, cat_config)

        # 7. Crop — preserve all channels (including alpha for transparent bg)
        cropped = crop_region(image, expanded)

        # 8. Resize
        target_fill = (
            cat_config.target_fill_ratio_min + cat_config.target_fill_ratio_max
        ) / 2
        resized = resize_to_fit(cropped, gc.canvas_size, target_fill)

        # 9. Place on canvas
        final = place_on_canvas(
            resized,
            gc.canvas_size,
            gc.background_color,
            cat_config.centering_bias_x,
            cat_config.centering_bias_y,
        )

        # 10. Compute metrics
        fill = compute_fill_ratio(
            (resized.shape[1], resized.shape[0]), gc.canvas_size
        )

        metrics = CropMetrics(
            fill_ratio=fill,
            crop_area_ratio=expanded.area / max(1, h * w),
            margin_px=int(cat_config.margin_pct * max(w, h)),
            bbox=expanded,
            object_pixel_count=object_pixels,
            component_count=sig_count,
        )

        return CropResult(
            mask=filtered_mask,
            bbox=expanded,
            cropped_image=cropped,
            final_image=final,
            metrics=metrics,
            flags=flags,
            background_type=bg_type,
        )

    def _generate_mask(
        self,
        image: np.ndarray,
        bg_type: BackgroundType,
        gc: object,
        cat_config: CategoryConfig,
    ) -> np.ndarray:
        """Select and run the appropriate mask generator."""
        if bg_type == BackgroundType.TRANSPARENT:
            return mask_from_alpha(image, gc.alpha_threshold)
        elif bg_type == BackgroundType.WHITE_BG:
            return mask_from_white_bg(
                image[:, :, :3],
                gc.white_distance_threshold,
                cat_config.threshold_bias,
            )
        else:
            return mask_from_complex_bg(
                image[:, :, :3],
                cat_config.morph_kernel_size + 2,
                cat_config.morph_iterations + 1,
                block_size=cat_config.adaptive_block_size,
                constant_c=cat_config.adaptive_c,
            )

    def _expand_bbox(
        self,
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

        # Thin-object protection: extra margin in the narrow dimension
        if config.thin_object_protection:
            aspect = max(bbox.w, bbox.h) / max(1, min(bbox.w, bbox.h))
            if aspect > 3.0:
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
        return expanded.clamp(img_w, img_h)
