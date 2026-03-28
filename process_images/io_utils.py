"""Image I/O utilities: loading, saving, format normalization, file discovery."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def discover_images(input_dir: Path) -> list[Path]:
    """Find all supported image files in a directory (non-recursive).

    Returns paths sorted by name for deterministic ordering.
    """
    files: list[Path] = []
    for item in input_dir.iterdir():
        if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(item)
    return sorted(files, key=lambda p: p.name.lower())


def load_image(path: Path) -> Optional[np.ndarray]:
    """Load an image file and return as RGB or RGBA numpy array.

    Returns None on failure (logged as error).
    """
    try:
        img = Image.open(path)
        img.load()  # Force full decode (handles lazy TIFF loading)
        # Warn about multi-page TIFFs — only first page is processed
        n_frames = getattr(img, "n_frames", 1)
        if n_frames > 1:
            logger.warning(
                "%s has %d pages — only page 1 will be processed",
                path.name, n_frames,
            )
        img = ImageOps.exif_transpose(img)  # Apply EXIF orientation
        img = _normalize_mode(img)
        return np.array(img)
    except Exception as e:
        logger.error("Failed to load image %s: %s", path, e)
        return None


def _normalize_mode(img: Image.Image) -> Image.Image:
    """Convert any Pillow image mode to RGB or RGBA."""
    if img.mode == "RGBA":
        return img
    if img.mode == "LA":
        return img.convert("RGBA")
    if img.mode == "PA":
        return img.convert("RGBA")
    if img.mode == "P":
        if "transparency" in img.info:
            return img.convert("RGBA")
        return img.convert("RGB")
    if img.mode in ("L", "1"):
        return img.convert("RGB")
    if img.mode == "CMYK":
        return img.convert("RGB")
    if img.mode in ("I", "I;16"):
        arr = np.array(img, dtype=np.float64)
        max_val = arr.max()
        if max_val > 0:
            arr = (arr / max_val * 255).astype(np.uint8)
        else:
            arr = arr.astype(np.uint8)
        return Image.fromarray(arr).convert("RGB")
    if img.mode == "RGB":
        return img
    # Fallback: attempt conversion
    return img.convert("RGB")


def has_alpha(img: np.ndarray) -> bool:
    """Check if a numpy image array has an alpha channel."""
    return img.ndim == 3 and img.shape[2] == 4


SUPPORTED_OUTPUT_FORMATS = {"jpg", "jpeg", "png", "webp", "tiff", "tif"}


def save_image(
    img: np.ndarray,
    path: Path,
    quality: int = 95,
    output_format: str = "jpg",
) -> None:
    """Save numpy array in the requested format.

    Args:
        img: Image as numpy array (RGB or RGBA).
        path: Output path (extension is informational — format arg controls codec).
        quality: JPEG/WebP quality 1-100.
        output_format: One of jpg, png, webp, tiff.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pil_img = Image.fromarray(img)

    fmt = output_format.lower().replace("jpeg", "jpg").replace("tif", "tiff")

    if fmt in ("jpg", "webp"):
        # JPEG and WebP don't support alpha — flatten onto white
        if pil_img.mode == "RGBA":
            bg = Image.new("RGB", pil_img.size, (255, 255, 255))
            bg.paste(pil_img, mask=pil_img.split()[3])
            pil_img = bg
        elif pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

    if fmt == "jpg":
        pil_img.save(path, format="JPEG", quality=quality, optimize=True)
    elif fmt == "webp":
        pil_img.save(path, format="WEBP", quality=quality)
    elif fmt == "png":
        pil_img.save(path, format="PNG", optimize=True)
    elif fmt == "tiff":
        pil_img.save(path, format="TIFF")
    else:
        # Fallback: let Pillow guess from extension
        pil_img.save(path, quality=quality)


def encode_image(
    img: np.ndarray,
    quality: int = 95,
    output_format: str = "jpg",
) -> bytes:
    """Encode numpy array to image bytes without writing to disk.

    Used by parallel workers to avoid shipping numpy arrays between
    processes — encode in the worker, write bytes on the main thread.
    """
    import io

    pil_img = Image.fromarray(img)
    fmt = output_format.lower().replace("jpeg", "jpg").replace("tif", "tiff")

    if fmt in ("jpg", "webp"):
        if pil_img.mode == "RGBA":
            bg = Image.new("RGB", pil_img.size, (255, 255, 255))
            bg.paste(pil_img, mask=pil_img.split()[3])
            pil_img = bg
        elif pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")

    buf = io.BytesIO()
    if fmt == "jpg":
        pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
    elif fmt == "webp":
        pil_img.save(buf, format="WEBP", quality=quality)
    elif fmt == "png":
        pil_img.save(buf, format="PNG", optimize=True)
    elif fmt == "tiff":
        pil_img.save(buf, format="TIFF")
    else:
        pil_img.save(buf, quality=quality)

    return buf.getvalue()


def save_jpeg(
    img: np.ndarray, path: Path, quality: int = 95
) -> None:
    """Save numpy array as JPEG. Convenience wrapper around save_image."""
    save_image(img, path, quality=quality, output_format="jpg")


def save_png(img: np.ndarray, path: Path) -> None:
    """Save numpy array as PNG (used for mask visualizations)."""
    save_image(img, path, output_format="png")
