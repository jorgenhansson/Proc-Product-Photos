"""Category taxonomy and per-category configuration resolution.

Hardcoded defaults encode sensible starting points for each golf product
category.  They can be overridden per-category in the YAML rules file.
"""

from __future__ import annotations

from ..config import CategoryConfig

CATEGORY_DEFAULTS: dict[str, CategoryConfig] = {
    "CLUB_LONG": CategoryConfig(
        name="CLUB_LONG",
        margin_pct=0.08,
        threshold_bias=-2.0,
        morph_kernel_size=3,
        morph_iterations=2,
        min_component_size=300,
        target_fill_ratio_min=0.30,
        target_fill_ratio_max=0.85,
        thin_object_protection=True,
        expected_aspect_ratio_min=2.0,
        expected_aspect_ratio_max=25.0,
    ),
    "CLUB_HEAD_ONLY": CategoryConfig(
        name="CLUB_HEAD_ONLY",
        margin_pct=0.06,
        morph_kernel_size=5,
        min_component_size=400,
        target_fill_ratio_min=0.35,
        target_fill_ratio_max=0.85,
        expected_aspect_ratio_min=1.0,
        expected_aspect_ratio_max=3.0,
    ),
    "BALL": CategoryConfig(
        name="BALL",
        margin_pct=0.12,
        morph_kernel_size=5,
        target_fill_ratio_min=0.30,
        target_fill_ratio_max=0.75,
        expected_aspect_ratio_min=1.0,
        expected_aspect_ratio_max=1.5,
    ),
    "SHOE": CategoryConfig(
        name="SHOE",
        margin_pct=0.06,
        centering_bias_y=0.02,
        target_fill_ratio_min=0.35,
        target_fill_ratio_max=0.85,
        expected_aspect_ratio_min=1.3,
        expected_aspect_ratio_max=4.0,
    ),
    "BAG": CategoryConfig(
        name="BAG",
        margin_pct=0.05,
        target_fill_ratio_min=0.40,
        target_fill_ratio_max=0.90,
        expected_aspect_ratio_min=1.0,
        expected_aspect_ratio_max=3.5,
    ),
    "APPAREL_FOLDED": CategoryConfig(
        name="APPAREL_FOLDED",
        margin_pct=0.06,
        target_fill_ratio_min=0.30,
        target_fill_ratio_max=0.80,
        expected_aspect_ratio_min=1.0,
        expected_aspect_ratio_max=3.0,
    ),
    "APPAREL_WORN_OR_SHAPED": CategoryConfig(
        name="APPAREL_WORN_OR_SHAPED",
        margin_pct=0.08,
        target_fill_ratio_min=0.25,
        target_fill_ratio_max=0.85,
        expected_aspect_ratio_min=1.0,
        expected_aspect_ratio_max=4.0,
    ),
    "ACCESSORY_SMALL": CategoryConfig(
        name="ACCESSORY_SMALL",
        margin_pct=0.10,
        min_component_size=200,
        target_fill_ratio_min=0.20,
        target_fill_ratio_max=0.70,
    ),
    "BOX_OR_PACKAGING": CategoryConfig(
        name="BOX_OR_PACKAGING",
        margin_pct=0.04,
        target_fill_ratio_min=0.40,
        target_fill_ratio_max=0.90,
        expected_aspect_ratio_min=1.0,
        expected_aspect_ratio_max=3.0,
    ),
}


def resolve_category(
    category: str,
    yaml_categories: dict[str, CategoryConfig],
) -> CategoryConfig:
    """Resolve category config with priority: YAML override > hardcoded default > generic.

    Args:
        category: Category name from mapping.
        yaml_categories: Category configs parsed from YAML rules.

    Returns:
        The most specific CategoryConfig available.
    """
    if category in yaml_categories:
        return yaml_categories[category]
    if category in CATEGORY_DEFAULTS:
        return CATEGORY_DEFAULTS[category]
    return CategoryConfig(name=category)
