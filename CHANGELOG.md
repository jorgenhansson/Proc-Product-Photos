# Changelog

All notable changes to Proc-Product-Photos are documented here.

## [Unreleased]

- Pending: naming convention based on Dimbo image angle codes
- Pending: 14 additional product categories (IRON_SET, GLOVE, CAP, etc.)
- Pending: testing on 8000 production images

## [0.6.0] - 2026-03-28

### Added
- Parallel processing with `--parallel` and `--workers` flags
- `ProcessPoolExecutor` with auto-detected core count
- Workers encode images to bytes in-process (no numpy cross-process)
- `encode_image()` and `encode_side_by_side()` for in-process encoding

### Performance
- 3.1x speedup on 12-core M2 Pro (508 images: 21m43s -> 6m58s)

## [0.5.0] - 2026-03-28

### Added
- Asymmetric per-side margins (`margin_top/bottom/left/right`)
- `margin_mode` config: "image" (default) or "object" relative
- `resolve_margins()` on CategoryConfig
- Configurable output format (`--format jpg/png/webp/tiff`)
- Configurable canvas size (`--canvas-size N`)
- Overwrite protection (`--overwrite` flag)
- `run_dimbo.sh` — batch wrapper for dimbo supplier images
- `rename_to_store.sh` — rename cropped files to store article numbers
- Filename sanitization (spaces to hyphens, unsafe chars removed)
- `{source_stem}` placeholder in filename pattern

### Changed
- Default filename pattern: `{source_stem}-cropped.{ext}`
- Category defaults rewritten with asymmetric margins from real image analysis
- `finalize.py` uses `target_fill_ratio_max` for resize (not average)

## [0.4.0] - 2026-03-28

### Fixed
- CLUB_LONG: removed redundant consistency check causing 87.5% false positives (#31)
- BALL: added edge-enhanced masking (`mask_from_white_bg_edge_enhanced`) for
  white-on-white detection (#32)
- ACCESSORY_SMALL: per-category `min_object_ratio` and `min_bbox_ratio` (#33)
- Fallback: flag-aware dispatch instead of always GrabCut (#34)
- CLUB_LONG: lowered `expected_aspect_ratio_min` to 1.0 for diagonal putters

### Added
- `mask_from_white_bg_edge_enhanced()` — Canny + contour fill + LAB union
- Flag-aware fallback dispatch: re-validate / edge-remask / GrabCut by failure mode
- `primary_flags` and `primary_result` on ImageContext for fallback context
- GrabCut downscale for large images (cap at 1200px, scale back after)

### Changed
- `max_bbox_ratio` default raised to 1.0 (allow full-frame products)
- BALL `threshold_bias` lowered to -6.0
- BALL `min_component_size` lowered to 200

### Tested
- 508 real supplier images: 99.5% success rate (506 OK, 2 corrupt files)
- Smoke tested on TaylorMade, FootJoy, Under Armour product images

## [0.3.0] - 2026-03-28

### Added
- Zero-margin mode: product edges extend to canvas edges
- EXIF orientation handling
- Same-batch naming collision prevention
- GrabCut seeded with GC_FGD for inner bbox region
- Expected aspect ratio validation per category
- Multi-page TIFF warning (only first page processed)
- Tests for CLI, multi-row mapping, and side-by-side preview

### Fixed
- HTML report wrong category totals
- Double morphology for COMPLEX_BG images
- Empty crop region guard

### Changed
- Extracted shared `finalize_crop()` from both strategies

## [0.2.0] - 2026-03-27

### Added
- LAB-space distance for mask generation and background detection
- Relaxed validation tolerance for fallback results
- Percentile stats (p10, p50, p90, p95)
- Per-category success rates and timing breakdown
- Source metadata, dual metrics, and labeled previews in review manifest
- Dataclass introspection for config parsing (no manual per-field parsers)

### Fixed
- Alpha channel preserved through crop pipeline
- Thin-object handling: skip morph-open, collinear component merge
- Minimum crop width for thin objects

### Changed
- Split `CropResult.bbox` into `object_bbox` and `crop_bbox`
- Removed dead config fields: `shadow_tolerance`, `fallback_sensitivity`

## [0.1.0] - 2026-03-27

### Added
- Initial implementation
- Classical crop strategy: threshold, morphology, connected components
- GrabCut-based fallback strategy
- 9 product categories with configurable crop rules
- YAML configuration with per-category overrides
- CSV/XLSX mapping with validation
- JSON statistics, console summary, HTML report
- Review manifest with side-by-side previews
- 13 flag reason codes
- Typer CLI
- Test suite with synthetic images
