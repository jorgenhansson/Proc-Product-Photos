"""Configuration loading and management from YAML rules files.

Uses dataclass introspection to avoid per-field parser lines.
Adding a new field to CategoryConfig or GlobalConfig automatically
makes it parseable from YAML — no parser updates needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CategoryConfig:
    """Crop and processing configuration for a product category.

    Margins can be specified per-side or uniformly:
    - margin_pct: uniform fallback (used if per-side value is < 0)
    - margin_top/bottom/left/right: per-side overrides (-1 = use margin_pct)

    Margin mode (margin_mode) controls how margin_pct is interpreted:
    - "object": margin relative to object bbox dimension (default, backwards compat)
    - "image": margin relative to image max dimension
    """

    name: str = ""
    margin_pct: float = 0.05
    margin_top: float = -1.0     # -1 = use margin_pct
    margin_bottom: float = -1.0
    margin_left: float = -1.0
    margin_right: float = -1.0
    margin_mode: str = "image"   # "image" or "object"
    threshold_bias: float = 0.0
    morph_kernel_size: int = 5
    morph_iterations: int = 2
    min_component_size: int = 500
    target_fill_ratio_min: float = 0.25
    target_fill_ratio_max: float = 0.90
    centering_bias_x: float = 0.0
    centering_bias_y: float = 0.0
    thin_object_protection: bool = False
    min_output_px: int = 50
    edge_proximity_px: int = 5
    min_object_ratio: float = 0.0  # 0 = use global default
    min_bbox_ratio: float = 0.0    # 0 = use global default
    expected_aspect_ratio_min: float = 1.0
    expected_aspect_ratio_max: float = 15.0
    adaptive_block_size: int = 21
    adaptive_c: float = 10.0

    def resolve_margins(self) -> tuple[float, float, float, float]:
        """Return (top, bottom, left, right) margin percentages.

        Per-side values override margin_pct.  A value of -1 means
        'use the uniform margin_pct'.
        """
        fallback = self.margin_pct
        return (
            fallback if self.margin_top < 0 else self.margin_top,
            fallback if self.margin_bottom < 0 else self.margin_bottom,
            fallback if self.margin_left < 0 else self.margin_left,
            fallback if self.margin_right < 0 else self.margin_right,
        )


@dataclass
class GlobalConfig:
    """Top-level pipeline configuration."""

    canvas_size: int = 1000
    jpeg_quality: int = 95
    background_color: tuple[int, int, int] = (255, 255, 255)
    white_distance_threshold: float = 12.0
    edge_whiteness_threshold: float = 0.85
    alpha_threshold: int = 128
    min_object_ratio: float = 0.005
    max_bbox_ratio: float = 1.0
    min_bbox_ratio: float = 0.01
    morph_kernel_size: int = 5
    morph_iterations: int = 2
    min_component_size: int = 500
    edge_proximity_px: int = 5
    adaptive_block_size: int = 21
    adaptive_c: float = 10.0
    output_format: str = "jpg"
    filename_pattern: str = "{source_stem}-cropped.{ext}"
    overwrite: bool = False


@dataclass
class FallbackConfig:
    """Configuration for the AI/heuristic fallback path."""

    enabled: bool = True
    strategy: str = "grabcut"
    grabcut_iterations: int = 5
    max_attempts: int = 1
    validation_tolerance: float = 0.8


@dataclass
class PipelineConfig:
    """Complete pipeline configuration assembled from YAML."""

    global_config: GlobalConfig = field(default_factory=GlobalConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    categories: dict[str, CategoryConfig] = field(default_factory=dict)

    def get_category_config(self, category: str) -> CategoryConfig:
        """Look up category config, falling back to global defaults."""
        if category in self.categories:
            return self.categories[category]
        return self._default_category_config(category)

    def _default_category_config(self, category: str) -> CategoryConfig:
        """Build a CategoryConfig inheriting shared fields from GlobalConfig."""
        inherited = _inherit_global_to_category(self.global_config)
        inherited["name"] = category
        return CategoryConfig(**inherited)


def load_config(path: Path) -> PipelineConfig:
    """Load pipeline configuration from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = PipelineConfig()

    if "global" in raw:
        _apply_dataclass(raw["global"], config.global_config)

    if "fallback" in raw:
        _apply_dataclass(raw["fallback"], config.fallback)

    if "categories" in raw:
        for name, cat_raw in raw["categories"].items():
            cat = _build_category_config(name, cat_raw or {}, config.global_config)
            config.categories[name] = cat

    return config


# ---------------------------------------------------------------------------
# Generic dataclass-aware parsers
# ---------------------------------------------------------------------------

_CATEGORY_FIELDS = {f.name for f in fields(CategoryConfig)}


def _apply_dataclass(raw: dict[str, Any], obj: object) -> None:
    """Apply YAML dict values to a dataclass instance.

    Only sets attributes that exist on the dataclass. Handles
    background_color tuple specially.
    """
    for f in fields(obj.__class__):
        if f.name not in raw:
            continue
        value = raw[f.name]
        if f.name == "background_color":
            value = tuple(value)
        setattr(obj, f.name, value)


def _inherit_global_to_category(g: GlobalConfig) -> dict[str, Any]:
    """Extract fields shared between GlobalConfig and CategoryConfig."""
    return {
        f.name: getattr(g, f.name)
        for f in fields(GlobalConfig)
        if f.name in _CATEGORY_FIELDS
    }


def _build_category_config(
    name: str, raw: dict[str, Any], g: GlobalConfig
) -> CategoryConfig:
    """Build a CategoryConfig from YAML with global inheritance.

    Priority: category YAML > global inherited > CategoryConfig defaults.
    Handles target_fill_ratio [min, max] list syntax.
    """
    # Start with global-inherited defaults for shared fields
    inherited = _inherit_global_to_category(g)

    # Handle target_fill_ratio list → two separate fields
    raw = dict(raw)  # copy to avoid mutating caller's dict
    fill = raw.pop("target_fill_ratio", None)

    # Merge: inherited < raw < name
    merged = {**inherited, **raw, "name": name}

    # Filter to valid CategoryConfig fields only
    valid = {k: v for k, v in merged.items() if k in _CATEGORY_FIELDS}
    cfg = CategoryConfig(**valid)

    # Apply fill ratio list if provided
    if isinstance(fill, list) and len(fill) == 2:
        cfg.target_fill_ratio_min = float(fill[0])
        cfg.target_fill_ratio_max = float(fill[1])

    return cfg
