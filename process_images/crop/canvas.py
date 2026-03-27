"""Final canvas placement: crop, resize, center, and compose on background."""

from __future__ import annotations

import numpy as np
from PIL import Image

from ..models import BBox


def crop_region(image: np.ndarray, bbox: BBox) -> np.ndarray:
    """Crop image to the bounding box region.

    Args:
        image: Source image (H, W, C).
        bbox: Region to extract.

    Returns:
        Cropped sub-image as a copy.
    """
    return image[bbox.y : bbox.y2, bbox.x : bbox.x2].copy()


def resize_to_fit(
    image: np.ndarray,
    canvas_size: int,
    fill_ratio_target: float = 1.0,
    min_output_px: int = 0,
) -> np.ndarray:
    """Resize image proportionally so its max dimension fills the target ratio.

    With fill_ratio_target=1.0 (zero-margin mode), the object's longest
    dimension will match the canvas size exactly.

    Upscaling is allowed when fill_ratio_target >= 0.95 (zero-margin)
    because the customer expects the product to fill the canvas.
    For lower fill targets, upscaling is only allowed if min_output_px
    forces it (prevents tiny products from being invisible).
    """
    h, w = image.shape[:2]
    target_dim = int(canvas_size * fill_ratio_target)

    scale = min(target_dim / max(1, w), target_dim / max(1, h))
    if scale > 1.0 and fill_ratio_target < 0.80:
        # Don't upscale unless min_output_px forces it.
        # With fill_ratio_target >= 0.80 (zero-margin and near-zero-margin
        # modes), upscaling is expected to fill the canvas.
        scale = min(1.0, canvas_size / max(1, max(w, h)))

    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))

    # Enforce minimum output size — allow controlled upscaling
    if min_output_px > 0 and max(new_w, new_h) < min_output_px:
        upscale = min_output_px / max(1, max(w, h))
        # Cap at canvas size
        upscale = min(upscale, canvas_size / max(1, max(w, h)))
        new_w = max(1, int(w * upscale))
        new_h = max(1, int(h * upscale))

    pil_img = Image.fromarray(image)
    resized = pil_img.resize((new_w, new_h), Image.LANCZOS)
    return np.array(resized)


def place_on_canvas(
    image: np.ndarray,
    canvas_size: int = 1000,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    bias_x: float = 0.0,
    bias_y: float = 0.0,
) -> np.ndarray:
    """Place an image centered on a square canvas.

    Args:
        image: RGB or RGBA source.
        canvas_size: Width and height of the square canvas.
        bg_color: Background fill color.
        bias_x: Horizontal centering bias (-1..1, fraction of canvas).
        bias_y: Vertical centering bias (-1..1, fraction of canvas).

    Returns:
        RGB canvas with image composited onto it.
    """
    h, w = image.shape[:2]
    canvas = np.full((canvas_size, canvas_size, 3), bg_color, dtype=np.uint8)

    x_offset = (canvas_size - w) // 2 + int(bias_x * canvas_size)
    y_offset = (canvas_size - h) // 2 + int(bias_y * canvas_size)

    # Clamp offsets
    x_offset = max(0, min(canvas_size - w, x_offset))
    y_offset = max(0, min(canvas_size - h, y_offset))

    if image.ndim == 3 and image.shape[2] == 4:
        # Alpha compositing
        rgb = image[:, :, :3].astype(np.float32)
        alpha = image[:, :, 3:4].astype(np.float32) / 255.0
        region = canvas[y_offset : y_offset + h, x_offset : x_offset + w].astype(
            np.float32
        )
        blended = (rgb * alpha + region * (1.0 - alpha)).astype(np.uint8)
        canvas[y_offset : y_offset + h, x_offset : x_offset + w] = blended
    else:
        src = image[:, :, :3] if image.ndim == 3 else np.stack([image] * 3, axis=2)
        canvas[y_offset : y_offset + h, x_offset : x_offset + w] = src

    return canvas


def compute_fill_ratio(
    object_size: tuple[int, int], canvas_size: int
) -> float:
    """Compute how much of the canvas the object fills (by max dimension).

    Args:
        object_size: (width, height) of the placed object.
        canvas_size: Side length of the square canvas.
    """
    if canvas_size <= 0:
        return 0.0
    return max(object_size) / canvas_size
