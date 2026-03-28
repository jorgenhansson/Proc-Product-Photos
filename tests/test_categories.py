"""Tests for category taxonomy and config resolution."""

from __future__ import annotations

import pytest

from process_images.config import CategoryConfig, PipelineConfig, load_config
from process_images.crop.categories import CATEGORY_DEFAULTS, resolve_category


class TestCategoryDefaults:
    def test_all_expected_categories_present(self):
        expected = {
            "CLUB_LONG",
            "CLUB_HEAD_ONLY",
            "BALL",
            "SHOE",
            "BAG",
            "APPAREL_FOLDED",
            "APPAREL_WORN_OR_SHAPED",
            "ACCESSORY_SMALL",
            "BOX_OR_PACKAGING",
        }
        assert expected == set(CATEGORY_DEFAULTS.keys())

    def test_club_long_has_thin_protection(self):
        cfg = CATEGORY_DEFAULTS["CLUB_LONG"]
        assert cfg.thin_object_protection is True

    def test_ball_has_symmetric_margin(self):
        cfg = CATEGORY_DEFAULTS["BALL"]
        assert cfg.centering_bias_x == 0.0
        assert cfg.centering_bias_y == 0.0

    def test_shoe_zero_margin(self):
        """In zero-margin mode, SHOE has no margin and no bias."""
        cfg = CATEGORY_DEFAULTS["SHOE"]
        assert cfg.margin_pct == 0.02
        assert cfg.edge_proximity_px == 0


class TestResolveCategory:
    def test_yaml_override_takes_precedence(self):
        yaml_cats = {
            "BALL": CategoryConfig(name="BALL", margin_pct=0.20),
        }
        result = resolve_category("BALL", yaml_cats)
        assert result.margin_pct == 0.20

    def test_hardcoded_default_used_when_no_yaml(self):
        result = resolve_category("CLUB_LONG", {})
        assert result.name == "CLUB_LONG"
        assert result.thin_object_protection is True

    def test_unknown_category_returns_generic(self):
        result = resolve_category("UNKNOWN_THING", {})
        assert result.name == "UNKNOWN_THING"
        assert result.margin_pct == 0.05  # generic default


class TestConfigLoading:
    def test_load_rules_yaml(self, sample_rules_yaml):
        config = load_config(sample_rules_yaml)
        assert config.global_config.canvas_size == 200
        assert config.fallback.enabled is True
        assert "CLUB_LONG" in config.categories
        assert config.categories["CLUB_LONG"].thin_object_protection is True

    def test_category_inherits_global(self, sample_rules_yaml):
        config = load_config(sample_rules_yaml)
        ball = config.categories["BALL"]
        # morph_kernel_size not overridden in BALL, should come from global
        assert ball.morph_kernel_size == config.global_config.morph_kernel_size

    def test_all_shared_fields_auto_inherited(self, tmp_path):
        """Every field shared between GlobalConfig and CategoryConfig
        should be automatically inherited without manual parser code.
        """
        from dataclasses import fields as dc_fields
        from process_images.config import GlobalConfig, CategoryConfig

        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "global:\n  adaptive_block_size: 99\n  adaptive_c: 42.0\n"
            "  edge_proximity_px: 17\n"
            "categories:\n  TEST_CAT:\n    margin_pct: 0.1\n",
            encoding="utf-8",
        )
        config = load_config(yaml_path)
        cat = config.categories["TEST_CAT"]
        gc = config.global_config

        # All fields that exist on both should match global
        shared = {
            f.name for f in dc_fields(GlobalConfig)
        } & {f.name for f in dc_fields(CategoryConfig)}
        for field_name in shared:
            assert getattr(cat, field_name) == getattr(gc, field_name), (
                f"{field_name}: category={getattr(cat, field_name)} "
                f"!= global={getattr(gc, field_name)}"
            )

    def test_category_override_beats_global(self, tmp_path):
        """Category-specific YAML values should override global inheritance."""
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "global:\n  morph_kernel_size: 7\n"
            "categories:\n  BALL:\n    morph_kernel_size: 3\n",
            encoding="utf-8",
        )
        config = load_config(yaml_path)
        assert config.global_config.morph_kernel_size == 7
        assert config.categories["BALL"].morph_kernel_size == 3

    def test_target_fill_ratio_list_syntax(self, tmp_path):
        """target_fill_ratio: [0.3, 0.8] should set min and max."""
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "categories:\n  SHOE:\n    target_fill_ratio: [0.3, 0.8]\n",
            encoding="utf-8",
        )
        config = load_config(yaml_path)
        shoe = config.categories["SHOE"]
        assert shoe.target_fill_ratio_min == 0.3
        assert shoe.target_fill_ratio_max == 0.8

    def test_unknown_yaml_keys_ignored(self, tmp_path):
        """YAML keys that don't match CategoryConfig fields should be ignored."""
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "categories:\n  BAG:\n    margin_pct: 0.05\n    bogus_field: 999\n",
            encoding="utf-8",
        )
        config = load_config(yaml_path)
        assert config.categories["BAG"].margin_pct == 0.05
        assert not hasattr(config.categories["BAG"], "bogus_field")

    def test_default_category_config_inherits(self):
        """PipelineConfig._default_category_config should inherit from global."""
        from process_images.config import GlobalConfig, PipelineConfig
        gc = GlobalConfig(adaptive_block_size=77, adaptive_c=3.14)
        pc = PipelineConfig(global_config=gc)
        cat = pc._default_category_config("UNKNOWN")
        assert cat.adaptive_block_size == 77
        assert cat.adaptive_c == 3.14
        assert cat.name == "UNKNOWN"

    def test_unknown_yaml_category_warns(self, tmp_path, caplog):
        """YAML category not in CATEGORY_DEFAULTS should produce a warning (#27)."""
        import logging
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "categories:\n"
            "  BOLL:\n"
            "    margin_pct: 0.05\n"
            "  BALL:\n"
            "    margin_pct: 0.08\n"
            "  SHEO:\n"
            "    margin_pct: 0.02\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="process_images.config"):
            config = load_config(yaml_path)

        # BOLL and SHEO should warn, BALL should not
        warnings = [r.message for r in caplog.records]
        assert any("BOLL" in w for w in warnings), "Should warn about BOLL"
        assert any("SHEO" in w for w in warnings), "Should warn about SHEO"
        assert not any("'BALL'" in w for w in warnings), "Should NOT warn about BALL"
        # All three should still be loaded
        assert "BOLL" in config.categories
        assert "BALL" in config.categories
        assert "SHEO" in config.categories

    def test_known_yaml_category_no_warning(self, tmp_path, caplog):
        """All known categories should produce no warnings."""
        import logging
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "categories:\n"
            "  BALL:\n"
            "    margin_pct: 0.1\n"
            "  SHOE:\n"
            "    margin_pct: 0.05\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="process_images.config"):
            load_config(yaml_path)

        config_warnings = [r for r in caplog.records if "not in known defaults" in r.message]
        assert len(config_warnings) == 0


class TestTypeCoercion:
    """Tests for YAML type coercion in _apply_dataclass (#23)."""

    def test_string_to_int(self, tmp_path):
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text('global:\n  canvas_size: "1000"\n')
        config = load_config(yaml_path)
        assert config.global_config.canvas_size == 1000
        assert isinstance(config.global_config.canvas_size, int)

    def test_float_to_int(self, tmp_path):
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text("global:\n  jpeg_quality: 90.5\n")
        config = load_config(yaml_path)
        assert config.global_config.jpeg_quality == 90
        assert isinstance(config.global_config.jpeg_quality, int)

    def test_int_to_float(self, tmp_path):
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text("global:\n  edge_whiteness_threshold: 85\n")
        config = load_config(yaml_path)
        assert config.global_config.edge_whiteness_threshold == 85.0
        assert isinstance(config.global_config.edge_whiteness_threshold, float)

    def test_string_to_bool(self, tmp_path):
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text('global:\n  overwrite: "yes"\n')
        config = load_config(yaml_path)
        assert config.global_config.overwrite is True
        assert isinstance(config.global_config.overwrite, bool)

    def test_string_to_int_in_fallback(self, tmp_path):
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text('fallback:\n  grabcut_iterations: "7"\n')
        config = load_config(yaml_path)
        assert config.fallback.grabcut_iterations == 7
        assert isinstance(config.fallback.grabcut_iterations, int)

    def test_invalid_coercion_warns_and_keeps_value(self, tmp_path, caplog):
        import logging
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text('global:\n  canvas_size: big\n')
        with caplog.at_level(logging.WARNING, logger="process_images.config"):
            config = load_config(yaml_path)

        assert config.global_config.canvas_size == "big"  # kept as-is
        assert any("Cannot coerce" in r.message for r in caplog.records)

    def test_bool_field_stays_bool(self, tmp_path):
        """YAML native bool should stay bool, not be coerced to int."""
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text("fallback:\n  enabled: false\n")
        config = load_config(yaml_path)
        assert config.fallback.enabled is False
        assert isinstance(config.fallback.enabled, bool)

    def test_category_float_coercion(self, tmp_path):
        """Integer value for a float field in category config should be coerced."""
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text("categories:\n  BALL:\n    margin_pct: 5\n")
        config = load_config(yaml_path)
        ball = config.categories["BALL"]
        assert ball.margin_pct == 5.0
        assert isinstance(ball.margin_pct, float)

    def test_normal_types_unchanged(self, tmp_path):
        """Correctly typed YAML values should pass through unchanged."""
        yaml_path = tmp_path / "rules.yaml"
        yaml_path.write_text(
            "global:\n"
            "  canvas_size: 500\n"
            "  jpeg_quality: 85\n"
            "  edge_whiteness_threshold: 0.9\n"
            "  overwrite: true\n"
        )
        config = load_config(yaml_path)
        assert config.global_config.canvas_size == 500
        assert isinstance(config.global_config.canvas_size, int)
        assert config.global_config.jpeg_quality == 85
        assert config.global_config.edge_whiteness_threshold == 0.9
        assert config.global_config.overwrite is True
