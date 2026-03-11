"""Configuration loading and management."""

import tomllib
from pathlib import Path
from importlib import resources

from meme_sorter.models import (
    AppConfig,
    Category,
    OllamaConfig,
    ProcessingConfig,
    VideoConfig,
)

STATE_DIR_NAME = ".meme-sorter"
CONFIG_FILENAME = "config.toml"
CATEGORIES_FILENAME = "categories.toml"


def get_state_dir(meme_dir: Path) -> Path:
    return meme_dir / STATE_DIR_NAME


def get_defaults_dir() -> Path:
    return Path(__file__).parent.parent.parent / "defaults"


def load_categories(path: Path) -> tuple[dict[str, Category], str, str]:
    """Load categories from a TOML file.

    Returns (categories_dict, default_category, preamble).
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)

    categories = {}
    for name, cat_data in data.get("categories", {}).items():
        if isinstance(cat_data, dict):
            categories[name] = Category(
                name=name,
                description=cat_data.get("description", ""),
                priority=cat_data.get("priority", 5),
            )
        else:
            categories[name] = Category(name=name)

    default_category = data.get("default_category", "Shitpost")
    non_meme_folder = data.get("non_meme_folder", "# Not Memes")
    preamble = data.get("prompt_rules", {}).get("preamble", "")

    return categories, default_category, non_meme_folder, preamble


def load_config(path: Path) -> dict:
    """Load config.toml and return raw dict."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def build_app_config(
    meme_dir: Path,
    cli_overrides: dict | None = None,
) -> AppConfig:
    """Build the full app config by merging defaults, local config, and CLI overrides."""
    config = AppConfig()
    state_dir = get_state_dir(meme_dir)

    # Load local config.toml if present
    config_path = state_dir / CONFIG_FILENAME
    if config_path.exists():
        raw = load_config(config_path)

        ollama = raw.get("ollama", {})
        config.ollama = OllamaConfig(
            endpoint=ollama.get("endpoint", config.ollama.endpoint),
            model=ollama.get("model", config.ollama.model),
            timeout=ollama.get("timeout", config.ollama.timeout),
            temperature=ollama.get("temperature", config.ollama.temperature),
            max_tokens=ollama.get("max_tokens", config.ollama.max_tokens),
            retries=ollama.get("retries", config.ollama.retries),
        )

        proc = raw.get("processing", {})
        config.processing = ProcessingConfig(
            workers=proc.get("workers", config.processing.workers),
            save_interval=proc.get("save_interval", config.processing.save_interval),
            image_extensions=proc.get(
                "image_extensions", config.processing.image_extensions
            ),
            video_extensions=proc.get(
                "video_extensions", config.processing.video_extensions
            ),
        )

        vid = raw.get("video", {})
        config.video = VideoConfig(
            num_frames=vid.get("num_frames", config.video.num_frames),
            thumbnail_size=vid.get("thumbnail_size", config.video.thumbnail_size),
        )

    # Load categories
    cat_path = state_dir / CATEGORIES_FILENAME
    if not cat_path.exists():
        cat_path = get_defaults_dir() / CATEGORIES_FILENAME

    if cat_path.exists():
        categories, default_cat, non_meme, preamble = load_categories(cat_path)
        config.categories = categories
        config.default_category = default_cat
        config.non_meme_folder = non_meme
        config.prompt_preamble = preamble

    # Apply CLI overrides
    if cli_overrides:
        if "model" in cli_overrides and cli_overrides["model"]:
            config.ollama.model = cli_overrides["model"]
        if "endpoint" in cli_overrides and cli_overrides["endpoint"]:
            config.ollama.endpoint = cli_overrides["endpoint"]
        if "workers" in cli_overrides and cli_overrides["workers"]:
            config.processing.workers = cli_overrides["workers"]

    return config
