"""Tests for the classical (deterministic) crop strategy."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from process_images.config import PipelineConfig, GlobalConfig, CategoryConfig
from process_images.crop.classical import ClassicalCropStrategy
from process_images.crop.finalize import finalize_crop
from process_images.crop.morphology import clean_mask, merge_collinear_components
from process_images.models import BBox, BackgroundType, Flag, ImageContext


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
        assert result.crop_bbox is not None
        assert result.crop_bbox.w > 0
        assert result.crop_bbox.h > 0

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

    def test_transparent_crop_no_black_halo(self, strategy, config_200):
        """Transparent-bg image with anti-aliased edges must not produce
        black artifacts on the white canvas.
        """
        # Create RGBA image: red circle with smooth alpha edges on transparent bg
        img = np.zeros((200, 200, 4), dtype=np.uint8)
        y, x = np.ogrid[:200, :200]
        dist = np.sqrt((x - 100.0) ** 2 + (y - 100.0) ** 2)
        # Smooth alpha falloff at edge (anti-aliased)
        alpha = np.clip(255 - (dist - 35) * 10, 0, 255).astype(np.uint8)
        img[:, :, 0] = 200  # red channel
        img[:, :, 3] = alpha

        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(img, ctx, config_200)
        assert result.final_image is not None
        assert result.final_image.shape == (200, 200, 3)

        # Check corners are white (not black from unmasked transparent pixels)
        for cy, cx in [(0, 0), (0, 199), (199, 0), (199, 199)]:
            pixel = result.final_image[cy, cx]
            assert int(pixel[0]) > 200, f"Corner ({cy},{cx}) is dark: {pixel}"
            assert int(pixel[1]) > 200, f"Corner ({cy},{cx}) is dark: {pixel}"
            assert int(pixel[2]) > 200, f"Corner ({cy},{cx}) is dark: {pixel}"

    def test_transparent_crop_preserves_alpha_compositing(self, strategy, config_200):
        """Alpha compositing should blend semi-transparent pixels with white,
        not render them as their raw RGB values.
        """
        img = np.zeros((200, 200, 4), dtype=np.uint8)
        # Semi-transparent green square (alpha above mask threshold of 128)
        img[70:130, 70:130, 1] = 200  # green
        img[70:130, 70:130, 3] = 180  # 70% alpha — above threshold

        ctx = ImageContext(source_path=Path("test.png"), category="BALL")
        result = strategy.crop(img, ctx, config_200)
        assert result.final_image is not None

        # Center should be a blend of green and white, not pure green
        # and definitely not black
        center = result.final_image[100, 100]
        assert int(center[1]) > 50, "Green channel should be present"
        assert int(center[0]) > 50, "Red channel should show white bleed-through"


class TestThinObjectProtection:
    """Tests for skip_open, collinear merge, and shaft preservation."""

    def test_skip_open_preserves_thin_line(self):
        """Morphological open deletes thin lines; skip_open should preserve them."""
        # Create a mask with a 4px-wide vertical line
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[10:190, 98:102] = 255

        result_with_open = clean_mask(mask, kernel_size=5, iterations=2, skip_open=False)
        result_without_open = clean_mask(mask, kernel_size=5, iterations=2, skip_open=True)

        # With open: thin line should be eroded away (or mostly gone)
        # Without open: line should be preserved
        assert np.count_nonzero(result_without_open) > np.count_nonzero(result_with_open)
        assert np.count_nonzero(result_without_open) > 0

    def test_skip_open_still_closes_gaps(self):
        """skip_open=True should still fill small holes (morph close runs)."""
        mask = np.zeros((200, 200), dtype=np.uint8)
        mask[80:120, 80:120] = 255
        mask[98:102, 98:102] = 0  # small 4x4 hole in center

        result = clean_mask(mask, kernel_size=5, iterations=2, skip_open=True)
        # Small hole should be filled by close operation
        assert result[100, 100] == 255

    def test_collinear_merge_two_vertical_components(self):
        """Two vertically aligned components should be merged."""
        mask = np.zeros((200, 200), dtype=np.uint8)
        # Component 1: top block
        mask[10:60, 90:110] = 255
        # Component 2: bottom block, vertically aligned
        mask[140:190, 90:110] = 255

        result = merge_collinear_components(mask, min_size=100)
        # Both components should be in the result
        assert result[30, 100] == 255   # top
        assert result[160, 100] == 255  # bottom

    def test_collinear_merge_not_aligned(self):
        """Two non-aligned components should NOT be merged."""
        mask = np.zeros((200, 200), dtype=np.uint8)
        # Component 1: top-left
        mask[10:40, 10:40] = 255
        # Component 2: bottom-right (diagonal, not collinear)
        mask[160:190, 160:190] = 255

        result = merge_collinear_components(mask, min_size=100, collinearity_threshold=0.15)
        original_count = np.count_nonzero(mask)
        result_count = np.count_nonzero(result)
        # For 2 components, diagonal alignment (dx≈dy) has off_axis/span ≈ 1.0,
        # which exceeds the threshold — should not merge
        # Result should equal original (no merge)
        assert result_count == original_count

    def test_club_shaft_preserved_in_pipeline(self, strategy, config_200, thin_object_image):
        """CLUB_LONG thin shaft should survive the full pipeline."""
        ctx = ImageContext(source_path=Path("test.png"), category="CLUB_LONG")
        result = strategy.crop(thin_object_image, ctx, config_200)
        assert result.final_image is not None
        # Shaft should be visible on canvas (dark pixels exist)
        dark_pixels = np.sum(result.final_image < 100)
        assert dark_pixels > 0, "Shaft should be visible on canvas"

    def test_club_head_shaft_merged(self, strategy, club_head_shaft_image):
        """Separated club head and shaft should be merged by collinear logic."""
        config = PipelineConfig(global_config=GlobalConfig(canvas_size=300))
        ctx = ImageContext(source_path=Path("test.png"), category="CLUB_LONG")
        result = strategy.crop(club_head_shaft_image, ctx, config)
        assert result.final_image is not None
        assert result.crop_bbox is not None
        # Bbox should span from shaft top to head bottom (>200px of the 300px image)
        assert result.crop_bbox.h > 180, f"Bbox should span both shaft and head, got h={result.crop_bbox.h}"

    def test_thin_object_min_crop_width(self, strategy):
        """A very thin vertical bar should get a minimum crop width
        of at least height/4 so it's visible after resize."""
        # 400px tall, 4px wide bar
        img = np.full((400, 400, 3), 255, dtype=np.uint8)
        img[10:390, 198:202] = [30, 30, 30]

        config = PipelineConfig(global_config=GlobalConfig(canvas_size=400))
        ctx = ImageContext(source_path=Path("test.png"), category="CLUB_LONG")
        result = strategy.crop(img, ctx, config)

        assert result.crop_bbox is not None
        # Crop width should be at least height//4 = ~95px, not just 4+margins
        assert result.crop_bbox.w >= 90, (
            f"Crop width {result.crop_bbox.w} too narrow for thin object"
        )


class TestFinalizeCropGuards:
    """Tests for edge case guards in finalize_crop."""

    def test_degenerate_bbox_returns_no_object(self):
        """A bbox that clamps to zero dimensions should return NO_OBJECT_FOUND."""
        image = np.full((100, 100, 3), 255, dtype=np.uint8)
        mask = np.zeros((100, 100), dtype=np.uint8)
        # Bbox entirely outside image — will clamp to w=0 or h=0
        bbox = BBox(x=200, y=200, w=50, h=50)
        gc = GlobalConfig(canvas_size=100)
        cat = CategoryConfig(margin_pct=0.0)

        result = finalize_crop(
            image=image,
            mask=mask,
            object_bbox=bbox,
            background_type=BackgroundType.WHITE_BG,
            flags=[],
            cat_config=cat,
            global_config=gc,
            object_pixel_count=0,
            component_count=0,
        )
        assert Flag.NO_OBJECT_FOUND in result.flags
        assert result.final_image is None
