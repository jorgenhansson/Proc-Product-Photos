"""Tests for canvas placement, resizing, and compositing."""

from __future__ import annotations

import numpy as np
import pytest

from process_images.crop.canvas import (
    compute_fill_ratio,
    crop_region,
    place_on_canvas,
    resize_to_fit,
)
from process_images.models import BBox


class TestCropRegion:
    def test_basic_crop(self):
        img = np.arange(100 * 100 * 3, dtype=np.uint8).reshape(100, 100, 3)
        bbox = BBox(10, 20, 30, 40)
        cropped = crop_region(img, bbox)
        assert cropped.shape == (40, 30, 3)

    def test_crop_is_copy(self):
        img = np.zeros((50, 50, 3), dtype=np.uint8)
        bbox = BBox(0, 0, 10, 10)
        cropped = crop_region(img, bbox)
        cropped[0, 0] = [255, 255, 255]
        assert img[0, 0, 0] == 0  # original unchanged


class TestResizeToFit:
    def test_doesnt_upscale_low_fill_target(self):
        """With fill_ratio_target < 0.80, small images are NOT upscaled."""
        small = np.zeros((50, 50, 3), dtype=np.uint8)
        result = resize_to_fit(small, canvas_size=200, fill_ratio_target=0.60)
        assert result.shape[0] <= 50
        assert result.shape[1] <= 50

    def test_upscales_in_zero_margin_mode(self):
        """With fill_ratio_target >= 0.95 (zero-margin), upscaling IS allowed."""
        small = np.zeros((50, 50, 3), dtype=np.uint8)
        result = resize_to_fit(small, canvas_size=200, fill_ratio_target=1.0)
        assert max(result.shape[:2]) == 200

    def test_min_output_px_forces_upscale(self):
        """Tiny image should be upscaled to meet min_output_px."""
        tiny = np.zeros((10, 10, 3), dtype=np.uint8)
        result = resize_to_fit(tiny, canvas_size=200, min_output_px=80)
        assert max(result.shape[:2]) >= 80

    def test_min_output_px_zero_no_upscale_low_fill(self):
        """min_output_px=0 with low fill target should not upscale."""
        tiny = np.zeros((10, 10, 3), dtype=np.uint8)
        result = resize_to_fit(tiny, canvas_size=200, fill_ratio_target=0.60, min_output_px=0)
        assert result.shape[0] <= 10
        assert result.shape[1] <= 10

    def test_downscales_large_image(self):
        large = np.zeros((500, 500, 3), dtype=np.uint8)
        result = resize_to_fit(large, canvas_size=200, fill_ratio_target=0.85)
        assert result.shape[0] <= 200
        assert result.shape[1] <= 200

    def test_preserves_aspect_ratio(self):
        img = np.zeros((100, 200, 3), dtype=np.uint8)  # 2:1
        result = resize_to_fit(img, canvas_size=200, fill_ratio_target=0.8)
        h, w = result.shape[:2]
        aspect = w / max(1, h)
        assert abs(aspect - 2.0) < 0.1


class TestPlaceOnCanvas:
    def test_output_is_square(self):
        img = np.zeros((50, 80, 3), dtype=np.uint8)
        canvas = place_on_canvas(img, canvas_size=200)
        assert canvas.shape == (200, 200, 3)

    def test_background_is_white(self):
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        canvas = place_on_canvas(img, canvas_size=100)
        # Corner should be white
        assert tuple(canvas[0, 0]) == (255, 255, 255)

    def test_centered(self):
        img = np.full((10, 10, 3), 128, dtype=np.uint8)
        canvas = place_on_canvas(img, canvas_size=100)
        # Center pixel should be 128
        assert canvas[50, 50, 0] == 128

    def test_rgba_compositing(self):
        img = np.zeros((10, 10, 4), dtype=np.uint8)
        img[:, :, 0] = 255  # Red
        img[:, :, 3] = 128  # 50% alpha
        canvas = place_on_canvas(img, canvas_size=100)
        # Should be blended between red and white
        center_r = int(canvas[50, 50, 0])
        center_g = int(canvas[50, 50, 1])
        # Red channel should be high (blended red + white)
        assert center_r >= 128
        # Green channel should be less than pure white (blended towards 0)
        assert center_g < 255

    def test_bias_shifts_position(self):
        img = np.full((10, 10, 3), 0, dtype=np.uint8)
        canvas_centered = place_on_canvas(img, 100, bias_x=0.0)
        canvas_shifted = place_on_canvas(img, 100, bias_x=0.2)
        # Find leftmost non-white column
        def leftmost(c):
            cols = np.any(c != 255, axis=(0, 2))
            return np.argmax(cols)

        assert leftmost(canvas_shifted) > leftmost(canvas_centered)


class TestFillRatio:
    def test_full_fill(self):
        assert compute_fill_ratio((100, 100), 100) == 1.0

    def test_half_fill(self):
        assert abs(compute_fill_ratio((50, 50), 100) - 0.5) < 0.01

    def test_zero_canvas(self):
        assert compute_fill_ratio((50, 50), 0) == 0.0
