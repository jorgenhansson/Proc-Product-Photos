"""Mask generation for different background types."""

from __future__ import annotations

import cv2
import numpy as np

from ..config import GlobalConfig
from ..models import BackgroundType


def detect_background_type(
    image: np.ndarray, config: GlobalConfig
) -> BackgroundType:
    """Detect whether image has transparent, white, or complex background.

    Detection order:
    1. If RGBA with meaningful alpha variation -> TRANSPARENT
    2. If border pixels are predominantly white -> WHITE_BG
    3. Otherwise -> COMPLEX_BG
    """
    # Check for alpha channel — require meaningful variation, not just
    # a single low pixel (guards against near-255 alpha from lossy workflows)
    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3]
        alpha_range = int(alpha.max()) - int(alpha.min())
        transparent_fraction = float(np.mean(alpha < 128))
        if alpha_range > 100 and transparent_fraction > 0.01:
            return BackgroundType.TRANSPARENT

    # Check border whiteness using LAB-space distance
    # LAB separates lightness from chrominance, giving better
    # discrimination between white background and colored products
    h, w = image.shape[:2]
    border_size = max(5, min(h, w) // 20)

    rgb_img = image[:, :, :3]
    top = rgb_img[:border_size, :]
    bottom = rgb_img[-border_size:, :]
    left = rgb_img[:, :border_size]
    right = rgb_img[:, -border_size:]

    border_rgb = np.concatenate(
        [
            top.reshape(-1, 3),
            bottom.reshape(-1, 3),
            left.reshape(-1, 3),
            right.reshape(-1, 3),
        ],
        axis=0,
    )
    distances = _lab_distance_to_white(border_rgb)
    white_ratio = float(np.mean(distances < config.white_distance_threshold))

    if white_ratio >= config.edge_whiteness_threshold:
        return BackgroundType.WHITE_BG

    return BackgroundType.COMPLEX_BG


def mask_from_alpha(
    image: np.ndarray, threshold: int = 128
) -> np.ndarray:
    """Generate binary mask from alpha channel.

    Pixels with alpha > threshold are considered foreground (255).
    """
    if image.ndim != 3 or image.shape[2] < 4:
        raise ValueError("Image has no alpha channel")
    alpha = image[:, :, 3]
    return (alpha > threshold).astype(np.uint8) * 255


def mask_from_white_bg(
    image: np.ndarray,
    distance_threshold: float = 12.0,
    bias: float = 0.0,
) -> np.ndarray:
    """Generate mask by weighted LAB-space distance to white.

    Uses CIELAB color space with L channel weighted at 0.5 to tolerate
    brightness variation (shadows) while strongly separating chrominance
    (colored products) from white backgrounds.

    Pixels farther from white than (distance_threshold + bias) are
    considered foreground.
    """
    rgb = image[:, :, :3]
    dist = _lab_distance_to_white(rgb)
    effective_threshold = max(1.0, distance_threshold + bias)
    return (dist > effective_threshold).astype(np.uint8) * 255


def _lab_distance_to_white(pixels: np.ndarray) -> np.ndarray:
    """Compute weighted LAB distance from pixels to pure white.

    Args:
        pixels: RGB array of shape (..., 3), dtype uint8.

    Returns:
        Distance array of same spatial shape, dtype float32.
        L is weighted at 0.5, a and b at 1.0.
    """
    original_shape = pixels.shape[:-1]

    # Reshape for cvtColor which needs (N, 1, 3)
    flat = pixels.reshape(-1, 1, 3).astype(np.uint8)
    lab = cv2.cvtColor(flat, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab = lab.reshape(-1, 3)

    # OpenCV LAB: L=[0,255], a=[0,255], b=[0,255]; white = (255, 128, 128)
    white_lab = np.array([255.0, 128.0, 128.0])
    weights = np.array([0.5, 1.0, 1.0])

    diff = (lab - white_lab) * weights
    dist = np.sqrt(np.sum(diff ** 2, axis=1))

    return dist.reshape(original_shape)


def mask_from_complex_bg(
    image: np.ndarray,
    block_size: int = 21,
    constant_c: float = 10.0,
) -> np.ndarray:
    """Generate raw mask using adaptive thresholding for complex backgrounds.

    Produces a binary mask via adaptive Gaussian thresholding.
    Morphological cleanup is NOT done here — it is handled by
    ``clean_mask()`` in the pipeline, which applies category-aware
    kernel size and thin-object protection consistently across all
    background types.

    Args:
        block_size: Neighbourhood size for adaptive thresholding (must be odd).
        constant_c: Constant subtracted from mean in adaptive threshold.
    """
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    # Ensure block_size is odd and >= 3
    bs = max(3, block_size)
    if bs % 2 == 0:
        bs += 1
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=bs,
        C=constant_c,
    )
