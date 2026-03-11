"""Shared test fixtures."""

import pytest
from pathlib import Path
import tempfile

from meme_sorter.models import AppConfig, Category, OllamaConfig
from meme_sorter.state import StateStore


@pytest.fixture
def tmp_meme_dir(tmp_path):
    """Create a temporary meme directory with some fake images."""
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    # Create some dummy image files
    for i in range(5):
        (meme_dir / f"meme_{i}.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    return meme_dir


@pytest.fixture
def tmp_state(tmp_path):
    """Create a temporary state store."""
    db_path = tmp_path / ".meme-sorter" / "state.db"
    return StateStore(db_path)


@pytest.fixture
def sample_config():
    """Create a minimal app config for testing."""
    return AppConfig(
        categories={
            "Shitpost": Category(name="Shitpost", description="Default catch-all", priority=0),
            "Gaming": Category(name="Gaming", description="Game memes", priority=5),
            "Anime": Category(name="Anime", description="Anime memes", priority=5),
        },
        default_category="Shitpost",
    )
