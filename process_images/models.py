"""Core data models for the image processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np


class BackgroundType(Enum):
    """Detected background type of an input image."""

    TRANSPARENT = "transparent"
    WHITE_BG = "white_bg"
    COMPLEX_BG = "complex_bg"


class ProcessingStatus(Enum):
    """Final processing status for an image."""

    OK = "ok"
    FLAGGED = "flagged"
    FAILED = "failed"
    RECOVERED = "recovered"


class Flag(Enum):
    """Reason codes for flagged or failed images."""

    NO_OBJECT_FOUND = "no_object_found"
    MASK_TOO_SMALL = "mask_too_small"
    MASK_TOO_FRAGMENTED = "mask_too_fragmented"
    BBOX_TOO_LARGE = "bbox_too_large"
    BBOX_TOO_SMALL = "bbox_too_small"
    CROP_CATEGORY_INCONSISTENT = "crop_category_inconsistent"
    OBJECT_TOO_CLOSE_TO_EDGE = "object_too_close_to_edge"
    MULTIPLE_LARGE_COMPONENTS = "multiple_large_components"
    FILL_RATIO_TOO_LOW = "fill_ratio_too_low"
    FILL_RATIO_TOO_HIGH = "fill_ratio_too_high"
    MISSING_MAPPING = "missing_mapping"
    IMAGE_READ_ERROR = "image_read_error"
    NAMING_CONFLICT = "naming_conflict"


FLAG_DESCRIPTIONS: dict[Flag, str] = {
    Flag.NO_OBJECT_FOUND: "No product object detected in image",
    Flag.MASK_TOO_SMALL: "Detected object mask is suspiciously small",
    Flag.MASK_TOO_FRAGMENTED: "Object mask is too fragmented to crop reliably",
    Flag.BBOX_TOO_LARGE: "Bounding box covers suspiciously large portion of image",
    Flag.BBOX_TOO_SMALL: "Bounding box is suspiciously small",
    Flag.CROP_CATEGORY_INCONSISTENT: "Crop result inconsistent with category expectations",
    Flag.OBJECT_TOO_CLOSE_TO_EDGE: "Object is too close to canvas edge after placement",
    Flag.MULTIPLE_LARGE_COMPONENTS: "Multiple similarly-sized components detected",
    Flag.FILL_RATIO_TOO_LOW: "Object fills too little of the final canvas",
    Flag.FILL_RATIO_TOO_HIGH: "Object fills too much of the final canvas",
    Flag.MISSING_MAPPING: "No mapping entry found for this supplier SKU",
    Flag.IMAGE_READ_ERROR: "Failed to read or decode image file",
    Flag.NAMING_CONFLICT: "Output filename conflicts with another image",
}


@dataclass
class BBox:
    """Bounding box: (x, y) is top-left corner, (w, h) is size."""

    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def aspect_ratio(self) -> float:
        if self.h == 0:
            return 0.0
        return self.w / self.h

    def clamp(self, img_w: int, img_h: int) -> BBox:
        """Clamp bounding box to image dimensions."""
        x = max(0, self.x)
        y = max(0, self.y)
        x2 = min(img_w, self.x2)
        y2 = min(img_h, self.y2)
        return BBox(x, y, max(0, x2 - x), max(0, y2 - y))


@dataclass
class MappingRow:
    """One row from the SKU-to-article mapping file."""

    supplier_sku: str
    store_article: str
    suffix: str
    category: str
    variant: str = ""
    color: str = ""
    angle: str = ""
    notes: str = ""

    @property
    def output_filename(self) -> str:
        return f"{self.store_article}_{self.suffix}.jpg"


@dataclass
class CropMetrics:
    """Quantitative metrics from a crop operation."""

    fill_ratio: float = 0.0
    crop_area_ratio: float = 0.0
    margin_px: int = 0
    object_bbox: Optional[BBox] = None
    crop_bbox: Optional[BBox] = None
    object_pixel_count: int = 0
    component_count: int = 0


@dataclass
class CropResult:
    """Result of a single crop strategy execution."""

    mask: Optional[np.ndarray] = None
    object_bbox: Optional[BBox] = None
    crop_bbox: Optional[BBox] = None
    cropped_image: Optional[np.ndarray] = None
    final_image: Optional[np.ndarray] = None
    metrics: CropMetrics = field(default_factory=CropMetrics)
    flags: list[Flag] = field(default_factory=list)
    background_type: BackgroundType = BackgroundType.WHITE_BG


@dataclass
class ImageContext:
    """Full context for processing a single image."""

    source_path: Path
    mapping_rows: list[MappingRow] = field(default_factory=list)
    category: str = ""
    background_type: Optional[BackgroundType] = None
    prior_mask: Optional[np.ndarray] = None


@dataclass
class ProcessingResult:
    """Final result for one processed image."""

    source_path: Path
    status: ProcessingStatus = ProcessingStatus.FAILED
    output_paths: list[Path] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    crop_metrics: Optional[CropMetrics] = None
    background_type: Optional[BackgroundType] = None
    processing_time_s: float = 0.0
    fallback_attempted: bool = False
    fallback_time_s: float = 0.0
    error_message: str = ""
    category: str = ""
