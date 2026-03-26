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
    skip_open: bool = False,
) -> np.ndarray:
    """Clean binary mask using morphological close then open.

    Close fills small holes, open removes small noise regions.

    Args:
        skip_open: If True, skip the morphological open step.  Use this
                   for thin-object categories (e.g. golf clubs) where
                   the open operation would delete narrow shafts.
    """
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
    )
    result = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, kernel, iterations=iterations
    )
    if not skip_open:
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


def merge_collinear_components(
    mask: np.ndarray,
    min_size: int = 100,
    collinearity_threshold: float = 0.15,
) -> np.ndarray:
    """Merge connected components whose centroids are roughly collinear.

    Designed for golf clubs where the shaft and head may be detected as
    separate components.  If 3+ components have centroids that fit a line
    well (low residual), or if 2 large components are roughly aligned
    vertically or horizontally, they are merged into a single mask.

    Args:
        min_size: Minimum component area to consider.
        collinearity_threshold: Maximum normalized residual to consider
            centroids as collinear (0 = perfect line, 1 = random).

    Returns:
        Mask with collinear components merged.
    """
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )

    if num_labels <= 2:
        # 0 or 1 foreground component — nothing to merge
        return mask

    # Gather significant components (skip label 0 = background)
    components = []
    for i in range(1, num_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area >= min_size:
            cx, cy = centroids[i]
            components.append((i, area, cx, cy))

    if len(components) < 2:
        return mask

    # For 2 components: check if they are roughly vertically or
    # horizontally aligned (common for club head + shaft)
    if len(components) == 2:
        _, _, cx1, cy1 = components[0]
        _, _, cx2, cy2 = components[1]
        dx = abs(cx1 - cx2)
        dy = abs(cy1 - cy2)
        span = max(dx, dy, 1.0)
        # Aligned if the off-axis displacement is small relative to span
        off_axis = min(dx, dy)
        if off_axis / span < collinearity_threshold * 3:
            merged = np.zeros_like(mask)
            for label, _, _, _ in components:
                merged[labels == label] = 255
            return merged
        return mask

    # For 3+ components: fit a line through centroids, check residual
    pts = np.array([(cx, cy) for _, _, cx, cy in components])
    mean = pts.mean(axis=0)
    centered = pts - mean

    # SVD to find principal axis
    _, s, vt = np.linalg.svd(centered, full_matrices=False)
    if s[0] == 0:
        return mask

    # Residual: ratio of minor to major singular value
    residual = s[1] / s[0] if len(s) > 1 else 0.0

    if residual < collinearity_threshold:
        # Components are collinear — merge all significant ones
        merged = np.zeros_like(mask)
        for label, _, _, _ in components:
            merged[labels == label] = 255
        return merged

    return mask
