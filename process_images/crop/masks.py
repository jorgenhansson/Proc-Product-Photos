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


def rgb_to_lab(image: np.ndarray) -> np.ndarray:
    """Convert RGB image to LAB color space.

    Use this to pre-compute LAB once and pass it to mask_from_white_bg()
    and mask_from_white_bg_edge_enhanced() to avoid redundant conversion.

    Args:
        image: RGB array of shape (H, W, 3), dtype uint8.

    Returns:
        LAB array of same shape, dtype uint8 (OpenCV LAB scale).
    """
    return cv2.cvtColor(image[:, :, :3].astype(np.uint8), cv2.COLOR_RGB2LAB)


def mask_from_white_bg(
    image: np.ndarray,
    distance_threshold: float = 12.0,
    bias: float = 0.0,
    precomputed_lab: np.ndarray | None = None,
) -> np.ndarray:
    """Generate mask by weighted LAB-space distance to white.

    Uses CIELAB color space with L channel weighted at 0.5 to tolerate
    brightness variation (shadows) while strongly separating chrominance
    (colored products) from white backgrounds.

    Pixels farther from white than (distance_threshold + bias) are
    considered foreground.

    Args:
        precomputed_lab: Optional pre-converted LAB array from rgb_to_lab().
            Avoids redundant RGB→LAB conversion when detect_background_type
            already computed it.
    """
    if precomputed_lab is not None:
        dist = _lab_array_distance_to_white(precomputed_lab)
    else:
        rgb = image[:, :, :3]
        dist = _lab_distance_to_white(rgb)
    effective_threshold = max(1.0, distance_threshold + bias)
    return (dist > effective_threshold).astype(np.uint8) * 255


def _lab_array_distance_to_white(lab: np.ndarray) -> np.ndarray:
    """Compute weighted distance to white from a pre-converted LAB array.

    Args:
        lab: LAB array of shape (H, W, 3) or (N, 3), dtype uint8.

    Returns:
        Distance array of spatial shape, dtype float32.
    """
    original_shape = lab.shape[:-1]
    flat = lab.reshape(-1, 3).astype(np.float32)

    white_lab = np.array([255.0, 128.0, 128.0])
    weights = np.array([0.5, 1.0, 1.0])

    diff = (flat - white_lab) * weights
    dist = np.sqrt(np.sum(diff ** 2, axis=1))

    return dist.reshape(original_shape)


def _lab_distance_to_white(pixels: np.ndarray) -> np.ndarray:
    """Compute weighted LAB distance from RGB pixels to pure white.

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

    white_lab = np.array([255.0, 128.0, 128.0])
    weights = np.array([0.5, 1.0, 1.0])

    diff = (lab - white_lab) * weights
    dist = np.sqrt(np.sum(diff ** 2, axis=1))

    return dist.reshape(original_shape)


def mask_from_white_bg_edge_enhanced(
    image: np.ndarray,
    distance_threshold: float = 12.0,
    bias: float = 0.0,
    canny_low: int = 30,
    canny_high: int = 100,
    dilate_iterations: int = 3,
    precomputed_lab: np.ndarray | None = None,
) -> np.ndarray:
    """Generate mask combining LAB distance with Canny edge detection.

    Designed for white/near-white objects on white backgrounds (e.g. golf
    balls) where color-distance alone cannot separate fg from bg.

    Strategy:
    1. Standard LAB distance mask (catches colored areas: logos, text)
    2. Canny edge detection (catches shape boundary via gradient)
    3. Dilate edges to close gaps
    4. Flood-fill from edges to create filled contour mask
    5. Union of distance mask and contour mask

    Args:
        precomputed_lab: Optional pre-converted LAB array from rgb_to_lab().
    """
    rgb = image[:, :, :3]
    h, w = rgb.shape[:2]

    # 1. Standard distance mask (use precomputed LAB if available)
    if precomputed_lab is not None:
        dist = _lab_array_distance_to_white(precomputed_lab)
    else:
        dist = _lab_distance_to_white(rgb)
    effective_threshold = max(1.0, distance_threshold + bias)
    distance_mask = (dist > effective_threshold).astype(np.uint8) * 255

    # 2. Canny edge detection
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Light blur to reduce noise while preserving object edges
    blurred = cv2.GaussianBlur(gray, (5, 5), 1.0)
    edges = cv2.Canny(blurred, canny_low, canny_high)

    # 3. Dilate edges to close gaps in the contour
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    edges_dilated = cv2.dilate(edges, kernel, iterations=dilate_iterations)

    # 4. Find contours and fill them to create solid mask regions
    contour_mask = np.zeros((h, w), dtype=np.uint8)
    contours, _ = cv2.findContours(
        edges_dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    # Only fill contours that are large enough to be a product
    min_contour_area = h * w * 0.002  # 0.2% of image
    for contour in contours:
        if cv2.contourArea(contour) >= min_contour_area:
            cv2.drawContours(contour_mask, [contour], -1, 255, cv2.FILLED)

    # 5. Union of both masks
    combined = cv2.bitwise_or(distance_mask, contour_mask)

    return combined


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
