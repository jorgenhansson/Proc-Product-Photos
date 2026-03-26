"""Tests for flagging logic and post-crop validation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from process_images.config import GlobalConfig, PipelineConfig
from process_images.models import (
    BBox,
    CropMetrics,
    CropResult,
    Flag,
    ImageContext,
)
from process_images.validators import validate_crop_result


@pytest.fixture
def config():
    return PipelineConfig(global_config=GlobalConfig(canvas_size=200))


@pytest.fixture
def context():
    return ImageContext(source_path=Path("test.png"), category="BALL")


class TestValidation:
    def test_mask_too_small(self, config, context):
        # Mask with very few pixels
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[100, 100] = 255  # single pixel
        result = CropResult(
            mask=mask,
            object_bbox=BBox(100, 100, 1, 1),
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.MASK_TOO_SMALL in flags

    def test_bbox_too_large(self, config, context):
        mask = np.full((200, 200), 255, dtype=np.uint8)
        result = CropResult(
            mask=mask,
            object_bbox=BBox(0, 0, 200, 200),
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.BBOX_TOO_LARGE in flags

    def test_bbox_too_small(self, config, context):
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[100, 100] = 255
        result = CropResult(
            mask=mask,
            object_bbox=BBox(100, 100, 1, 1),
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.BBOX_TOO_SMALL in flags

    def test_fill_ratio_too_low(self, config, context):
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.05),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.FILL_RATIO_TOO_LOW in flags

    def test_fill_ratio_too_high(self, config, context):
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.99),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.FILL_RATIO_TOO_HIGH in flags

    def test_edge_proximity(self, config, context):
        # Object touching the edge of final canvas
        canvas = np.full((200, 200, 3), 255, dtype=np.uint8)
        canvas[0:5, 50:150] = [0, 0, 0]  # top edge
        result = CropResult(
            final_image=canvas,
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.OBJECT_TOO_CLOSE_TO_EDGE in flags

    def test_no_flags_for_good_result(self, config, context):
        # Well-centered object, reasonable fill
        canvas = np.full((200, 200, 3), 255, dtype=np.uint8)
        canvas[40:160, 40:160] = [100, 100, 100]
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[40:160, 40:160] = 255
        result = CropResult(
            mask=mask,
            object_bbox=BBox(40, 40, 120, 120),
            final_image=canvas,
            metrics=CropMetrics(fill_ratio=0.55),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert len(flags) == 0

    def test_category_inconsistency(self, config, context):
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.01),  # way below expected
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.CROP_CATEGORY_INCONSISTENT in flags

    def test_existing_flags_not_duplicated(self, config, context):
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[100, 100] = 255
        result = CropResult(
            mask=mask,
            object_bbox=BBox(100, 100, 1, 1),
            flags=[Flag.MASK_TOO_SMALL],  # already flagged
            metrics=CropMetrics(fill_ratio=0.5),
        )
        new_flags = validate_crop_result(result, (200, 200), context, config)
        # MASK_TOO_SMALL should not appear again
        assert Flag.MASK_TOO_SMALL not in new_flags
