#!/usr/bin/env bash
# =============================================================================
# run_dimbo.sh — Process dimbo supplier images
#
# Crops, resizes and places supplier product images on a square white canvas
# using category-aware rules.  Flagged images are retried with a heuristic
# fallback.  Output filenames preserve the original name (sanitized) with
# "-cropped" appended.
#
# Prerequisites:
#   1. Python venv:  python3 -m venv .venv && pip install -e .
#   2. dimbo/Bilder/ directory with supplier images
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SOURCE_DIR="dimbo/Bilder"
INPUT_DIR="dimbo_run/input"
OUTPUT_DIR="dimbo_run/output"
REVIEW_DIR="dimbo_run/review"
MAPPING="dimbo_run/mapping.csv"
RULES="rules.example.yaml"
STATS="dimbo_run/stats.json"
HTML_REPORT="dimbo_run/report.html"
CANVAS_SIZE=""
FORMAT=""
OVERWRITE=false
REGEN=false
CLEAN=true
PASSTHROUGH_ARGS=()

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
show_help() {
    cat << 'HELPEOF'
Usage: ./run_dimbo.sh [OPTIONS]

Process dimbo supplier images: detect product, crop, resize, and generate
statistics.  Output filenames are sanitized originals with "-cropped" suffix.

Directories:
  --source DIR        Source image directory (default: dimbo/Bilder)
  -i, --input DIR     Pipeline input dir with symlinks (default: dimbo_run/input)
  -o, --output DIR    Output directory for processed images (default: dimbo_run/output)
  --review DIR        Review dir for flagged/failed (default: dimbo_run/review)
  --mapping FILE      Mapping CSV (default: dimbo_run/mapping.csv, auto-generated)
  --rules FILE        YAML rules file (default: rules.example.yaml)

Image options:
  -s, --canvas-size N Canvas size in pixels (default: 1000)
  -f, --format FMT    Output format: jpg, png, webp, tiff (default: jpg)
  --overwrite         Overwrite existing output files

Processing:
  -p, --parallel      Enable parallel processing (auto-detects CPU cores)
  -w, --workers N     Set number of parallel workers (implies --parallel)
  -n, --limit N       Process only the first N images (for testing)
  -v, --verbose       Enable debug logging
  --dry-run           Validate inputs and mapping, then exit
  --regen             Force regeneration of mapping and input symlinks
  --no-clean          Keep previous output (default: clean before each run)

Other:
  -h, --help          Show this help message and exit

Examples:
  ./run_dimbo.sh                              Full batch, 1000x1000 JPG
  ./run_dimbo.sh --parallel                   Parallel (auto-detect cores)
  ./run_dimbo.sh -w 4                         Parallel with 4 workers
  ./run_dimbo.sh -s 800 -f png                800x800 PNG output
  ./run_dimbo.sh --limit 10 -v                Quick test, verbose
  ./run_dimbo.sh --source /path/to/images     Custom source directory
  ./run_dimbo.sh -o /tmp/cropped --overwrite  Custom output, overwrite existing
  ./run_dimbo.sh --regen                      Rebuild mapping from source

Output:
  dimbo_run/output/     Processed images ({original}-cropped.{ext})
  dimbo_run/review/     Flagged/failed images + manifest.json
  dimbo_run/stats.json  Pipeline statistics (JSON)
  dimbo_run/report.html Visual report with category breakdown
HELPEOF
}

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        --source)
            SOURCE_DIR="$2"; shift 2 ;;
        -i|--input)
            INPUT_DIR="$2"; shift 2 ;;
        -o|--output)
            OUTPUT_DIR="$2"; shift 2 ;;
        --review)
            REVIEW_DIR="$2"; shift 2 ;;
        --mapping)
            MAPPING="$2"; shift 2 ;;
        --rules)
            RULES="$2"; shift 2 ;;
        -s|--canvas-size)
            CANVAS_SIZE="$2"; shift 2 ;;
        -f|--format)
            FORMAT="$2"; shift 2 ;;
        --overwrite)
            OVERWRITE=true; shift ;;
        --regen)
            REGEN=true; shift ;;
        --no-clean)
            CLEAN=false; shift ;;
        -p|--parallel)
            PASSTHROUGH_ARGS+=("--parallel"); shift ;;
        -w|--workers)
            PASSTHROUGH_ARGS+=("--workers" "$2"); shift 2 ;;
        -n|--limit|-v|--verbose|--dry-run)
            # These pass through directly to the Python CLI
            PASSTHROUGH_ARGS+=("$1"); shift ;;
        *)
            # Pass unknown args through (handles -n 10 etc.)
            PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

# ---------------------------------------------------------------------------
# Activate venv
# ---------------------------------------------------------------------------
if [[ -d ".venv" ]]; then
    source .venv/bin/activate
else
    echo "ERROR: .venv not found."
    echo "Run:  python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
    exit 1
fi

# ---------------------------------------------------------------------------
# Generate mapping + symlink input
# ---------------------------------------------------------------------------
if [[ "$REGEN" == true ]] || [[ ! -f "$MAPPING" ]] || [[ ! -d "$INPUT_DIR" ]]; then
    echo "Generating mapping and input symlinks from $SOURCE_DIR ..."
    python3 - "$SOURCE_DIR" "$MAPPING" "$INPUT_DIR" << 'PYEOF'
import csv, re, sys, unicodedata
from pathlib import Path


def sanitize_filename(name: str) -> str:
    """Make a filename safe for all filesystems."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-zA-Z0-9\-.]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-.")


source_dir = Path(sys.argv[1])
mapping_path = Path(sys.argv[2])
input_dir = Path(sys.argv[3])

categories = {
    "Bollar": "BALL",
    "Putters": "CLUB_LONG",
    "Golf Bags": "BAG",
    "Kläder": "APPAREL_FOLDED",
    "Footwear - OBS Embargo": "SHOE",
}

all_images = []
for folder, cat in categories.items():
    src_dir = source_dir / folder
    if not src_dir.exists():
        continue
    for img in sorted(src_dir.rglob("*")):
        if img.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
            all_images.append((img, cat))

# Deduplicate by stem: keep PNG > TIF > JPG
priority = {".png": 0, ".tif": 1, ".tiff": 1, ".jpg": 2, ".jpeg": 2}
by_stem = {}
for img, cat in all_images:
    ext_pri = priority.get(img.suffix.lower(), 9)
    if img.stem not in by_stem or ext_pri < by_stem[img.stem][1]:
        by_stem[img.stem] = (img, ext_pri, cat)

unique = [(p, c) for _, (p, _, c) in sorted(by_stem.items())]

rows = []
seen_safe = {}
for img, cat in unique:
    safe = sanitize_filename(img.stem)
    if not safe:
        safe = f"image-{len(rows):04d}"
    if safe in seen_safe and seen_safe[safe] != img.stem:
        safe = f"{safe}-{len(rows):04d}"
    seen_safe[safe] = img.stem
    rows.append({
        "supplier_sku": img.stem,
        "store_article": safe,
        "suffix": "cropped",
        "category": cat,
    })

mapping_path.parent.mkdir(parents=True, exist_ok=True)
with open(mapping_path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["supplier_sku", "store_article", "suffix", "category"])
    w.writeheader()
    w.writerows(rows)

input_dir.mkdir(parents=True, exist_ok=True)
for f in input_dir.iterdir():
    if f.is_symlink() or f.is_file():
        f.unlink()
for img, _ in unique:
    dst = input_dir / img.name
    if not dst.exists():
        dst.symlink_to(img.resolve())

sanitized_count = sum(1 for r in rows if r["store_article"] != r["supplier_sku"])
print(f"  {len(unique)} unique images mapped and symlinked")
if sanitized_count:
    print(f"  {sanitized_count} filenames sanitized (spaces/unsafe chars removed)")
PYEOF
fi

# ---------------------------------------------------------------------------
# Clean previous output
# ---------------------------------------------------------------------------
if [[ "$CLEAN" == true ]]; then
    rm -rf "$OUTPUT_DIR" "$REVIEW_DIR"
fi
mkdir -p "$OUTPUT_DIR" "$REVIEW_DIR"

# ---------------------------------------------------------------------------
# Build CLI arguments
# ---------------------------------------------------------------------------
CLI_ARGS=(
    --input "$INPUT_DIR"
    --output "$OUTPUT_DIR"
    --review "$REVIEW_DIR"
    --mapping "$MAPPING"
    --rules "$RULES"
    --stats "$STATS"
    --html-report "$HTML_REPORT"
)

[[ -n "$CANVAS_SIZE" ]] && CLI_ARGS+=(--canvas-size "$CANVAS_SIZE")
[[ -n "$FORMAT" ]]      && CLI_ARGS+=(--format "$FORMAT")
[[ "$OVERWRITE" == true ]] && CLI_ARGS+=(--overwrite)

# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Processing dimbo images"
echo "  Source:  $SOURCE_DIR"
echo "  Input:   $INPUT_DIR"
echo "  Output:  $OUTPUT_DIR"
echo "  Canvas:  ${CANVAS_SIZE:-1000 (default)}"
echo "  Format:  ${FORMAT:-jpg (default)}"
echo "  Rules:   $RULES"
echo "============================================================"
echo ""

python -m process_images.cli \
    "${CLI_ARGS[@]}" \
    ${PASSTHROUGH_ARGS[@]+"${PASSTHROUGH_ARGS[@]}"}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
EXT="${FORMAT:-jpg}"
OUTPUT_COUNT=$(find "$OUTPUT_DIR" -maxdepth 1 -name "*.$EXT" 2>/dev/null | wc -l | tr -d ' ')
REVIEW_COUNT=$(find "$REVIEW_DIR" -maxdepth 1 \( -name "*.png" -o -name "*.jpg" \) 2>/dev/null | wc -l | tr -d ' ')

echo ""
echo "============================================================"
echo "  Done!"
echo "  Output images:  $OUTPUT_COUNT files"
echo "  Review items:   $REVIEW_COUNT files"
echo "  Stats:          $STATS"
echo "  HTML report:    $HTML_REPORT"
echo "============================================================"
