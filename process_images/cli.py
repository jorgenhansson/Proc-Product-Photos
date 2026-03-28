"""CLI entry point for the image processing pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="process-images",
    help="Batch processing pipeline for supplier product images.",
    add_completion=False,
)


@app.command()
def main(
    input_dir: Path = typer.Option(
        ..., "--input", "-i", help="Input directory containing supplier images"
    ),
    output_dir: Path = typer.Option(
        ..., "--output", "-o", help="Output directory for processed images"
    ),
    review_dir: Path = typer.Option(
        ..., "--review", help="Review directory for flagged/failed images"
    ),
    mapping_file: Path = typer.Option(
        ..., "--mapping", "-m", help="CSV or XLSX mapping file (SKU to article)"
    ),
    rules_file: Path = typer.Option(
        ..., "--rules", "-r", help="YAML rules/configuration file"
    ),
    canvas_size: Optional[int] = typer.Option(
        None, "--canvas-size", "-s",
        help="Output canvas size in pixels (default: 1000, from rules YAML)",
    ),
    output_format: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Output image format: jpg, png, webp, tiff (default: jpg)",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite",
        help="Overwrite existing output files without flagging as conflict",
    ),
    stats_file: Optional[Path] = typer.Option(
        None, "--stats", help="Output JSON statistics file"
    ),
    html_report: Optional[Path] = typer.Option(
        None, "--html-report", help="Output HTML report file"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose/debug logging"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate inputs and exit without processing"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", help="Process only the first N images"
    ),
) -> None:
    """Process supplier product images: crop, resize, rename, and place on canvas."""
    # -- Logging --
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("process_images")

    # -- Validate paths --
    if not input_dir.is_dir():
        log.error("Input directory does not exist: %s", input_dir)
        raise typer.Exit(1)
    if not mapping_file.is_file():
        log.error("Mapping file does not exist: %s", mapping_file)
        raise typer.Exit(1)
    if not rules_file.is_file():
        log.error("Rules file does not exist: %s", rules_file)
        raise typer.Exit(1)

    # -- Validate format --
    from .io_utils import SUPPORTED_OUTPUT_FORMATS

    if output_format is not None:
        fmt = output_format.lower().replace("jpeg", "jpg")
        if fmt not in SUPPORTED_OUTPUT_FORMATS:
            log.error(
                "Unsupported output format '%s'. Supported: %s",
                output_format, ", ".join(sorted(SUPPORTED_OUTPUT_FORMATS)),
            )
            raise typer.Exit(1)

    # -- Load configuration --
    from .config import load_config

    config = load_config(rules_file)

    # CLI overrides take precedence over YAML
    if canvas_size is not None:
        config.global_config.canvas_size = canvas_size
    if output_format is not None:
        config.global_config.output_format = output_format.lower().replace("jpeg", "jpg")
    if overwrite:
        config.global_config.overwrite = True

    log.info(
        "Config: %d categories, fallback=%s, canvas=%dpx, format=%s, overwrite=%s",
        len(config.categories),
        "on" if config.fallback.enabled else "off",
        config.global_config.canvas_size,
        config.global_config.output_format,
        config.global_config.overwrite,
    )

    # -- Load mapping --
    from .mapping import load_mapping

    mapping = load_mapping(mapping_file)
    for flag, msg in mapping.issues:
        log.warning("Mapping [%s]: %s", flag.value, msg)
    log.info("Loaded %d SKU mappings", len(mapping.rows_by_sku))

    if dry_run:
        log.info("Dry run complete -- exiting after validation")
        raise typer.Exit(0)

    # -- Build strategies --
    from .crop.classical import ClassicalCropStrategy
    from .crop.ai_fallback import AIFallbackCropStrategy

    primary = ClassicalCropStrategy()
    fallback = AIFallbackCropStrategy() if config.fallback.enabled else None

    # -- Run pipeline --
    from .pipeline import Pipeline

    pipeline = Pipeline(config, mapping, primary, fallback)
    stats = pipeline.run(input_dir, output_dir, review_dir, limit=limit)

    # -- Output --
    summary = stats.to_console()
    log.info("\n%s", summary)

    if stats_file:
        stats.to_json(stats_file)
        log.info("Statistics written to %s", stats_file)

    if html_report:
        from .reporting import write_html_report

        write_html_report(stats.to_dict(), stats.results, html_report)
        log.info("HTML report written to %s", html_report)


if __name__ == "__main__":
    app()
