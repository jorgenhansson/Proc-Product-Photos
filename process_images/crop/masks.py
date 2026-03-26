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

    # Check border whiteness
    rgb = image[:, :, :3].astype(np.float32)
    h, w = rgb.shape[:2]
    border_size = max(5, min(h, w) // 20)

    top = rgb[:border_size, :].reshape(-1, 3)
    bottom = rgb[-border_size:, :].reshape(-1, 3)
    left = rgb[:, :border_size].reshape(-1, 3)
    right = rgb[:, -border_size:].reshape(-1, 3)

    border_pixels = np.concatenate([top, bottom, left, right], axis=0)
    white = np.array([255.0, 255.0, 255.0])
    distances = np.sqrt(np.sum((border_pixels - white) ** 2, axis=1))
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
    distance_threshold: float = 30.0,
    bias: float = 0.0,
) -> np.ndarray:
    """Generate mask by thresholding Euclidean distance to pure white.

    Pixels farther from white than (distance_threshold + bias) are
    considered foreground.
    """
    rgb = image[:, :, :3].astype(np.float32)
    white = np.array([255.0, 255.0, 255.0])
    dist = np.sqrt(np.sum((rgb - white) ** 2, axis=2))
    effective_threshold = max(1.0, distance_threshold + bias)
    return (dist > effective_threshold).astype(np.uint8) * 255


def mask_from_complex_bg(
    image: np.ndarray,
    morph_kernel_size: int = 7,
    morph_iterations: int = 3,
    block_size: int = 21,
    constant_c: float = 10.0,
) -> np.ndarray:
    """Generate mask using adaptive thresholding for complex backgrounds.

    Uses adaptive Gaussian thresholding followed by heavier morphological
    operations to handle gradients and shadows.

    Args:
        block_size: Neighbourhood size for adaptive thresholding (must be odd).
        constant_c: Constant subtracted from mean in adaptive threshold.
    """
    gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    # Ensure block_size is odd and >= 3
    bs = max(3, block_size)
    if bs % 2 == 0:
        bs += 1
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=bs,
        C=constant_c,
    )
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (morph_kernel_size, morph_kernel_size)
    )
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE, kernel, iterations=morph_iterations
    )
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, kernel, iterations=max(1, morph_iterations - 1)
    )
    return binary
