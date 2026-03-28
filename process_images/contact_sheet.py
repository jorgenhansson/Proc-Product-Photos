"""Thumbnail contact sheet for visual QA.

Generates a single large image with all output images as thumbnails in a
grid, grouped by category.  Recovered images get a green border, flagged
images get a red border.  Category headers show name and count.

Can be used standalone or integrated into the pipeline via --contact-sheet.

Standalone:
    python -m process_images.contact_sheet \
        --input ./output \
        --results ./results.json \
        --output ./contact.jpg \
        --thumb-size 100 \
        --columns 20
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Colors (RGB)
_WHITE = (255, 255, 255)
_LIGHT_GRAY = (240, 240, 240)
_DARK_GRAY = (80, 80, 80)
_GREEN = (46, 125, 50)
_RED = (198, 40, 40)
_YELLOW = (245, 180, 0)
_HEADER_BG = (55, 71, 79)
_HEADER_FG = (255, 255, 255)


def generate_contact_sheet(
    image_dir: Path,
    results: dict[str, dict],
    output_path: Path,
    thumb_size: int = 100,
    columns: int = 20,
    quality: int = 90,
) -> None:
    """Generate a contact sheet image grouped by category.

    Args:
        image_dir: Directory containing output images.
        results: Per-image results dict (from --results JSON).
        output_path: Path to write the contact sheet (JPEG or PNG).
        thumb_size: Thumbnail size in pixels.
        columns: Number of columns in the grid.
        quality: JPEG quality (1-100).
    """
    # Group images by category
    by_category: dict[str, list[tuple[str, dict]]] = {}
    uncategorized: list[tuple[str, dict]] = []

    # Match output files to results
    output_files = {f.name: f for f in image_dir.iterdir() if f.is_file()}

    for filename, info in sorted(results.items()):
        cat = info.get("category", "UNKNOWN") or "UNKNOWN"
        # Find matching output file (results key is source name, output is -cropped)
        # Try direct match first, then stem-based match
        matched_file = _find_output_file(filename, output_files)
        if matched_file is None:
            continue  # No output for this image (flagged/failed)

        entry = (matched_file.name, info)
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(entry)

    # Also add output files not in results (e.g. if results.json is partial)
    known_outputs = set()
    for entries in by_category.values():
        for fname, _ in entries:
            known_outputs.add(fname)

    for fname, fpath in sorted(output_files.items()):
        if fname not in known_outputs and fname.lower().endswith((".jpg", ".jpeg", ".png")):
            uncategorized.append((fname, {}))

    if uncategorized:
        by_category["UNCATEGORIZED"] = uncategorized

    if not by_category:
        logger.warning("No images found for contact sheet")
        return

    # Calculate layout
    border = 2
    cell_size = thumb_size + border * 2
    header_height = 28
    padding = 4

    total_width = columns * cell_size + padding * 2
    total_height = padding

    for cat, entries in sorted(by_category.items()):
        total_height += header_height  # category header
        rows = (len(entries) + columns - 1) // columns
        total_height += rows * cell_size

    total_height += padding

    # Create canvas
    canvas = Image.new("RGB", (total_width, total_height), _WHITE)
    draw = ImageDraw.Draw(canvas)

    # Try to load a small font
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 10)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        except (OSError, IOError):
            font = ImageFont.load_default()
            font_small = font

    y = padding

    for cat, entries in sorted(by_category.items()):
        # Draw category header
        draw.rectangle(
            [padding, y, total_width - padding, y + header_height],
            fill=_HEADER_BG,
        )
        ok_count = sum(1 for _, info in entries if info.get("status", "") in ("ok", "recovered"))
        header_text = f"  {cat} ({len(entries)} images, {ok_count} OK)"
        draw.text((padding + 4, y + 6), header_text, fill=_HEADER_FG, font=font)
        y += header_height

        # Draw thumbnails
        for i, (fname, info) in enumerate(entries):
            col = i % columns
            row = i // columns
            x = padding + col * cell_size
            cy = y + row * cell_size

            # Determine border color
            status = info.get("status", "")
            flags = info.get("flags", [])
            if status == "recovered":
                border_color = _GREEN
            elif status in ("flagged", "failed") or flags:
                border_color = _RED
            else:
                border_color = _LIGHT_GRAY

            # Draw border rectangle
            draw.rectangle(
                [x, cy, x + cell_size - 1, cy + cell_size - 1],
                fill=border_color,
            )

            # Load and paste thumbnail
            img_path = image_dir / fname
            try:
                thumb = Image.open(img_path)
                thumb.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
                # Center on cell
                tx = x + border + (thumb_size - thumb.size[0]) // 2
                ty = cy + border + (thumb_size - thumb.size[1]) // 2
                canvas.paste(thumb, (tx, ty))
            except Exception as e:
                # Draw placeholder
                draw.rectangle(
                    [x + border, cy + border,
                     x + border + thumb_size, cy + border + thumb_size],
                    fill=_LIGHT_GRAY,
                )
                draw.text(
                    (x + border + 4, cy + border + thumb_size // 2),
                    "ERR", fill=_RED, font=font_small,
                )

        rows = (len(entries) + columns - 1) // columns
        y += rows * cell_size

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = "JPEG" if output_path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
    if fmt == "JPEG":
        canvas = canvas.convert("RGB")
        canvas.save(output_path, format=fmt, quality=quality, optimize=True)
    else:
        canvas.save(output_path, format=fmt)

    total_images = sum(len(v) for v in by_category.values())
    logger.info(
        "Contact sheet: %d images across %d categories → %s (%dx%d)",
        total_images, len(by_category), output_path,
        total_width, total_height,
    )


def _find_output_file(
    source_name: str, output_files: dict[str, Path]
) -> Optional[Path]:
    """Find the output file corresponding to a source image name.

    Tries multiple patterns since source_name is the input filename but
    output may have -cropped suffix and different extension.
    """
    stem = Path(source_name).stem

    # Direct match
    if source_name in output_files:
        return output_files[source_name]

    # Try {stem}-cropped.{ext} patterns
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = f"{stem}-cropped.{ext}"
        if candidate in output_files:
            return output_files[candidate]

    # Lowercase fallback
    stem_lower = stem.lower()
    for fname, fpath in output_files.items():
        if fname.lower().startswith(stem_lower):
            return fpath

    return None


# -- Standalone CLI --

def _cli():
    """Standalone CLI entry point."""
    import typer

    app = typer.Typer(
        name="contact-sheet",
        help="Generate thumbnail contact sheet for visual QA.",
        add_completion=False,
    )

    @app.command()
    def main(
        input_dir: Path = typer.Option(
            ..., "--input", "-i", help="Directory containing output images"
        ),
        results_file: Path = typer.Option(
            ..., "--results", "-r", help="Per-image results JSON"
        ),
        output: Path = typer.Option(
            ..., "--output", "-o", help="Output contact sheet path (.jpg or .png)"
        ),
        thumb_size: int = typer.Option(
            100, "--thumb-size", "-t", help="Thumbnail size in pixels"
        ),
        columns: int = typer.Option(
            20, "--columns", "-c", help="Number of columns"
        ),
        quality: int = typer.Option(
            90, "--quality", "-q", help="JPEG quality (1-100)"
        ),
    ) -> None:
        """Generate a contact sheet from pipeline output."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        )

        if not input_dir.is_dir():
            typer.echo(f"Error: {input_dir} not found", err=True)
            raise typer.Exit(1)
        if not results_file.is_file():
            typer.echo(f"Error: {results_file} not found", err=True)
            raise typer.Exit(1)

        with open(results_file, "r") as f:
            results = json.load(f)

        generate_contact_sheet(
            input_dir, results, output,
            thumb_size=thumb_size, columns=columns, quality=quality,
        )
        typer.echo(f"Contact sheet written to {output}")

    app()


if __name__ == "__main__":
    _cli()
