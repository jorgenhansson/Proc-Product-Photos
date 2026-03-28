#!/usr/bin/env bash
# =============================================================================
# rename_to_store.sh — Copy cropped images with store article numbers
#
# Reads the dimbo master Excel files (TaylorMade, Under Armour) to build a
# supplier SKU → store article number mapping.  Then copies each cropped
# image to a new filename with the supplier SKU replaced by the store
# article number.
#
# Images whose filenames don't contain a known supplier SKU are copied
# unchanged (or skipped, depending on --skip-unmatched).
#
# Prerequisites:
#   1. Python venv with pandas + openpyxl:  pip install -e .
#   2. Cropped images in source directory (from run_dimbo.sh)
#   3. Master Excel files in dimbo/
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
SOURCE_DIR="dimbo_run/output"
DEST_DIR="dimbo_run/store_named"
MASTER_DIR="dimbo"
DRY_RUN=false
SKIP_UNMATCHED=false
OVERWRITE=false

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------
show_help() {
    cat << 'HELPEOF'
Usage: ./rename_to_store.sh [OPTIONS]

Copy cropped product images, replacing the supplier SKU in the filename
with the store's own article number (from master Excel files).

Options:
  -s, --source DIR        Source directory with cropped images
                          (default: dimbo_run/output)
  -d, --dest DIR          Destination directory for renamed copies
                          (default: dimbo_run/store_named)
  --master DIR            Directory containing master Excel files
                          (default: dimbo)
  --skip-unmatched        Don't copy files that can't be matched to a SKU
  --overwrite             Overwrite existing files in destination
  --dry-run               Show what would be done without copying
  -h, --help              Show this help message and exit

How it works:
  1. Reads all *.xlsx files in the master directory
  2. Extracts column H (store article) and I (supplier SKU) from each
  3. For each cropped image, finds the longest supplier SKU that appears
     as a substring in the filename
  4. Replaces that substring with the store article number
  5. Copies to destination directory

Examples:
  ./rename_to_store.sh                        Default: copy to dimbo_run/store_named/
  ./rename_to_store.sh --dry-run              Preview without copying
  ./rename_to_store.sh -d /tmp/final          Custom destination
  ./rename_to_store.sh --skip-unmatched       Only copy matched files

Filename transformation:
  1389846-004_BC-cropped.jpg
    SKU match: 1389846-004 → store: 7850035-0049
    Output:    7850035-0049_BC-cropped.jpg
HELPEOF
}

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)       show_help; exit 0 ;;
        -s|--source)     SOURCE_DIR="$2"; shift 2 ;;
        -d|--dest)       DEST_DIR="$2"; shift 2 ;;
        --master)        MASTER_DIR="$2"; shift 2 ;;
        --skip-unmatched) SKIP_UNMATCHED=true; shift ;;
        --overwrite)     OVERWRITE=true; shift ;;
        --dry-run)       DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1"; show_help; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Activate venv
# ---------------------------------------------------------------------------
if [[ -d ".venv" ]]; then
    source .venv/bin/activate
else
    echo "ERROR: .venv not found."
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "ERROR: Source directory does not exist: $SOURCE_DIR"
    echo "Run ./run_dimbo.sh first to generate cropped images."
    exit 1
fi

# ---------------------------------------------------------------------------
# Run rename script
# ---------------------------------------------------------------------------
python3 - "$SOURCE_DIR" "$DEST_DIR" "$MASTER_DIR" "$DRY_RUN" "$SKIP_UNMATCHED" "$OVERWRITE" << 'PYEOF'
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

import pandas as pd

source_dir = Path(sys.argv[1])
dest_dir = Path(sys.argv[2])
master_dir = Path(sys.argv[3])
dry_run = sys.argv[4] == "true"
skip_unmatched = sys.argv[5] == "true"
overwrite = sys.argv[6] == "true"


def sanitize(name: str) -> str:
    """Sanitize a string for use in filenames."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-zA-Z0-9\-.]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-.")


# -----------------------------------------------------------------------
# 1. Load all master Excel files
# -----------------------------------------------------------------------
all_mappings = []
xlsx_files = sorted(master_dir.glob("*.xlsx"))
xlsx_files = [f for f in xlsx_files if not f.name.startswith("~$")]

if not xlsx_files:
    print(f"ERROR: No .xlsx files found in {master_dir}")
    sys.exit(1)

for xlsx_path in xlsx_files:
    try:
        df = pd.read_excel(xlsx_path, skiprows=1, usecols=[7, 8], dtype=str)
        df.columns = ["store_article", "supplier_sku"]
        df = df.dropna(subset=["store_article", "supplier_sku"])
        df = df[df["store_article"] != "Artnr"]
        df["store_article"] = df["store_article"].str.strip()
        df["supplier_sku"] = df["supplier_sku"].str.strip()
        all_mappings.append(df)
        print(f"  Loaded {len(df)} entries from {xlsx_path.name}")
    except Exception as e:
        print(f"  WARNING: Failed to read {xlsx_path.name}: {e}")

if not all_mappings:
    print("ERROR: No mapping data loaded")
    sys.exit(1)

mapping = pd.concat(all_mappings).drop_duplicates(subset=["supplier_sku"])
print(f"  Total unique SKU mappings: {len(mapping)}")

# Build lookup: supplier_sku → store_article
# Sort by SKU length descending so longest match wins
sku_to_store = {}
for _, row in mapping.iterrows():
    sku_to_store[row["supplier_sku"]] = row["store_article"]

# Sort by length descending for longest-match-first
sorted_skus = sorted(sku_to_store.keys(), key=len, reverse=True)

# -----------------------------------------------------------------------
# 2. Process cropped images
# -----------------------------------------------------------------------
if not dry_run:
    dest_dir.mkdir(parents=True, exist_ok=True)

source_files = sorted(
    f for f in source_dir.iterdir()
    if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif")
)
print(f"\n  Source images: {len(source_files)}")

matched = 0
unmatched = 0
skipped = 0
copied = 0
overwritten = 0
errors = []

for src_file in source_files:
    stem = src_file.stem      # e.g. "1389846-004_BC-cropped"
    ext = src_file.suffix      # e.g. ".jpg"

    # Find longest supplier SKU that appears in the filename
    found_sku = None
    for sku in sorted_skus:
        if sku in stem:
            found_sku = sku
            break

    if found_sku:
        store_art = sku_to_store[found_sku]
        # Replace the SKU substring with store article number
        new_stem = stem.replace(found_sku, sanitize(store_art), 1)
        new_name = f"{new_stem}{ext}"
        matched += 1
    else:
        if skip_unmatched:
            skipped += 1
            continue
        # Copy unchanged
        new_name = src_file.name
        unmatched += 1

    dest_file = dest_dir / new_name

    if dest_file.exists() and not overwrite:
        errors.append(f"EXISTS: {new_name} (use --overwrite)")
        continue

    if dry_run:
        action = "RENAME" if found_sku else "COPY  "
        sku_info = f"  SKU={found_sku} → {sku_to_store[found_sku]}" if found_sku else ""
        print(f"  {action}: {src_file.name}  →  {new_name}{sku_info}")
    else:
        shutil.copy2(src_file, dest_file)
        copied += 1
        if dest_file.exists():
            overwritten += 1

# -----------------------------------------------------------------------
# 3. Summary
# -----------------------------------------------------------------------
print(f"""
============================================================
  Rename Summary
============================================================
  Source files:    {len(source_files)}
  SKU matched:    {matched}
  Unmatched:      {unmatched}
  Skipped:        {skipped}
  {"Would copy" if dry_run else "Copied"}:        {matched + unmatched - skipped if dry_run else copied}
  Errors:         {len(errors)}
============================================================""")

if errors:
    print("\nErrors:")
    for e in errors[:20]:
        print(f"  {e}")
    if len(errors) > 20:
        print(f"  ... and {len(errors) - 20} more")

if dry_run:
    print("\n  (Dry run — no files were copied. Remove --dry-run to execute.)")
PYEOF
