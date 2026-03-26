"""Tests for the classical (deterministic) crop strategy."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from process_images.config import PipelineConfig, GlobalConfig
from process_images.crop.classical import ClassicalCropStrategy
from process_images.models import Flag, ImageContext


@pytest.fixture
def strategy():
    return ClassicalCropStrategy()


@pytest.fixture
def config_200():
    return PipelineConfig(global_config=GlobalConfig(canvas_size=200))


class TestClassicalCrop:
    def test_white_bg_produces_final_image(
        self, strategy, config_200, white_bg_image
    ):
        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(white_bg_image, ctx, config_200)
        assert result.final_image is not None
        assert result.final_image.shape == (200, 200, 3)

    def test_transparent_bg_produces_final_image(
        self, strategy, config_200, transparent_bg_image
    ):
        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(transparent_bg_image, ctx, config_200)
        assert result.final_image is not None

    def test_mask_is_binary(self, strategy, config_200, white_bg_image):
        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(white_bg_image, ctx, config_200)
        unique = set(np.unique(result.mask))
        assert unique <= {0, 255}

    def test_bbox_is_computed(self, strategy, config_200, white_bg_image):
        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(white_bg_image, ctx, config_200)
        assert result.bbox is not None
        assert result.bbox.w > 0
        assert result.bbox.h > 0

    def test_fill_ratio_is_positive(self, strategy, config_200, white_bg_image):
        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(white_bg_image, ctx, config_200)
        assert result.metrics.fill_ratio > 0

    def test_empty_image_flags_no_object(
        self, strategy, config_200, empty_image
    ):
        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(empty_image, ctx, config_200)
        assert Flag.NO_OBJECT_FOUND in result.flags or Flag.MASK_TOO_SMALL in result.flags

    def test_thin_object_detected(self, strategy, config_200, thin_object_image):
        ctx = ImageContext(source_path=Path("test.png"), category="CLUB_LONG")
        result = strategy.crop(thin_object_image, ctx, config_200)
        # Should succeed (thin protection enabled for CLUB_LONG)
        assert result.final_image is not None

    def test_category_affects_margin(self, strategy, config_200, white_bg_image):
        ctx_ball = ImageContext(source_path=Path("t.png"), category="BALL")
        ctx_box = ImageContext(
            source_path=Path("t.png"), category="BOX_OR_PACKAGING"
        )
        r_ball = strategy.crop(white_bg_image, ctx_ball, config_200)
        r_box = strategy.crop(white_bg_image, ctx_box, config_200)
        # BALL has larger margin than BOX_OR_PACKAGING
        assert r_ball.metrics.margin_px >= r_box.metrics.margin_px

    def test_margin_is_image_relative(self, strategy, config_200, white_bg_image):
        """Margin should be based on image dimensions, not object dimensions."""
        ctx = ImageContext(source_path=Path("t.png"), category="BALL")
        result = strategy.crop(white_bg_image, ctx, config_200)
        # BALL margin_pct = 0.12, image is 200x200 → margin = 200 * 0.12 = 24
        assert result.metrics.margin_px == 24
