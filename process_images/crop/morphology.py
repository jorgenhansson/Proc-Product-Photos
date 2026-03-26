"""Morphological operations and connected component analysis."""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from ..models import BBox


def clean_mask(
    mask: np.ndarray,
    kernel_size: int = 5,
    iterations: int = 2,
) -> np.ndarray:
    """Clean binary mask using morphological close then open.

    Close fills small holes, open removes small noise regions.
    """
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    result = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, kernel, iterations=iterations
    )
    result = cv2.morphologyEx(
        result, cv2.MORPH_OPEN, kernel, iterations=max(1, iterations - 1)
    )
    return result


def find_main_component(
    mask: np.ndarray,
    min_size: int = 500,
) -> tuple[np.ndarray, int]:
    """Find the largest connected component above min_size.

    Returns:
        (filtered_mask, significant_count) where significant_count is the
        number of components >= min_size.
    """
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )

    if num_labels <= 1:
        return mask, 0

    # Skip label 0 (background)
    areas = stats[1:, cv2.CC_STAT_AREA]

    significant = [
        (i + 1, int(a)) for i, a in enumerate(areas) if a >= min_size
    ]

    if not significant:
        return np.zeros_like(mask), 0

    significant.sort(key=lambda x: x[1], reverse=True)

    main_label = significant[0][0]
    filtered = ((labels == main_label) * 255).astype(np.uint8)

    return filtered, len(significant)


def compute_bbox(mask: np.ndarray) -> Optional[BBox]:
    """Compute bounding box of non-zero pixels in mask.

    Returns None if mask has no non-zero pixels.
    """
    coords = cv2.findNonZero(mask)
    if coords is None:
        return None
    x, y, w, h = cv2.boundingRect(coords)
    return BBox(x, y, w, h)


def detect_thin_object(bbox: BBox, threshold: float = 5.0) -> bool:
    """Detect if bounding box suggests an extremely thin/elongated object.

    Returns True if the aspect ratio (max/min dimension) exceeds threshold.
    Useful for detecting golf club shafts.
    """
    if bbox.w == 0 or bbox.h == 0:
        return False
    ratio = max(bbox.w, bbox.h) / max(1, min(bbox.w, bbox.h))
    return ratio > threshold


def count_significant_components(
    mask: np.ndarray,
    min_size: int = 500,
) -> int:
    """Count connected components above minimum pixel size."""
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    if num_labels <= 1:
        return 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    return sum(1 for a in areas if a >= min_size)
