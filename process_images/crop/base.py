"""Abstract base class for crop strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..config import PipelineConfig
from ..models import CropResult, ImageContext


class CropStrategy(ABC):
    """Interface for all crop strategies (classical, AI fallback, etc.).

    Each strategy receives a loaded image, its processing context, and the
    pipeline configuration.  It returns a CropResult containing the mask,
    bounding box, cropped/final images, metrics, and any flags raised
    during processing.
    """

    @abstractmethod
    def crop(
        self,
        image: np.ndarray,
        context: ImageContext,
        config: PipelineConfig,
    ) -> CropResult:
        """Execute the crop strategy on a single image.

        Args:
            image: RGB or RGBA numpy array.
            context: Processing context (source path, category, mapping).
            config: Full pipeline configuration.

        Returns:
            CropResult with mask, bbox, final image, metrics and flags.
        """
