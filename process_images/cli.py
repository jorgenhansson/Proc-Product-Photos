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
    results_file: Optional[Path] = typer.Option(
        None, "--results", help="Output per-image results JSON (for diff analysis)"
    ),
    contact_sheet: Optional[Path] = typer.Option(
        None, "--contact-sheet", help="Output contact sheet image (.jpg or .png)"
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
    parallel: bool = typer.Option(
        False, "--parallel", "-p",
        help="Enable parallel processing using all available CPU cores",
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers", "-w",
        help="Number of parallel workers (implies --parallel). Default: CPU count.",
    ),
    resume: bool = typer.Option(
        False, "--resume",
        help="Resume from checkpoint — skip already-processed images",
    ),
    force_resume: bool = typer.Option(
        False, "--force",
        help="Force resume even if rules YAML changed since checkpoint",
    ),
    no_checkpoint: bool = typer.Option(
        False, "--no-checkpoint",
        help="Disable checkpoint writing (for clean runs)",
    ),
    quality_gate: Optional[str] = typer.Option(
        None, "--quality-gate",
        help="Quality gate action: warn, abort, or ignore (overrides rules YAML)",
    ),
    no_quality_gate: bool = typer.Option(
        False, "--no-quality-gate",
        help="Disable quality gate checks entirely",
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

    # Quality gate overrides
    if no_quality_gate:
        config.quality_gate.enabled = False
    elif quality_gate is not None:
        valid_actions = ("warn", "abort", "ignore")
        if quality_gate not in valid_actions:
            log.error(
                "Invalid --quality-gate value '%s'. Valid: %s",
                quality_gate, ", ".join(valid_actions),
            )
            raise typer.Exit(1)
        config.quality_gate.enabled = True
        config.quality_gate.action = quality_gate

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

    # -- Determine parallelism --
    import os
    num_workers = 0  # sequential
    if workers is not None and workers > 1:
        num_workers = workers
    elif parallel:
        num_workers = max(1, os.cpu_count() or 1)
        # Leave one core free for the main thread and OS
        if num_workers > 2:
            num_workers -= 1

    if num_workers > 1:
        log.info("Parallel mode: %d workers", num_workers)
    else:
        log.info("Sequential mode")

    # -- Checkpoint --
    from .checkpoint import Checkpoint, hash_file, load_checkpoint, new_checkpoint

    cp: Optional[Checkpoint] = None
    if not no_checkpoint:
        cp_path = output_dir / ".checkpoint.json"
        config_hash = hash_file(rules_file)

        if resume:
            try:
                cp = load_checkpoint(cp_path, config_hash, force=force_resume)
            except ValueError as e:
                log.error("%s", e)
                raise typer.Exit(1)
        else:
            cp = new_checkpoint(cp_path, config_hash)

    # -- Run pipeline --
    from .pipeline import Pipeline, QualityGateError

    pipeline = Pipeline(config, mapping, primary, fallback)
    try:
        stats = pipeline.run(
            input_dir, output_dir, review_dir,
            limit=limit, workers=num_workers, checkpoint=cp,
        )
    except QualityGateError as e:
        stats = pipeline.stats
        log.error("Pipeline aborted by quality gate:\n%s", e.detail)
        # Still write partial stats and manifest
        summary = stats.to_console()
        log.info("\n%s", summary)
        if stats_file:
            stats.to_json(stats_file)
        raise typer.Exit(2)

    # -- Output --
    summary = stats.to_console()
    log.info("\n%s", summary)

    if stats_file:
        stats.to_json(stats_file)
        log.info("Statistics written to %s", stats_file)

    if results_file:
        stats.results_to_json(results_file)
        log.info("Per-image results written to %s", results_file)

    if contact_sheet:
        from .contact_sheet import generate_contact_sheet

        # Build results dict for contact sheet
        results_data = {}
        for r in stats.results:
            results_data[r.source_path.name] = {
                "status": r.status.value,
                "category": r.category,
                "flags": [f.value for f in r.flags],
                "fill_ratio": r.crop_metrics.fill_ratio if r.crop_metrics else 0.0,
            }
        generate_contact_sheet(
            output_dir, results_data, contact_sheet,
        )
        log.info("Contact sheet written to %s", contact_sheet)

    if html_report:
        from .reporting import write_html_report

        write_html_report(stats.to_dict(), stats.results, html_report)
        log.info("HTML report written to %s", html_report)


if __name__ == "__main__":
    app()
