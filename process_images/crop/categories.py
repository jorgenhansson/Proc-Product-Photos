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
        min_output_px=30,
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
        threshold_bias=-6.0,
        morph_kernel_size=5,
        min_component_size=200,
        min_object_ratio=0.001,
        min_bbox_ratio=0.005,
        target_fill_ratio_min=0.20,
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
        min_component_size=50,
        min_object_ratio=0.0001,
        min_bbox_ratio=0.0003,
        target_fill_ratio_min=0.10,
        target_fill_ratio_max=0.70,
        min_output_px=100,
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
    """Resolve category config: hardcoded default ← YAML override (merged).

    Priority: hardcoded category default provides base values, then
    YAML-specified fields override them.  This ensures new fields
    added to CATEGORY_DEFAULTS are always available even if the YAML
    was written before those fields existed.

    Args:
        category: Category name from mapping.
        yaml_categories: Category configs parsed from YAML rules.

    Returns:
        The most specific CategoryConfig available.
    """
    from dataclasses import fields as dc_fields

    base = CATEGORY_DEFAULTS.get(category, CategoryConfig(name=category))

    if category not in yaml_categories:
        return base

    yaml_cfg = yaml_categories[category]

    # Merge: hardcoded category default ← YAML explicit values.
    # We detect "explicitly set in YAML" by checking which fields in
    # the raw YAML dict differ from what global inheritance alone would
    # produce.  Fields that only have global-inherited values should
    # NOT override hardcoded category defaults.
    #
    # Strategy: start from base (hardcoded), overlay yaml_cfg fields
    # that were explicitly written in the YAML (not just inherited from
    # global).  We approximate this by comparing yaml_cfg against a
    # CategoryConfig built only from global inheritance (no YAML).
    from ..config import GlobalConfig, _inherit_global_to_category
    global_only = CategoryConfig(**{
        **_inherit_global_to_category(GlobalConfig()),
        "name": category,
    })

    merged_kwargs = {}
    for f in dc_fields(CategoryConfig):
        base_val = getattr(base, f.name)
        yaml_val = getattr(yaml_cfg, f.name)
        global_only_val = getattr(global_only, f.name)
        # If YAML value differs from what pure-global inheritance gives,
        # it was explicitly set in the YAML → use it.
        if yaml_val != global_only_val:
            merged_kwargs[f.name] = yaml_val
        else:
            # Not explicitly set in YAML → use hardcoded category default
            merged_kwargs[f.name] = base_val

    merged_kwargs["name"] = category
    return CategoryConfig(**merged_kwargs)
