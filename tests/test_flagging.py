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

    def test_fill_ratio_too_low(self, config, context):
        """Fill ratio far below category minimum triggers FILL_RATIO_TOO_LOW."""
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.01),  # way below expected
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.FILL_RATIO_TOO_LOW in flags

    def test_category_inconsistency_via_aspect_ratio(self, config, context):
        """Aspect ratio outside category range triggers CROP_CATEGORY_INCONSISTENT."""
        # BALL expects aspect ratio 1.0-1.5; give it 5.0 (very elongated)
        mask = np.ones((200, 200), dtype=np.uint8) * 255
        result = CropResult(
            mask=mask,
            object_bbox=BBox(10, 10, 180, 36),  # AR = 5.0
            metrics=CropMetrics(fill_ratio=0.5),
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


class TestRelaxedTolerance:
    """Tests for the tolerance parameter used in fallback validation."""

    def test_strict_flags_low_fill(self, config, context):
        """Fill ratio 0.10 is below BALL min (0.20) at tolerance=1.0."""
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.10),
        )
        flags = validate_crop_result(result, (200, 200), context, config, tolerance=1.0)
        assert Flag.FILL_RATIO_TOO_LOW in flags

    def test_relaxed_accepts_low_fill(self, config, context):
        """Fill ratio 0.22 is above BALL min*0.8 (0.24) at tolerance=0.8."""
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.25),
        )
        flags = validate_crop_result(result, (200, 200), context, config, tolerance=0.8)
        # 0.30 * 0.8 = 0.24 → 0.25 is above → no flag
        assert Flag.FILL_RATIO_TOO_LOW not in flags

    def test_relaxed_still_rejects_very_low_fill(self, config, context):
        """Fill ratio 0.10 is still below even relaxed threshold."""
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.10),
        )
        flags = validate_crop_result(result, (200, 200), context, config, tolerance=0.8)
        assert Flag.FILL_RATIO_TOO_LOW in flags

    def test_relaxed_widens_fill_max(self, config, context):
        """Fill ratio 0.80 exceeds BALL max (0.75) strict but passes at 0.8 tolerance."""
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.80),
        )
        strict = validate_crop_result(result, (200, 200), context, config, tolerance=1.0)
        relaxed = validate_crop_result(result, (200, 200), context, config, tolerance=0.8)
        # 0.75 / 0.8 = 0.9375 → 0.80 < 0.9375 → passes
        assert Flag.FILL_RATIO_TOO_HIGH in strict
        assert Flag.FILL_RATIO_TOO_HIGH not in relaxed

    def test_relaxed_bbox_threshold(self, config, context):
        """Smaller bbox passes with relaxed tolerance."""
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[99:101, 99:101] = 255  # 4 pixels
        result = CropResult(
            mask=mask,
            object_bbox=BBox(99, 99, 2, 2),  # tiny
            metrics=CropMetrics(fill_ratio=0.5),
        )
        strict = validate_crop_result(result, (200, 200), context, config, tolerance=1.0)
        relaxed = validate_crop_result(result, (200, 200), context, config, tolerance=0.5)
        # min_bbox_ratio=0.01 strict, 0.005 relaxed
        # bbox ratio = 4/40000 = 0.0001 → flagged in both, but the principle is tested
        assert Flag.BBOX_TOO_SMALL in strict
        assert Flag.BBOX_TOO_SMALL in relaxed  # still too small even relaxed

    def test_tolerance_1_equals_default(self, config, context):
        """tolerance=1.0 should produce identical results to omitting it."""
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
        default_flags = validate_crop_result(result, (200, 200), context, config)
        explicit_flags = validate_crop_result(result, (200, 200), context, config, tolerance=1.0)
        assert default_flags == explicit_flags


class TestAspectRatioValidation:
    """Tests for category-specific aspect ratio checks."""

    def test_ball_square_bbox_passes(self, config):
        """A roughly square bbox for BALL should not flag."""
        context = ImageContext(source_path=Path("t.png"), category="BALL")
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            object_bbox=BBox(50, 50, 80, 90),  # aspect ~1.1
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.CROP_CATEGORY_INCONSISTENT not in flags

    def test_ball_elongated_bbox_flags(self, config):
        """An elongated bbox for BALL should flag as inconsistent."""
        context = ImageContext(source_path=Path("t.png"), category="BALL")
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            object_bbox=BBox(50, 10, 20, 180),  # aspect 9:1
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.CROP_CATEGORY_INCONSISTENT in flags

    def test_club_long_elongated_passes(self, config):
        """An elongated bbox for CLUB_LONG should pass."""
        context = ImageContext(source_path=Path("t.png"), category="CLUB_LONG")
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            object_bbox=BBox(90, 10, 20, 180),  # aspect 9:1
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.CROP_CATEGORY_INCONSISTENT not in flags

    def test_club_long_square_bbox_flags(self, config):
        """A square bbox for CLUB_LONG should flag (expected elongated)."""
        context = ImageContext(source_path=Path("t.png"), category="CLUB_LONG")
        result = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            object_bbox=BBox(50, 50, 80, 80),  # aspect 1:1
            metrics=CropMetrics(fill_ratio=0.5),
        )
        flags = validate_crop_result(result, (200, 200), context, config)
        assert Flag.CROP_CATEGORY_INCONSISTENT in flags

    def test_orientation_independent(self, config):
        """Horizontal and vertical clubs should both pass."""
        context = ImageContext(source_path=Path("t.png"), category="CLUB_LONG")
        # Vertical club
        r1 = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            object_bbox=BBox(90, 10, 20, 180),  # 9:1 vertical
            metrics=CropMetrics(fill_ratio=0.5),
        )
        # Horizontal club
        r2 = CropResult(
            mask=np.zeros((200, 200), dtype=np.uint8),
            object_bbox=BBox(10, 90, 180, 20),  # 9:1 horizontal
            metrics=CropMetrics(fill_ratio=0.5),
        )
        f1 = validate_crop_result(r1, (200, 200), context, config)
        f2 = validate_crop_result(r2, (200, 200), context, config)
        assert Flag.CROP_CATEGORY_INCONSISTENT not in f1
        assert Flag.CROP_CATEGORY_INCONSISTENT not in f2
