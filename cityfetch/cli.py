"""
cli.py
------
Main entry point for CDS-CityFetch CLI tool.

Uses Click for a modern, user-friendly command-line interface.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from tqdm import tqdm

from . import __version__, __description__
from .fetcher import CityFetcher
from .language_service import fetch_language_codes
from .outputs.sql_generator import SQLGenerator
from .outputs.json_generator import JSONGenerator
from .outputs.csv_generator import CSVGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


@click.group(invoke_without_command=True)
@click.option(
    "--version",
    is_flag=True,
    help="Show version information and exit.",
)
@click.pass_context
def cli(ctx: click.Context, version: bool) -> None:
    """
    CDS-CityFetch - Fetch city data from Wikidata.
    
    Fetches comprehensive city information from Wikidata SPARQL endpoint
    and exports to SQL (MySQL), JSON, or CSV format.
    
    Run without a command to see available options.
    """
    if version:
        click.echo(f"CDS-CityFetch {__version__}")
        click.echo(__description__)
        ctx.exit(0)
    
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="fetch")
@click.option(
    "-l", "--languages",
    required=True,
    help="Comma-separated language codes (e.g., 'en,de,fr'). Required.",
)
@click.option(
    "-o", "--output",
    default=None,
    help="Output file name. Default: cities.sql (or appropriate extension for format).",
)
@click.option(
    "--dir",
    "output_dir",
    default=".",
    help="Output directory. Default: current directory.",
)
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["sql", "json", "csv"], case_sensitive=False),
    default="sql",
    help="Output format. Default: sql",
)
@click.option(
    "-d", "--data-dir",
    default=None,
    help="Working directory for temporary files.",
)
@click.option(
    "--batch-size",
    default=1000,
    type=int,
    help="Number of rows per batch (SQL format only). Default: 1000",
)
@click.option(
    "--max-pages",
    default=40,
    type=int,
    help="Maximum pages to fetch per language (safety limit). Default: 40",
)
@click.option(
    "--page-size",
    default=500,
    type=int,
    help="Number of cities per Wikidata request. Default: 500",
)
@click.option(
    "-v", "--verbose",
    is_flag=True,
    help="Show detailed progress information.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Simulate the fetch without writing files.",
)
@click.option(
    "--webhook-url",
    default=None,
    help="URL to POST webhook notification after successful fetch.",
)
@click.option(
    "--webhook-secret",
    default=None,
    help="Secret token for webhook authentication (sent as X-Webhook-Secret header).",
)
def fetch_command(
    languages: str,
    output: str | None,
    output_dir: str,
    output_format: str,
    data_dir: str | None,
    batch_size: int,
    max_pages: int,
    page_size: int,
    verbose: bool,
    dry_run: bool,
    webhook_url: str | None,
    webhook_secret: str | None,
) -> None:
    """
    Fetch city data from Wikidata.
    
    This is the main command that connects to Wikidata SPARQL endpoint,
    fetches city information for the specified languages, and exports
    to your chosen format.
    
    Examples:
    
        # Fetch English cities to SQL file in current directory
        cityfetch fetch -l en
        
        # Fetch to specific directory
        cityfetch fetch -l en --dir ./output
        
        # Fetch multiple languages to JSON
        cityfetch fetch -l en,de,fr -f json
        
        # Fetch with webhook notification
        cityfetch fetch -l en --webhook-url http://myapp:8080/reload
        
        # Fetch with progress bar and verbose output
        cityfetch fetch -l en -v
        
        # Dry run (test without writing files)
        cityfetch fetch -l en --dry-run
    """
    # Parse and validate languages
    lang_list = fetch_language_codes(override=languages)
    if not lang_list:
        click.echo("Error: No valid languages specified.", err=True)
        sys.exit(1)
    
    click.echo(f"Fetching city data for {len(lang_list)} language(s): {', '.join(lang_list)}")
    
    if dry_run:
        click.echo("DRY RUN: No files will be written.")
    
    # Determine output filename
    if output:
        output_filename = output
    else:
        # Auto-generate filename based on format
        extensions = {"sql": "cities.sql", "json": "cities.json", "csv": "cities.csv"}
        output_filename = extensions.get(output_format.lower(), "cities.sql")
    
    # Setup paths
    output_path = Path(output_dir) / output_filename
    if data_dir:
        work_dir = Path(data_dir)
    else:
        work_dir = Path(output_dir)
    
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize fetcher with progress callback
    fetcher = CityFetcher(
        max_pages=max_pages,
        page_size=page_size,
        verbose=verbose,
    )
    
    # Collect all cities
    all_cities = []
    
    with tqdm(
        total=len(lang_list),
        desc="Languages",
        disable=not verbose,
        unit="lang",
    ) as lang_pbar:
        for lang in lang_list:
            lang_pbar.set_description(f"Fetching {lang}")
            
            try:
                cities = fetcher.fetch_cities_for_language(lang)
                all_cities.extend(cities)
                
                if verbose:
                    logger.info(f"Fetched {len(cities)} cities for '{lang}'")
                    
            except Exception as exc:
                if verbose:
                    logger.warning(f"Failed to fetch cities for '{lang}': {exc}")
                click.echo(f"Warning: Failed to fetch for language '{lang}': {exc}", err=True)
            
            lang_pbar.update(1)
    
    if not all_cities:
        click.echo("Error: No cities fetched from Wikidata.", err=True)
        sys.exit(1)
    
    click.echo(f"Total unique cities collected: {len(all_cities)}")
    
    if dry_run:
        click.echo(f"DRY RUN: Would write {len(all_cities)} cities to {output}")
        return
    
    # Generate output file
    output_format = output_format.lower()
    
    try:
        if output_format == "sql":
            generator = SQLGenerator(batch_size=batch_size)
        elif output_format == "json":
            generator = JSONGenerator()
        elif output_format == "csv":
            generator = CSVGenerator()
        else:
            click.echo(f"Error: Unknown format '{output_format}'", err=True)
            sys.exit(1)
        
        output_file = generator.generate(all_cities, output_path, work_dir)
        click.echo(f"✓ Successfully wrote {len(all_cities)} cities to {output_file}")
        
        # Send webhook notification if configured
        if webhook_url:
            try:
                import httpx
                import json
                from datetime import datetime, timezone
                
                payload = {
                    "event": "fetch_complete",
                    "data": {
                        "file_path": str(output_file),
                        "absolute_path": str(output_file.absolute()),
                        "format": output_format,
                        "languages": lang_list,
                        "record_count": len(all_cities),
                        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "success": True,
                    }
                }
                
                headers = {"Content-Type": "application/json"}
                if webhook_secret:
                    headers["X-Webhook-Secret"] = webhook_secret
                
                click.echo(f"Sending webhook notification to {webhook_url}...")
                response = httpx.post(webhook_url, json=payload, headers=headers, timeout=30)
                
                if response.is_success:
                    click.echo(f"✓ Webhook notification sent successfully (HTTP {response.status_code})")
                else:
                    click.echo(f"⚠ Webhook notification failed (HTTP {response.status_code}): {response.text[:200]}", err=True)
                    
            except Exception as exc:
                click.echo(f"⚠ Failed to send webhook notification: {exc}", err=True)
        
    except Exception as exc:
        click.echo(f"Error writing output file: {exc}", err=True)
        sys.exit(1)


@cli.command(name="cron")
@click.option(
    "-l", "--languages",
    required=True,
    help="Languages to fetch (e.g., 'en,de').",
)
@click.option(
    "-o", "--output",
    default="./cities.sql",
    help="Output file path.",
)
@click.option(
    "--interval",
    default="weekly",
    type=click.Choice(["hourly", "daily", "weekly", "monthly"]),
    help="How often to run. Default: weekly",
)
@click.option(
    "--user/--system",
    default=True,
    help="Install for current user (default) or system-wide (requires sudo).",
)
def cron_command(
    languages: str,
    output: str,
    interval: str,
    user: bool,
) -> None:
    """
    Show cron setup instructions for scheduled fetching.
    
    This command displays the crontab line needed to schedule
    automatic city data fetching. You must manually add it to
    your crontab using 'crontab -e'.
    
    Examples:
    
        # Show weekly fetch cron line
        cityfetch cron -l en
        
        # Show daily fetch cron line for multiple languages
        cityfetch cron -l en,de,fr -o /var/data/cities.sql --interval daily
    """
    # Determine cron schedule
    schedules = {
        "hourly": "0 * * * *",
        "daily": "0 2 * * *",
        "weekly": "0 2 * * 0",
        "monthly": "0 2 1 * *",
    }
    schedule = schedules[interval]
    
    # Detect how we're running (frozen binary or Python script)
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller frozen executable
        binary_str = sys.executable
    else:
        # Running as Python script
        binary_str = "cityfetch"
    
    # Build command
    cmd = f'{binary_str} fetch -l {languages} -o "{output}"'
    
    click.echo("=" * 60)
    click.echo("CRON SETUP INSTRUCTIONS")
    click.echo("=" * 60)
    click.echo()
    click.echo(f"To schedule {interval} city data fetching, add this line to your crontab:")
    click.echo()
    click.echo(f"  {schedule} {cmd}")
    click.echo()
    click.echo("Steps to install:")
    click.echo()
    
    if user:
        click.echo("  1. Open your crontab:")
        click.echo("     crontab -e")
        click.echo()
        click.echo("  2. Paste the line above at the end of the file")
        click.echo()
        click.echo("  3. Save and exit (Ctrl+O, Enter, Ctrl+X for nano)")
    else:
        click.echo("  1. Open system crontab (requires sudo):")
        click.echo("     sudo crontab -e")
        click.echo()
        click.echo("  2. Paste the line above at the end of the file")
        click.echo()
        click.echo("  3. Save and exit")
    
    click.echo()
    click.echo("To verify your crontab is set up correctly:")
    click.echo("  crontab -l")
    click.echo()
    click.echo("To remove the scheduled task later:")
    click.echo("  crontab -e  # Then delete the line")
    click.echo()
    click.echo("=" * 60)


@cli.command(name="version")
def version_command() -> None:
    """Show version information."""
    click.echo(f"CDS-CityFetch {__version__}")
    click.echo(__description__)
    click.echo()
    click.echo("City data source: Wikidata (https://www.wikidata.org)")
    click.echo("Output formats: SQL (MySQL), JSON, CSV")


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
