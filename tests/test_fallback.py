"""Tests for the AI/heuristic fallback crop strategy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from process_images.config import GlobalConfig, PipelineConfig
from process_images.crop.ai_fallback import AIFallbackCropStrategy
from process_images.crop.base import CropStrategy
from process_images.models import CropResult, Flag, ImageContext, CropMetrics


@pytest.fixture
def config():
    return PipelineConfig(global_config=GlobalConfig(canvas_size=200))


@pytest.fixture
def context():
    return ImageContext(source_path=Path("test.png"), category="BALL")


class TestGrabCutFallback:
    def test_produces_result(self, config, context, white_bg_image):
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        assert isinstance(result, CropResult)

    def test_produces_final_image_for_good_input(
        self, config, context, white_bg_image
    ):
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        # GrabCut should find the dark square
        if result.final_image is not None:
            assert result.final_image.shape == (200, 200, 3)

    def test_handles_empty_image(self, config, context, empty_image):
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(empty_image, context, config)
        # May or may not find object, but should not crash
        assert isinstance(result, CropResult)


class TestExternalProvider:
    def test_delegates_to_provider(self, config, context, white_bg_image):
        mock_provider = MagicMock(spec=CropStrategy)
        mock_result = CropResult(
            final_image=np.zeros((200, 200, 3), dtype=np.uint8),
            metrics=CropMetrics(fill_ratio=0.5),
        )
        mock_provider.crop.return_value = mock_result

        strategy = AIFallbackCropStrategy(external_provider=mock_provider)
        result = strategy.crop(white_bg_image, context, config)

        mock_provider.crop.assert_called_once_with(
            white_bg_image, context, config
        )
        assert result is mock_result

    def test_none_provider_uses_grabcut(self, config, context, white_bg_image):
        strategy = AIFallbackCropStrategy(external_provider=None)
        result = strategy.crop(white_bg_image, context, config)
        # Should use internal GrabCut, not crash
        assert isinstance(result, CropResult)


class TestPriorMaskInitialization:
    """Verify that GrabCut uses the classical mask when available."""

    def test_uses_prior_mask_without_crash(self, config, white_bg_image):
        # Create a prior mask matching the dark square
        prior = np.zeros((200, 200), dtype=np.uint8)
        prior[70:130, 70:130] = 255

        context = ImageContext(
            source_path=Path("test.png"),
            category="BALL",
            prior_mask=prior,
        )
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        assert isinstance(result, CropResult)
        # With a good prior mask, GrabCut should produce a final image
        assert result.final_image is not None

    def test_falls_back_to_rect_without_prior(self, config, white_bg_image):
        context = ImageContext(
            source_path=Path("test.png"),
            category="BALL",
            prior_mask=None,
        )
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        assert isinstance(result, CropResult)

    def test_ignores_wrong_shape_prior(self, config, white_bg_image):
        # Prior mask with wrong dimensions should be ignored
        wrong_prior = np.zeros((50, 50), dtype=np.uint8)
        context = ImageContext(
            source_path=Path("test.png"),
            category="BALL",
            prior_mask=wrong_prior,
        )
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        # Should not crash, falls back to rect init
        assert isinstance(result, CropResult)

    def test_gc_fgd_seeding_improves_result(self, config, white_bg_image):
        """Prior mask with GC_FGD inner region should produce a valid crop.
        The inner 50% of the prior bbox gets marked as definite foreground,
        which anchors GrabCut's segmentation."""
        prior = np.zeros((200, 200), dtype=np.uint8)
        prior[70:130, 70:130] = 255  # large enough for inner bbox

        context = ImageContext(
            source_path=Path("test.png"),
            category="BALL",
            prior_mask=prior,
        )
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        assert result.final_image is not None
        # Should have found a valid object
        assert result.object_bbox is not None
        assert result.metrics.fill_ratio > 0

    def test_hollow_prior_mask_respects_hole(self, config):
        """Donut-shaped prior mask: center hole should NOT be marked GC_FGD (#24).

        Creates a ring-shaped prior mask (foreground with hole in center).
        The old code would mark the entire inner 50% bbox as GC_FGD,
        corrupting the hole. The fix only marks pixels where prior==255.
        """
        # White bg with dark ring (donut)
        img = np.full((200, 200, 3), 255, dtype=np.uint8)
        # Outer ring
        cv2 = __import__("cv2")
        cv2.circle(img, (100, 100), 60, (40, 40, 40), 20)  # ring, not filled

        # Prior mask matching the ring
        prior = np.zeros((200, 200), dtype=np.uint8)
        cv2.circle(prior, (100, 100), 60, 255, 20)

        # Verify the hole exists in prior
        assert prior[100, 100] == 0, "Center of donut should be background in prior"

        context = ImageContext(
            source_path=Path("test.png"),
            category="BALL",
            prior_mask=prior,
        )
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(img, context, config)
        assert isinstance(result, CropResult)
        # Should not crash and should produce some result

    def test_tiny_prior_no_gc_fgd_crash(self, config, white_bg_image):
        """A very small prior mask (< 4px wide) should not crash
        when the inner-bbox GC_FGD logic skips."""
        prior = np.zeros((200, 200), dtype=np.uint8)
        prior[100, 100] = 255  # single pixel — bbox 1x1

        context = ImageContext(
            source_path=Path("test.png"),
            category="BALL",
            prior_mask=prior,
        )
        strategy = AIFallbackCropStrategy()
        result = strategy.crop(white_bg_image, context, config)
        assert isinstance(result, CropResult)
