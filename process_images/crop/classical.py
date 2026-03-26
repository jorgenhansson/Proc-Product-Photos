"""Classical (deterministic) crop strategy -- the primary pipeline."""

from __future__ import annotations

import logging

import numpy as np

from ..config import CategoryConfig, PipelineConfig
from ..models import (
    BackgroundType,
    CropMetrics,
    CropResult,
    Flag,
    ImageContext,
)
from .base import CropStrategy
from .categories import resolve_category
from .finalize import finalize_crop
from .masks import (
    detect_background_type,
    mask_from_alpha,
    mask_from_complex_bg,
    mask_from_white_bg,
)
from .morphology import (
    clean_mask,
    compute_bbox,
    find_main_component,
    merge_collinear_components,
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

        # 3. Clean mask — skip morph-open for thin-object categories
        #    to prevent shaft deletion
        mask = clean_mask(
            mask,
            kernel_size=cat_config.morph_kernel_size,
            iterations=cat_config.morph_iterations,
            skip_open=cat_config.thin_object_protection,
        )

        # 4. Find main component
        filtered_mask, sig_count = find_main_component(
            mask, cat_config.min_component_size
        )

        # 4b. For thin-object categories with multiple components,
        #     attempt collinear merge (shaft + head).
        #     Merged components may still be physically separated, so
        #     use the full merged mask (not find_main_component) and
        #     treat the merged set as a single logical object.
        if sig_count > 1 and cat_config.thin_object_protection:
            merged = merge_collinear_components(
                mask, cat_config.min_component_size
            )
            merged_pixels = int(np.count_nonzero(merged))
            if merged_pixels > np.count_nonzero(filtered_mask):
                filtered_mask = merged
                sig_count = 1  # treat merged group as single object
                logger.debug(
                    "Collinear merge: combined %d px", merged_pixels
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

        # 6-10. Expand bbox, crop, resize, canvas, metrics
        return finalize_crop(
            image=image,
            mask=filtered_mask,
            object_bbox=bbox,
            background_type=bg_type,
            flags=flags,
            cat_config=cat_config,
            global_config=gc,
            object_pixel_count=int(np.count_nonzero(filtered_mask)),
            component_count=sig_count,
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
                block_size=cat_config.adaptive_block_size,
                constant_c=cat_config.adaptive_c,
            )

