"""Shared test fixtures: synthetic images, configs, mappings, temp dirs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from process_images.config import (
    CategoryConfig,
    FallbackConfig,
    GlobalConfig,
    PipelineConfig,
)
from process_images.models import MappingRow


# ---------------------------------------------------------------------------
# Synthetic images
# ---------------------------------------------------------------------------


@pytest.fixture
def white_bg_image() -> np.ndarray:
    """200x200 white image with a 60x60 dark square at center."""
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[70:130, 70:130] = [40, 40, 40]
    return img


@pytest.fixture
def transparent_bg_image() -> np.ndarray:
    """200x200 RGBA with transparent background and a red filled circle."""
    img = np.zeros((200, 200, 4), dtype=np.uint8)
    y, x = np.ogrid[:200, :200]
    mask = (x - 100) ** 2 + (y - 100) ** 2 <= 40**2
    img[mask] = [255, 0, 0, 255]
    return img


@pytest.fixture
def complex_bg_image() -> np.ndarray:
    """200x200 image with noisy mid-tone background and a black rectangle.

    The background has enough local variation for adaptive thresholding
    to produce non-trivial results.
    """
    rng = np.random.RandomState(42)
    img = rng.randint(140, 200, size=(200, 200, 3), dtype=np.uint8)
    # Black object — strong contrast against noisy bg
    img[40:160, 50:150] = [0, 0, 0]
    return img


@pytest.fixture
def tiny_object_image() -> np.ndarray:
    """200x200 white image with a 5x5 black dot — triggers MASK_TOO_SMALL."""
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[98:103, 98:103] = [0, 0, 0]
    return img


@pytest.fixture
def empty_image() -> np.ndarray:
    """200x200 pure white image — no object at all."""
    return np.full((200, 200, 3), 255, dtype=np.uint8)


@pytest.fixture
def multi_object_image() -> np.ndarray:
    """200x200 white image with two separated dark squares."""
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[20:60, 20:60] = [30, 30, 30]
    img[140:180, 140:180] = [30, 30, 30]
    return img


@pytest.fixture
def thin_object_image() -> np.ndarray:
    """200x200 white image with a thin vertical bar (club shaft-like)."""
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    img[10:190, 98:102] = [30, 30, 30]
    return img


# ---------------------------------------------------------------------------
# Configurations
# ---------------------------------------------------------------------------


@pytest.fixture
def default_config() -> PipelineConfig:
    """Default pipeline config with sensible test values."""
    return PipelineConfig(
        global_config=GlobalConfig(canvas_size=200),
        fallback=FallbackConfig(enabled=True),
    )


@pytest.fixture
def no_fallback_config() -> PipelineConfig:
    """Pipeline config with fallback disabled."""
    return PipelineConfig(
        global_config=GlobalConfig(canvas_size=200),
        fallback=FallbackConfig(enabled=False),
    )


# ---------------------------------------------------------------------------
# Mapping data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_mapping_rows() -> list[MappingRow]:
    return [
        MappingRow(
            supplier_sku="IMG001",
            store_article="100001",
            suffix="front",
            category="CLUB_LONG",
        ),
        MappingRow(
            supplier_sku="IMG002",
            store_article="100002",
            suffix="front",
            category="BALL",
        ),
        MappingRow(
            supplier_sku="IMG003",
            store_article="100003",
            suffix="side",
            category="SHOE",
        ),
    ]


@pytest.fixture
def sample_mapping_csv(tmp_path: Path, sample_mapping_rows) -> Path:
    """Write a sample mapping CSV to tmp_path and return its path."""
    csv_path = tmp_path / "mapping.csv"
    lines = ["supplier_sku,store_article,suffix,category"]
    for r in sample_mapping_rows:
        lines.append(f"{r.supplier_sku},{r.store_article},{r.suffix},{r.category}")
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path


@pytest.fixture
def sample_rules_yaml(tmp_path: Path) -> Path:
    """Write a minimal rules YAML to tmp_path and return its path."""
    yaml_path = tmp_path / "rules.yaml"
    yaml_path.write_text(
        """\
global:
  canvas_size: 200
  jpeg_quality: 85
  white_distance_threshold: 30.0

fallback:
  enabled: true
  strategy: grabcut

categories:
  CLUB_LONG:
    margin_pct: 0.08
    thin_object_protection: true
  BALL:
    margin_pct: 0.12
""",
        encoding="utf-8",
    )
    return yaml_path
