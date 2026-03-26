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

    def test_shoe_has_vertical_bias(self):
        cfg = CATEGORY_DEFAULTS["SHOE"]
        assert cfg.centering_bias_y > 0


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
