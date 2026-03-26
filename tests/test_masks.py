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
        mask_normal = mask_from_white_bg(white_bg_image, distance_threshold=30)
        mask_strict = mask_from_white_bg(
            white_bg_image, distance_threshold=30, bias=-20
        )
        # Stricter threshold → fewer foreground pixels
        assert np.count_nonzero(mask_strict) <= np.count_nonzero(mask_normal)


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
