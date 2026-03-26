"""Tests for mask generation and background type detection."""

from __future__ import annotations

import numpy as np
import pytest

from process_images.config import GlobalConfig
from process_images.crop.masks import (
    detect_background_type,
    mask_from_alpha,
    mask_from_complex_bg,
    mask_from_white_bg,
)
from process_images.models import BackgroundType


class TestBackgroundDetection:
    def test_transparent_detected(self, transparent_bg_image):
        config = GlobalConfig()
        result = detect_background_type(transparent_bg_image, config)
        assert result == BackgroundType.TRANSPARENT

    def test_white_bg_detected(self, white_bg_image):
        config = GlobalConfig()
        result = detect_background_type(white_bg_image, config)
        assert result == BackgroundType.WHITE_BG

    def test_complex_bg_detected(self, complex_bg_image):
        config = GlobalConfig()
        result = detect_background_type(complex_bg_image, config)
        assert result == BackgroundType.COMPLEX_BG

    def test_pure_white_is_white_bg(self):
        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        config = GlobalConfig()
        result = detect_background_type(img, config)
        assert result == BackgroundType.WHITE_BG

    def test_near_full_alpha_not_transparent(self):
        """RGBA image with alpha all at 254 should NOT be classified as transparent.
        Guards against lossy TIFF exports with near-full alpha.
        """
        img = np.full((100, 100, 4), 255, dtype=np.uint8)
        img[:, :, 3] = 254  # near-full alpha, no real transparency
        config = GlobalConfig()
        result = detect_background_type(img, config)
        assert result != BackgroundType.TRANSPARENT

    def test_single_low_alpha_pixel_not_transparent(self):
        """One corrupted pixel with alpha=0 should not trigger TRANSPARENT
        if the rest of the image is fully opaque.
        """
        img = np.full((100, 100, 4), 255, dtype=np.uint8)
        img[50, 50, 3] = 0  # single pixel — transparent_fraction < 0.01
        config = GlobalConfig()
        result = detect_background_type(img, config)
        assert result != BackgroundType.TRANSPARENT

    def test_genuine_alpha_variation_detected(self):
        """Image with real transparent background (large transparent area) is detected."""
        img = np.full((100, 100, 4), 255, dtype=np.uint8)
        img[:50, :, 3] = 0  # top half fully transparent
        config = GlobalConfig()
        result = detect_background_type(img, config)
        assert result == BackgroundType.TRANSPARENT


class TestAlphaMask:
    def test_produces_binary_mask(self, transparent_bg_image):
        mask = mask_from_alpha(transparent_bg_image)
        unique = set(np.unique(mask))
        assert unique <= {0, 255}

    def test_foreground_detected(self, transparent_bg_image):
        mask = mask_from_alpha(transparent_bg_image)
        assert np.count_nonzero(mask) > 0

    def test_background_is_zero(self, transparent_bg_image):
        mask = mask_from_alpha(transparent_bg_image)
        # Corner pixel should be background
        assert mask[0, 0] == 0

    def test_raises_for_rgb_image(self, white_bg_image):
        with pytest.raises(ValueError, match="no alpha"):
            mask_from_alpha(white_bg_image)


class TestWhiteBgMask:
    def test_object_detected(self, white_bg_image):
        mask = mask_from_white_bg(white_bg_image)
        assert np.count_nonzero(mask) > 0

    def test_background_is_zero(self, white_bg_image):
        mask = mask_from_white_bg(white_bg_image)
        assert mask[0, 0] == 0
        assert mask[199, 199] == 0

    def test_object_center_is_foreground(self, white_bg_image):
        mask = mask_from_white_bg(white_bg_image)
        assert mask[100, 100] == 255

    def test_bias_affects_threshold(self, white_bg_image):
        mask_normal = mask_from_white_bg(white_bg_image, distance_threshold=12)
        mask_strict = mask_from_white_bg(
            white_bg_image, distance_threshold=12, bias=-8
        )
        # Stricter threshold → fewer foreground pixels
        assert np.count_nonzero(mask_strict) <= np.count_nonzero(mask_normal)

    def test_lab_separates_pink_from_gray(self):
        """LAB distance should distinguish a pink product pixel from a
        gray shadow pixel, even though both have similar RGB distance
        to white (~42).
        """
        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        # Pink product area
        img[40:60, 40:60] = [255, 225, 225]
        # Gray shadow area (same RGB distance to white as pink)
        img[40:60, 70:90] = [235, 235, 235]

        # At threshold ~10: gray (LAB dist ~9) should be background,
        # pink (LAB dist ~15) should be foreground
        mask = mask_from_white_bg(img, distance_threshold=10.0)

        # Pink area should be detected as foreground
        assert mask[50, 50] == 255, "Pink product should be foreground"
        # Gray area should be background (below threshold)
        assert mask[50, 80] == 0, "Gray shadow should be background"

    def test_lab_detects_colored_product_on_white(self):
        """A colored product on white background should be cleanly separated."""
        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        # Red product
        img[30:70, 30:70] = [200, 50, 50]

        mask = mask_from_white_bg(img, distance_threshold=12.0)
        # Product should be fully detected
        product_pixels = np.count_nonzero(mask[30:70, 30:70])
        total_product = 40 * 40
        assert product_pixels > total_product * 0.95, "Red product should be fully detected"
        # Background should be clean
        assert mask[5, 5] == 0, "White corner should be background"


class TestComplexBgMask:
    def test_produces_mask(self, complex_bg_image):
        mask = mask_from_complex_bg(complex_bg_image)
        assert mask.shape == complex_bg_image.shape[:2]
        assert np.count_nonzero(mask) > 0

    def test_custom_block_size_and_c(self, complex_bg_image):
        mask_default = mask_from_complex_bg(complex_bg_image)
        mask_custom = mask_from_complex_bg(
            complex_bg_image, block_size=31, constant_c=5.0
        )
        # Different parameters should produce different masks
        assert mask_custom.shape == mask_default.shape
        # Lower C → more pixels classified as foreground
        assert np.count_nonzero(mask_custom) != np.count_nonzero(mask_default)

    def test_even_block_size_rounded_to_odd(self, complex_bg_image):
        # Even block_size should be handled gracefully (rounded up to odd)
        mask = mask_from_complex_bg(complex_bg_image, block_size=20)
        assert mask.shape == complex_bg_image.shape[:2]
