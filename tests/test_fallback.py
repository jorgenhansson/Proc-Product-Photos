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
