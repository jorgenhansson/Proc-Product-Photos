"""SKU-to-article mapping loader and validator."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .models import Flag, MappingRow

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"supplier_sku", "store_article", "suffix", "category"}
OPTIONAL_COLUMNS = {"variant", "color", "angle", "notes"}
SAFE_FILENAME_RE = re.compile(r"^[\w\-]+$")


@dataclass
class MappingLookup:
    """Validated mapping data with lookup by supplier SKU."""

    rows_by_sku: dict[str, list[MappingRow]] = field(default_factory=dict)
    issues: list[tuple[Flag, str]] = field(default_factory=list)

    def lookup(self, sku: str) -> list[MappingRow]:
        """Find all mapping rows for a supplier SKU.

        Lookup is case-insensitive to handle case-insensitive filesystems
        (macOS APFS/HFS+). Original case is preserved in MappingRow.
        """
        return self.rows_by_sku.get(sku.lower(), [])


def load_mapping(path: Path) -> MappingLookup:
    """Load and validate a mapping file (CSV or XLSX).

    Returns a MappingLookup with rows keyed by supplier_sku and any
    validation issues found during loading.
    """
    result = MappingLookup()

    # Read file
    try:
        if path.suffix.lower() in (".xlsx", ".xls"):
            df = pd.read_excel(path, dtype=str)
        else:
            df = pd.read_csv(path, dtype=str)
    except Exception as e:
        result.issues.append(
            (Flag.IMAGE_READ_ERROR, f"Failed to read mapping file: {e}")
        )
        return result

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Validate required columns
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        result.issues.append(
            (Flag.IMAGE_READ_ERROR, f"Missing required columns: {missing}")
        )
        return result

    # Fill NaN with empty string for optional columns
    for col in OPTIONAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df.fillna("")

    # Parse rows
    rows_by_sku: dict[str, list[MappingRow]] = {}
    output_filenames: list[str] = []

    for idx, row_data in df.iterrows():
        sku = str(row_data["supplier_sku"]).strip()
        article = str(row_data["store_article"]).strip()
        suffix = str(row_data["suffix"]).strip()
        category = str(row_data["category"]).strip()

        if not sku:
            result.issues.append(
                (Flag.MISSING_MAPPING, f"Row {idx}: empty supplier_sku")
            )
            continue

        if not article or not suffix:
            result.issues.append(
                (Flag.MISSING_MAPPING, f"Row {idx}: empty store_article or suffix for SKU '{sku}'")
            )
            continue

        # Validate category
        if not category:
            result.issues.append(
                (Flag.MISSING_MAPPING, f"SKU '{sku}': missing category")
            )

        mapping_row = MappingRow(
            supplier_sku=sku,
            store_article=article,
            suffix=suffix,
            category=category,
            variant=str(row_data.get("variant", "")).strip(),
            color=str(row_data.get("color", "")).strip(),
            angle=str(row_data.get("angle", "")).strip(),
            notes=str(row_data.get("notes", "")).strip(),
        )

        # Validate output filename
        out_name = mapping_row.output_filename
        stem = Path(out_name).stem
        if not SAFE_FILENAME_RE.match(stem):
            result.issues.append(
                (Flag.NAMING_CONFLICT, f"SKU '{sku}': unsafe output filename '{out_name}'")
            )

        output_filenames.append(out_name)

        sku_key = sku.lower()
        if sku_key not in rows_by_sku:
            rows_by_sku[sku_key] = []
        rows_by_sku[sku_key].append(mapping_row)

    # Check for duplicate output filenames
    filename_counts = Counter(output_filenames)
    for fname, count in filename_counts.items():
        if count > 1:
            result.issues.append(
                (Flag.NAMING_CONFLICT, f"Output filename '{fname}' appears {count} times")
            )

    result.rows_by_sku = rows_by_sku
    return result
