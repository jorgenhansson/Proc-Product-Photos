"""Configuration loading and management from YAML rules files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CategoryConfig:
    """Crop and processing configuration for a product category."""

    name: str = ""
    margin_pct: float = 0.05
    threshold_bias: float = 0.0
    morph_kernel_size: int = 5
    morph_iterations: int = 2
    min_component_size: int = 500
    target_fill_ratio_min: float = 0.25
    target_fill_ratio_max: float = 0.90
    centering_bias_x: float = 0.0
    centering_bias_y: float = 0.0
    thin_object_protection: bool = False
    shadow_tolerance: float = 10.0
    fallback_sensitivity: float = 0.5
    edge_proximity_px: int = 5
    adaptive_block_size: int = 21
    adaptive_c: float = 10.0


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
    max_bbox_ratio: float = 0.99
    min_bbox_ratio: float = 0.01
    morph_kernel_size: int = 5
    morph_iterations: int = 2
    min_component_size: int = 500
    edge_proximity_px: int = 5
    adaptive_block_size: int = 21
    adaptive_c: float = 10.0
    filename_pattern: str = "{store_article}_{suffix}.jpg"


@dataclass
class FallbackConfig:
    """Configuration for the AI/heuristic fallback path."""

    enabled: bool = True
    strategy: str = "grabcut"
    grabcut_iterations: int = 5
    max_attempts: int = 1


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
        cfg = CategoryConfig(name=category)
        g = self.global_config
        cfg.morph_kernel_size = g.morph_kernel_size
        cfg.morph_iterations = g.morph_iterations
        cfg.min_component_size = g.min_component_size
        cfg.edge_proximity_px = g.edge_proximity_px
        cfg.adaptive_block_size = g.adaptive_block_size
        cfg.adaptive_c = g.adaptive_c
        return cfg


def load_config(path: Path) -> PipelineConfig:
    """Load pipeline configuration from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = PipelineConfig()

    if "global" in raw:
        _apply_global(raw["global"], config.global_config)

    if "fallback" in raw:
        _apply_fallback(raw["fallback"], config.fallback)

    if "categories" in raw:
        for name, cat_raw in raw["categories"].items():
            cat = _build_category_config(name, cat_raw, config.global_config)
            config.categories[name] = cat

    return config


def _apply_global(raw: dict[str, Any], gc: GlobalConfig) -> None:
    gc.canvas_size = raw.get("canvas_size", gc.canvas_size)
    gc.jpeg_quality = raw.get("jpeg_quality", gc.jpeg_quality)
    bg = raw.get("background_color", list(gc.background_color))
    gc.background_color = tuple(bg)
    gc.white_distance_threshold = raw.get(
        "white_distance_threshold", gc.white_distance_threshold
    )
    gc.edge_whiteness_threshold = raw.get(
        "edge_whiteness_threshold", gc.edge_whiteness_threshold
    )
    gc.alpha_threshold = raw.get("alpha_threshold", gc.alpha_threshold)
    gc.min_object_ratio = raw.get("min_object_ratio", gc.min_object_ratio)
    gc.max_bbox_ratio = raw.get("max_bbox_ratio", gc.max_bbox_ratio)
    gc.min_bbox_ratio = raw.get("min_bbox_ratio", gc.min_bbox_ratio)
    gc.morph_kernel_size = raw.get("morph_kernel_size", gc.morph_kernel_size)
    gc.morph_iterations = raw.get("morph_iterations", gc.morph_iterations)
    gc.min_component_size = raw.get("min_component_size", gc.min_component_size)
    gc.edge_proximity_px = raw.get("edge_proximity_px", gc.edge_proximity_px)
    gc.adaptive_block_size = raw.get("adaptive_block_size", gc.adaptive_block_size)
    gc.adaptive_c = raw.get("adaptive_c", gc.adaptive_c)
    gc.filename_pattern = raw.get("filename_pattern", gc.filename_pattern)


def _apply_fallback(raw: dict[str, Any], fc: FallbackConfig) -> None:
    fc.enabled = raw.get("enabled", fc.enabled)
    fc.strategy = raw.get("strategy", fc.strategy)
    fc.grabcut_iterations = raw.get("grabcut_iterations", fc.grabcut_iterations)
    fc.max_attempts = raw.get("max_attempts", fc.max_attempts)


def _build_category_config(
    name: str, raw: dict[str, Any], g: GlobalConfig
) -> CategoryConfig:
    cfg = CategoryConfig(name=name)
    # Inherit global defaults
    cfg.morph_kernel_size = g.morph_kernel_size
    cfg.morph_iterations = g.morph_iterations
    cfg.min_component_size = g.min_component_size
    cfg.edge_proximity_px = g.edge_proximity_px

    # Override with category-specific values
    cfg.margin_pct = raw.get("margin_pct", cfg.margin_pct)
    cfg.threshold_bias = raw.get("threshold_bias", cfg.threshold_bias)
    cfg.morph_kernel_size = raw.get("morph_kernel_size", cfg.morph_kernel_size)
    cfg.morph_iterations = raw.get("morph_iterations", cfg.morph_iterations)
    cfg.min_component_size = raw.get("min_component_size", cfg.min_component_size)

    fill = raw.get(
        "target_fill_ratio",
        [cfg.target_fill_ratio_min, cfg.target_fill_ratio_max],
    )
    if isinstance(fill, list) and len(fill) == 2:
        cfg.target_fill_ratio_min = float(fill[0])
        cfg.target_fill_ratio_max = float(fill[1])

    cfg.centering_bias_x = raw.get("centering_bias_x", cfg.centering_bias_x)
    cfg.centering_bias_y = raw.get("centering_bias_y", cfg.centering_bias_y)
    cfg.thin_object_protection = raw.get(
        "thin_object_protection", cfg.thin_object_protection
    )
    cfg.shadow_tolerance = raw.get("shadow_tolerance", cfg.shadow_tolerance)
    cfg.fallback_sensitivity = raw.get(
        "fallback_sensitivity", cfg.fallback_sensitivity
    )
    cfg.edge_proximity_px = raw.get("edge_proximity_px", cfg.edge_proximity_px)
    # Inherit global adaptive threshold defaults, then allow category override
    cfg.adaptive_block_size = raw.get("adaptive_block_size", g.adaptive_block_size)
    cfg.adaptive_c = float(raw.get("adaptive_c", g.adaptive_c))

    return cfg
