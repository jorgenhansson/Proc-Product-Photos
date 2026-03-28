# Proc-Product-Photos

Batch processing pipeline for supplier product images in golf e-commerce.

Reads supplier images (TIFF/PNG/JPEG), detects and crops the product object,
applies category-aware asymmetric margins, places the result on a configurable
square canvas, and outputs renamed files ready for upload.

Tested on 508 real supplier images (TaylorMade, FootJoy, Under Armour) with
99.5% success rate.

## Features

- **Deterministic primary pipeline** — classical image processing (LAB-space
  thresholding, morphology, connected components). No AI dependency for the
  main path.
- **Category-aware asymmetric cropping** — 9 golf product categories with
  per-side margins, fill ratios, thin-object protection, and centering bias.
- **Edge-enhanced masking** — Canny + contour fill for near-white objects on
  white backgrounds (golf balls).
- **Flag-aware fallback** — dispatches by failure mode: relaxed re-validation
  for strict-threshold flags, edge-enhanced remask for mask failures, GrabCut
  refinement for everything else.
- **Parallel processing** — `ProcessPoolExecutor` with auto-detected core count.
  3.1x speedup on 12-core M2 Pro.
- **Configuration-driven** — all thresholds, category rules, margins, and
  behavior externalized in YAML.
- **Comprehensive statistics** — JSON stats, console summary, optional HTML report.
- **Review workflow** — flagged images copied to review directory with manifest,
  side-by-side preview images, and reason codes.
- **Configurable output** — canvas size, image format (JPG/PNG/WebP/TIFF),
  filename pattern, overwrite protection.

## Quick Start

```bash
# Install
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run on your images
python -m process_images.cli \
  --input ./input \
  --output ./output \
  --review ./review \
  --mapping ./mapping.csv \
  --rules ./rules.example.yaml \
  --stats ./stats.json \
  --html-report ./report.html
```

## Dimbo Workflow

Two convenience scripts for processing dimbo supplier images:

```bash
# Process all images in dimbo/Bilder/
./run_dimbo.sh

# Parallel mode (auto-detect CPU cores)
./run_dimbo.sh --parallel

# With options
./run_dimbo.sh --parallel --canvas-size 800 --format png --limit 50

# After processing: rename to store article numbers using Excel master files
./rename_to_store.sh
```

### Output structure

```
dimbo_run/
  input/          Symlinks to original images (deduplicated)
  output/         Cropped images: {original}-cropped.jpg
  store_named/    Same images renamed to store article numbers
  review/         Flagged/failed images + manifest.json
  stats.json      Pipeline statistics
  report.html     Visual report
```

## CLI Reference

| Flag | Short | Description |
|------|-------|-------------|
| `--input` | `-i` | Input directory with supplier images (required) |
| `--output` | `-o` | Output directory for processed images (required) |
| `--review` | | Review directory for flagged images (required) |
| `--mapping` | `-m` | CSV or XLSX mapping file (required) |
| `--rules` | `-r` | YAML configuration file (required) |
| `--canvas-size` | `-s` | Canvas size in pixels (default: 1000) |
| `--format` | `-f` | Output format: jpg, png, webp, tiff (default: jpg) |
| `--overwrite` | | Overwrite existing output files |
| `--parallel` | `-p` | Enable parallel processing (auto-detect cores) |
| `--workers` | `-w` | Number of parallel workers (implies --parallel) |
| `--stats` | | Output JSON statistics file |
| `--html-report` | | Output HTML report |
| `--verbose` | `-v` | Enable debug logging |
| `--dry-run` | | Validate inputs without processing |
| `--limit` | `-n` | Process only first N images |

## Mapping File

CSV or XLSX with columns:

| Column | Required | Description |
|--------|----------|-------------|
| `supplier_sku` | Yes | Input filename (with extension) or stem |
| `store_article` | Yes | Store article number |
| `suffix` | Yes | Image variant (front, side, cropped, ...) |
| `category` | Yes | Product category (CLUB_LONG, BALL, etc.) |
| `variant` | No | Product variant |
| `color` | No | Color description |
| `angle` | No | Photo angle |
| `notes` | No | Free-form notes |

Default output filename pattern: `{source_stem}-cropped.{ext}`

Configurable via `filename_pattern` in rules YAML.

## Categories

### Current (9 categories, tested on real images)

| Category | Margins | Key behavior |
|----------|---------|--------------|
| `CLUB_LONG` | Asymmetric: shaft 0%, head 5% | Thin-object protection, collinear merge |
| `CLUB_HEAD_ONLY` | Uniform 6% | Compact object, tight crop |
| `BALL` | Uniform 8% | Edge-enhanced mask for white-on-white |
| `SHOE` | Horizontal 2%, vertical by shape | Preserve heel/toe |
| `BAG` | Vertical 2% | Preserve top/base |
| `APPAREL_FOLDED` | Vertical 0%, horizontal 1% | Tight crop |
| `APPAREL_WORN_OR_SHAPED` | Uniform 2% | Softer boundary tolerance |
| `ACCESSORY_SMALL` | Uniform 5% | Per-category min_object_ratio |
| `BOX_OR_PACKAGING` | Uniform 2% | Preserve packaging edges |

### Planned (pending real image testing)

IRON_SET, CART_PUSH, CART_ELECTRIC, GLOVE, CAP, HEADCOVER, UMBRELLA,
ELECTRONICS, WATCH, TRAVEL_COVER, SOFT_CASE, RAIN_GEAR, EYEWEAR, CLUB_SET

## Architecture

```
Input images
    |
    v
+----------------------------+
|  Classical Crop Strategy   |  <- Deterministic primary pipeline
|  detect bg type            |     (alpha / white-bg / complex)
|  -> generate mask          |     (LAB distance / edge-enhanced / adaptive)
|  -> morphology + cleanup   |
|  -> find main component    |
|  -> category-aware margins |     (asymmetric, per-side)
|  -> crop -> resize         |
|  -> place on canvas        |
+-------------+--------------+
              |
        +-----+------+
        |  Validate  |
        +-----+------+
         OK   |  Flagged
         |    v
         |  +------------------------+
         |  | Flag-aware Fallback    |
         |  | - validation-only:     |  <- re-use primary result
         |  |   relaxed re-validate  |
         |  | - mask failure:        |  <- edge-enhanced remask
         |  |   threshold sweep      |
         |  | - other:               |  <- GrabCut with prior mask
         |  |   refinement           |
         |  +-----------+------------+
         |        OK    |  Still bad
         |        |     v
         |        |   review/
         v        v
       output/
```

## Parallel Processing

```bash
# Auto-detect cores (leaves 1 free for OS)
python -m process_images.cli --parallel ...

# Explicit worker count
python -m process_images.cli --workers 8 ...
```

Workers run in separate processes via `ProcessPoolExecutor`. Each worker
loads, crops, validates, runs fallback, and encodes to bytes. The main
thread writes files and accumulates statistics.

**Benchmark (508 real images, Apple M2 Pro 12 cores):**

| Mode | Wall time | Speedup |
|------|-----------|---------|
| Sequential | 21m 43s | 1.0x |
| Parallel (11 workers) | 6m 58s | 3.1x |

## Extension: Real AI Integration

The fallback strategy is pluggable. To use a real segmentation model:

```python
from process_images.crop.base import CropStrategy
from process_images.crop.ai_fallback import AIFallbackCropStrategy

class MySegmentationStrategy(CropStrategy):
    def crop(self, image, context, config):
        # Call your model/API here
        ...

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
+-- pyproject.toml
+-- rules.example.yaml          Category rules and thresholds
+-- run_dimbo.sh                Batch wrapper for dimbo images
+-- rename_to_store.sh          Rename cropped files to store article numbers
+-- README.md
+-- CHANGELOG.md
+-- process_images/
|   +-- __init__.py
|   +-- cli.py                  Typer CLI entry point
|   +-- config.py               YAML config, dataclasses, per-side margins
|   +-- models.py               Data models, enums, flags
|   +-- pipeline.py             Orchestrator (sequential + parallel)
|   +-- io_utils.py             Image I/O, discovery, encode
|   +-- mapping.py              SKU mapping loader/validator
|   +-- statistics.py           Stats accumulation, JSON/console
|   +-- reporting.py            HTML report, review manifest, previews
|   +-- validators.py           Post-crop validation checks
|   +-- crop/
|       +-- __init__.py
|       +-- base.py             CropStrategy ABC
|       +-- classical.py        Primary deterministic pipeline
|       +-- ai_fallback.py      Flag-aware fallback + extension point
|       +-- masks.py            Mask generation (alpha, white, edge-enhanced, complex)
|       +-- morphology.py       Morphological ops, connected components, collinear merge
|       +-- categories.py       Category taxonomy, config resolution with YAML merge
|       +-- canvas.py           Resize, center, place on canvas
|       +-- finalize.py         Shared bbox expansion + crop-to-canvas
+-- tests/
    +-- conftest.py             Synthetic image fixtures
    +-- test_mapping.py
    +-- test_categories.py
    +-- test_filename.py
    +-- test_masks.py
    +-- test_classical_crop.py
    +-- test_flagging.py
    +-- test_fallback.py
    +-- test_canvas.py
    +-- test_statistics.py
    +-- test_pipeline.py
```
