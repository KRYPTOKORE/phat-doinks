"""CLI interface using Click."""

import shutil
import time
from pathlib import Path

import click
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from meme_sorter.config import (
    build_app_config,
    get_defaults_dir,
    get_state_dir,
    CATEGORIES_FILENAME,
    CONFIG_FILENAME,
)
from meme_sorter.core import run_sort, run_recheck, run_undo
from meme_sorter.events import EventBus, FileProcessed, ProgressUpdate, RunComplete, RunStarted
from meme_sorter.state import StateStore

console = Console()


def _get_state_and_config(path, cli_overrides=None):
    meme_dir = Path(path).resolve()
    state_dir = get_state_dir(meme_dir)
    config = build_app_config(meme_dir, cli_overrides)
    state = StateStore(state_dir / "state.db")
    return meme_dir, config, state


def _make_cli_bus(console):
    """Create an event bus with Rich console output handlers."""
    bus = EventBus()
    stats = {"moved": 0, "kept": 0, "errors": 0, "current": 0, "total": 0}
    move_counts = {}

    def on_started(event: RunStarted):
        stats["total"] = event.total
        console.print(f"[bold]{event.mode.upper()} MODE[/bold]: {event.total} files to process")

    def on_processed(event: FileProcessed):
        if event.error:
            stats["errors"] += 1
            console.print(f"  [red]ERROR[/red] [{event.path.name[:40]}]: {event.error}")
        if event.moved:
            stats["moved"] += 1
            cat = event.new_category
            move_counts[cat] = move_counts.get(cat, 0) + 1
            console.print(
                f"  [yellow]MOVE[/yellow] {event.path.name[:40]}  "
                f"[{event.current_category}] -> [{event.new_category}]"
            )

    def on_progress(event: ProgressUpdate):
        stats["current"] = event.current
        elapsed = event.elapsed
        rate = event.current / elapsed if elapsed > 0 else 0
        eta = (event.total - event.current) / rate if rate > 0 else 0
        console.print(
            f"  [{event.current}/{event.total}] "
            f"{rate:.1f}/s  ETA: {eta / 60:.0f}m  "
            f"moved: {stats['moved']}  errors: {stats['errors']}",
            end="\r",
        )

    def on_complete(event: RunComplete):
        console.print()
        console.print(f"\n[bold green]Complete![/bold green]")
        console.print(f"  Processed: {event.processed}")
        console.print(f"  Moved: {event.moved}")
        console.print(f"  Kept: {event.kept}")
        console.print(f"  Errors: {event.errors}")
        console.print(f"  Duration: {event.duration / 60:.1f} minutes")
        if move_counts:
            console.print("\n  Move breakdown:")
            for cat, count in sorted(move_counts.items(), key=lambda x: -x[1]):
                console.print(f"    {cat:40s} {count:5d}")

    bus.subscribe(RunStarted, on_started)
    bus.subscribe(FileProcessed, on_processed)
    bus.subscribe(ProgressUpdate, on_progress)
    bus.subscribe(RunComplete, on_complete)

    return bus


@click.group()
@click.version_option()
def main():
    """Phat Doinks -- sort your meme collection using local AI vision models."""
    pass


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--categories", "cat_file", type=click.Path(), help="Custom categories file to copy")
def init(path, cat_file):
    """Initialize a meme directory for sorting."""
    meme_dir = Path(path).resolve()
    state_dir = get_state_dir(meme_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    # Copy default config if not present
    config_dest = state_dir / CONFIG_FILENAME
    if not config_dest.exists():
        default_config = get_defaults_dir() / CONFIG_FILENAME
        if default_config.exists():
            shutil.copy2(str(default_config), str(config_dest))
        else:
            config_dest.write_text(_DEFAULT_CONFIG)
        console.print(f"  Created {config_dest}")

    # Copy categories
    cat_dest = state_dir / CATEGORIES_FILENAME
    if cat_file:
        shutil.copy2(cat_file, str(cat_dest))
        console.print(f"  Copied categories from {cat_file}")
    elif not cat_dest.exists():
        default_cats = get_defaults_dir() / CATEGORIES_FILENAME
        if default_cats.exists():
            shutil.copy2(str(default_cats), str(cat_dest))
        else:
            cat_dest.write_text(_DEFAULT_CATEGORIES)
        console.print(f"  Created {cat_dest}")

    console.print(f"\n[bold green]Initialized[/bold green] {meme_dir}")
    console.print(f"  Edit categories: {cat_dest}")
    console.print(f"  Edit config:     {config_dest}")


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Classify but don't move files")
@click.option("--limit", type=int, default=0, help="Max files to process (0=all)")
@click.option("--resume", is_flag=True, help="Resume from previous run")
@click.option("--workers", type=int, help="Override concurrent workers count")
@click.option("--model", type=str, help="Override Ollama model")
@click.option("--endpoint", type=str, help="Override Ollama endpoint URL")
def sort(path, dry_run, limit, resume, workers, model, endpoint):
    """Sort unsorted memes in the directory."""
    overrides = {"workers": workers, "model": model, "endpoint": endpoint}
    meme_dir, config, state = _get_state_and_config(path, overrides)
    bus = _make_cli_bus(console)

    resume_id = None
    if resume:
        history = state.get_run_history(1)
        if history and history[0]["mode"] == "sort" and not history[0]["finished_at"]:
            resume_id = history[0]["run_id"]
            console.print(f"Resuming run {resume_id}")

    try:
        run_sort(meme_dir, config, bus, state, dry_run, limit, resume_id)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Use --resume to continue.[/yellow]")
    finally:
        state.close()


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Classify but don't move files")
@click.option("--limit", type=int, default=0, help="Max files to process (0=all)")
@click.option("--resume", is_flag=True, help="Resume from previous run")
@click.option("--folder", type=str, help="Only recheck a specific category folder")
@click.option("--workers", type=int, help="Override concurrent workers count")
@click.option("--model", type=str, help="Override Ollama model")
@click.option("--endpoint", type=str, help="Override Ollama endpoint URL")
def recheck(path, dry_run, limit, resume, folder, workers, model, endpoint):
    """Re-classify already-sorted files."""
    overrides = {"workers": workers, "model": model, "endpoint": endpoint}
    meme_dir, config, state = _get_state_and_config(path, overrides)
    bus = _make_cli_bus(console)

    resume_id = None
    if resume:
        history = state.get_run_history(1)
        if history and history[0]["mode"] == "recheck" and not history[0]["finished_at"]:
            resume_id = history[0]["run_id"]
            console.print(f"Resuming run {resume_id}")

    try:
        run_recheck(meme_dir, config, bus, state, dry_run, limit, folder, resume_id)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Use --resume to continue.[/yellow]")
    finally:
        state.close()


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def status(path):
    """Show current state: file counts per category."""
    meme_dir = Path(path).resolve()
    config = build_app_config(meme_dir)

    table = Table(title=f"Meme Directory: {meme_dir}")
    table.add_column("Category", style="cyan")
    table.add_column("Files", justify="right", style="green")

    total = 0
    exts = {"." + e for e in config.processing.image_extensions} | {
        "." + e for e in config.processing.video_extensions
    }

    for cat in sorted(config.categories.keys()):
        cat_dir = meme_dir / cat
        if cat_dir.is_dir():
            count = sum(
                1 for f in cat_dir.iterdir()
                if f.is_file() and f.suffix.lower() in exts
            )
            if count > 0:
                table.add_row(cat, str(count))
                total += count

    # Not Memes folder
    nm_dir = meme_dir / config.non_meme_folder
    if nm_dir.is_dir():
        count = sum(
            1 for f in nm_dir.iterdir()
            if f.is_file() and f.suffix.lower() in exts
        )
        if count > 0:
            table.add_row(config.non_meme_folder, str(count))
            total += count

    # Unsorted in root
    unsorted = sum(
        1 for f in meme_dir.iterdir()
        if f.is_file() and f.suffix.lower() in exts
    )
    if unsorted > 0:
        table.add_row("(unsorted)", str(unsorted))
        total += unsorted

    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")

    console.print(table)


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--run-id", type=str, help="Undo a specific run (default: latest)")
@click.option("--dry-run", is_flag=True, help="Show what would be undone")
def undo(path, run_id, dry_run):
    """Undo the last sort/recheck operation."""
    meme_dir, config, state = _get_state_and_config(path)
    batch = state.get_undo_batch(run_id)

    if not batch:
        console.print("Nothing to undo.")
        state.close()
        return

    console.print(f"Will revert {len(batch)} moves:")
    for move in batch[:10]:
        console.print(f"  {Path(move['dest_path']).name} -> {Path(move['source_path']).parent.name}/")
    if len(batch) > 10:
        console.print(f"  ... and {len(batch) - 10} more")

    if dry_run:
        console.print("\n[yellow]Dry run -- no files moved.[/yellow]")
        state.close()
        return

    reverted = run_undo(state, run_id)
    console.print(f"\n[bold green]Reverted {reverted} moves.[/bold green]")
    state.close()


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
def gui(path):
    """Launch the graphical interface."""
    try:
        from meme_sorter.gui import launch_gui
    except ImportError:
        console.print("[red]PySide6 not installed.[/red] Install with:")
        console.print("  pip install meme-sorter[gui]")
        raise SystemExit(1)
    launch_gui(Path(path).resolve())


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option("--edit", is_flag=True, help="Open config in $EDITOR")
def config(path, edit):
    """Show or edit configuration."""
    meme_dir = Path(path).resolve()
    state_dir = get_state_dir(meme_dir)

    if edit:
        import os
        editor = os.environ.get("EDITOR", "nano")
        config_path = state_dir / CONFIG_FILENAME
        if config_path.exists():
            os.execlp(editor, editor, str(config_path))
        else:
            console.print(f"No config found. Run [bold]meme-sorter init {path}[/bold] first.")
        return

    app_config = build_app_config(meme_dir)
    console.print(f"[bold]Meme directory:[/bold] {meme_dir}")
    console.print(f"[bold]State directory:[/bold] {state_dir}")
    console.print(f"[bold]Ollama model:[/bold] {app_config.ollama.model}")
    console.print(f"[bold]Ollama endpoint:[/bold] {app_config.ollama.endpoint}")
    console.print(f"[bold]Workers:[/bold] {app_config.processing.workers}")
    console.print(f"[bold]Default category:[/bold] {app_config.default_category}")
    console.print(f"[bold]Categories:[/bold] {len(app_config.categories)}")
    for name in sorted(app_config.categories):
        desc = app_config.categories[name].description
        short = desc[:60] + "..." if len(desc) > 60 else desc
        console.print(f"  {name}: {short}")


# Inline defaults for when the defaults/ directory isn't available
_DEFAULT_CONFIG = """\
[ollama]
endpoint = "http://localhost:11434"
model = "llama3.2-vision"
timeout = 120
temperature = 0.1
max_tokens = 100
retries = 2

[processing]
workers = 3
save_interval = 25
image_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"]
video_extensions = ["mp4", "webm", "mov", "mkv", "avi"]

[video]
num_frames = 4
thumbnail_size = 512
"""

_DEFAULT_CATEGORIES = """\
default_category = "Shitpost"
non_meme_folder = "# Not Memes"

[prompt_rules]
preamble = \"\"\"
Look at this image and categorize it.

This image is from someone's meme collection. Almost everything here IS a meme.
Be very generous about what counts as a meme -- if it's funny, ironic, absurd,
has text overlay, is a screenshot of social media, a reaction image, an edited image,
a comic, fan art used humorously, or anything that looks like it was saved from the
internet for entertainment, it IS a meme.

Only mark as NOT a meme if it's clearly a personal photo, a plain unedited photograph,
a desktop wallpaper, or a purely informational document with no humor.

When in doubt, it IS a meme.
\"\"\"

[categories.Shitpost]
description = "LAST RESORT only. Use this ONLY if the meme does not fit ANY of the other categories. Most memes should go in a specific category, not here."
priority = 0

[categories."Reaction Image"]
description = "Images primarily used as reactions in conversations. Expressive faces, reaction pics, 'mood' images."
priority = 3

[categories.Anime]
description = "Anime-related memes that are NOT specifically Evangelion, Lain, or Miku."
priority = 5

[categories.Gaming]
description = "Video game memes, gaming culture, game screenshots used as memes."
priority = 5

[categories.Political]
description = "Political memes, political commentary, political figures."
priority = 5

[categories.Animals]
description = "Animal memes, cute animals, funny animal photos."
priority = 5

[categories.Cursed]
description = "ONLY genuinely unsettling, uncomfortable, or disturbing images. Uncanny valley, 'why does this exist' energy. Typically NO text or very minimal text. Weird but funny images with text are Shitpost, not Cursed."
priority = 5

[categories."Twitter or Social Media Screenshot"]
description = "Screenshots of tweets, tumblr posts, reddit posts, facebook posts, instagram posts, or other social media platforms (NOT 4chan)."
priority = 5

[categories."Tech and Programming"]
description = "Programming memes, tech humor, code screenshots, IT memes."
priority = 5

[categories.Wholesome]
description = "Wholesome, heartwarming, or feel-good memes."
priority = 5

[categories.Food]
description = "Food memes, cooking memes, food culture."
priority = 5

[categories."Film and TV"]
description = "Movie and TV show memes, scenes used as memes."
priority = 5

[categories.Music]
description = "Music memes, musician memes, album cover edits."
priority = 5

[categories.Sports]
description = "Sports memes, athlete memes, sports culture."
priority = 5

[categories.Military]
description = "Military memes, military humor, war memes."
priority = 5

[categories.History]
description = "History memes, historical events, historical figures."
priority = 5

[categories.Fashion]
description = "Fashion memes, outfit memes, clothing culture."
priority = 5

[categories."Science and Space"]
description = "Science memes, space memes, physics jokes."
priority = 5

[categories.Discord]
description = "Screenshots of Discord messages, Discord memes, Discord UI."
priority = 7

[categories.NSFW]
description = "The image itself contains pornographic visuals: visible nudity, hentai, explicit sexual imagery. Judge ONLY by what is visually depicted, NOT by text labels. Violence/gore do NOT count."
priority = 100
"""
