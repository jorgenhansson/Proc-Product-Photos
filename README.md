# Proc-Product-Photos

Batch processing pipeline for supplier product images in golf e-commerce.

Reads supplier images (TIFF/PNG/JPEG), detects and crops the product object,
applies category-aware margins, places the result on a 1000x1000 white JPEG
canvas, and renames files using a SKU-to-article mapping.

## Features

- **Deterministic primary pipeline** — classical image processing (thresholding,
  morphology, connected components), no AI dependency for the main path.
- **Category-aware cropping** — 9 golf product categories with tunable margins,
  fill ratios, thin-object protection, and centering bias.
- **Pluggable fallback** — GrabCut-based heuristic fallback for flagged images,
  with a clean extension point for real AI/segmentation models.
- **Configuration-driven** — all thresholds, category rules, and behavior
  externalized in YAML.
- **Comprehensive statistics** — JSON stats, console summary, optional HTML report.
- **Review workflow** — flagged images copied to review directory with manifest,
  side-by-side preview images, and reason codes.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Copy and adjust configuration
cp rules.example.yaml rules.yaml

# Run
process-images \
  --input ./input \
  --output ./output \
  --review ./review \
  --mapping ./mapping.csv \
  --rules ./rules.yaml \
  --stats ./stats.json \
  --html-report ./report.html
```

## CLI Options

| Flag | Description |
|------|-------------|
| `--input` | Input directory with supplier images (required) |
| `--output` | Output directory for processed images (required) |
| `--review` | Review directory for flagged images (required) |
| `--mapping` | CSV or XLSX mapping file (required) |
| `--rules` | YAML configuration file (required) |
| `--stats` | Output JSON statistics file (optional) |
| `--html-report` | Output HTML report (optional) |
| `--verbose / -v` | Enable debug logging |
| `--dry-run` | Validate inputs without processing |
| `--limit / -n` | Process only first N images |

## Mapping File

CSV or XLSX with columns:

| Column | Required | Description |
|--------|----------|-------------|
| `supplier_sku` | Yes | Input filename stem (without extension) |
| `store_article` | Yes | Store article number |
| `suffix` | Yes | Image variant (front, side, alt1, ...) |
| `category` | Yes | Product category (CLUB_LONG, BALL, etc.) |
| `variant` | No | Product variant |
| `color` | No | Color description |
| `angle` | No | Photo angle |
| `notes` | No | Free-form notes |

Output filename: `{store_article}_{suffix}.jpg`

## Categories

| Category | Behavior |
|----------|----------|
| `CLUB_LONG` | Conservative crop, thin-object protection for shafts |
| `CLUB_HEAD_ONLY` | Tighter crop, preserve contour |
| `BALL` | Symmetric crop, high centering consistency |
| `SHOE` | Preserve heel/toe, mild vertical offset |
| `BAG` | Preserve top/base, avoid making product too small |
| `APPAREL_FOLDED` | Tight crop, moderate margin |
| `APPAREL_WORN_OR_SHAPED` | Softer boundary tolerance |
| `ACCESSORY_SMALL` | Prevent tiny objects on canvas |
| `BOX_OR_PACKAGING` | Preserve packaging edges |

## Architecture

```
Input images
    │
    ▼
┌──────────────────────────┐
│  Classical Crop Strategy │  ← Deterministic primary pipeline
│  (threshold → mask →     │
│   morphology → bbox →    │
│   category margins →     │
│   crop → resize → canvas)│
└──────────┬───────────────┘
           │
     ┌─────┴─────┐
     │  Flagged?  │
     └─────┬─────┘
       NO  │  YES
       │   ▼
       │  ┌──────────────────┐
       │  │  AI Fallback     │  ← GrabCut or external provider
       │  │  (validate after)│
       │  └────────┬─────────┘
       │      OK   │  Still bad
       │      │    ▼
       │      │  review/
       ▼      ▼
     output/
```

## Extension: Real AI Integration

To plug in a real segmentation model:

```python
from process_images.crop.base import CropStrategy
from process_images.crop.ai_fallback import AIFallbackCropStrategy

class MySegmentationStrategy(CropStrategy):
    def crop(self, image, context, config):
        # Call your model/API here
        ...

# Use as external provider
fallback = AIFallbackCropStrategy(
    external_provider=MySegmentationStrategy()
)
```

## Testing

```bash
pytest tests/ -v
pytest tests/ -v --cov=process_images --cov-report=term-missing
```

All tests use synthetic images — no real product images needed.

## Project Structure

```
Proc-Product-Photos/
├── pyproject.toml
├── rules.example.yaml
├── README.md
├── process_images/
│   ├── __init__.py
│   ├── cli.py              # Typer CLI entry point
│   ├── config.py            # YAML config loading
│   ├── models.py            # Data models, enums, flags
│   ├── pipeline.py          # Main orchestrator
│   ├── io_utils.py          # Image I/O, discovery
│   ├── mapping.py           # SKU mapping loader/validator
│   ├── statistics.py        # Stats accumulation, JSON/console
│   ├── reporting.py         # HTML report, review manifest, previews
│   ├── validators.py        # Post-crop validation checks
│   └── crop/
│       ├── __init__.py
│       ├── base.py          # CropStrategy ABC
│       ├── classical.py     # Primary deterministic pipeline
│       ├── ai_fallback.py   # GrabCut fallback + extension point
│       ├── masks.py         # Mask generation (alpha, white, complex)
│       ├── morphology.py    # Morphological ops, connected components
│       ├── categories.py    # Category taxonomy, config resolution
│       └── canvas.py        # Resize, center, place on canvas
└── tests/
    ├── conftest.py          # Synthetic image fixtures
    ├── test_mapping.py
    ├── test_categories.py
    ├── test_filename.py
    ├── test_masks.py
    ├── test_classical_crop.py
    ├── test_flagging.py
    ├── test_fallback.py
    ├── test_canvas.py
    ├── test_statistics.py
    └── test_pipeline.py
```
