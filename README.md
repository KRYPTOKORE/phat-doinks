# Phat Doinks

Sort your meme collection using local AI vision models.

Phat Doinks uses [Ollama](https://ollama.com) to classify images (and videos) into categories you define, then moves them into folders. Everything runs locally on your machine — no cloud APIs, no data leaves your box.

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** installed and running
- A vision model pulled in Ollama (e.g. `ollama pull llama3.2-vision`)

### GPU Notes

- `llama3.2-vision` (11B) works well but needs ~11GB — if your VRAM is under that it'll split across GPU/CPU automatically
- `gemma3:4b` fits in 8GB VRAM but accuracy suffers with many categories
- `qwen2.5vl` models currently have no CUDA support in Ollama (CPU only)

## Install

```bash
pip install git+https://github.com/KRYPTOKORE/phat-doinks.git
```

For the GUI (recommended):

```bash
pip install "phat-doinks[gui] @ git+https://github.com/KRYPTOKORE/phat-doinks.git"
```

Or clone and install locally:

```bash
git clone https://github.com/KRYPTOKORE/phat-doinks.git
cd phat-doinks
pip install -e ".[gui]"
```

## Quick Start

```bash
# 1. Make sure Ollama is running with a vision model
ollama pull llama3.2-vision

# 2. Initialize your meme folder
phat-doinks init ~/memes

# 3. Launch the GUI
phat-doinks gui ~/memes

# Or sort from the command line
phat-doinks sort ~/memes
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `phat-doinks init [PATH]` | Initialize a meme directory with config files |
| `phat-doinks sort [PATH]` | Classify and move unsorted files into category folders |
| `phat-doinks recheck [PATH]` | Re-classify files already in category folders |
| `phat-doinks status [PATH]` | Show file counts per category |
| `phat-doinks undo [PATH]` | Revert the last sort/recheck run |
| `phat-doinks config [PATH]` | Show or edit configuration |
| `phat-doinks gui [PATH]` | Launch the graphical interface |

### Useful Flags

```bash
# Preview what would happen without moving anything
phat-doinks sort ~/memes --dry-run

# Only process 20 files
phat-doinks sort ~/memes --limit 20

# Resume an interrupted run
phat-doinks sort ~/memes --resume

# Use a different model
phat-doinks sort ~/memes --model gemma3:4b

# Recheck only one category folder
phat-doinks recheck ~/memes --folder Gaming

# Undo a specific run
phat-doinks undo ~/memes --run-id <id>
```

## Configuration

After `init`, your meme directory gets a `.meme-sorter/` folder with two config files:

### config.toml

```toml
[ollama]
endpoint = "http://localhost:11434"
model = "llama3.2-vision"
timeout = 120
temperature = 0.1
max_tokens = 100
retries = 2

[processing]
workers = 3
image_extensions = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"]
video_extensions = ["mp4", "webm", "mov", "mkv", "avi"]
```

### categories.toml

Define your own categories — each one gets a name, description (used in the AI prompt), and priority:

```toml
default_category = "Shitpost"
non_meme_folder = "# Not Memes"

[prompt_rules]
preamble = """
Your custom instructions for the model go here.
"""

[categories.Animals]
description = "Any meme featuring animals: cats, dogs, birds, etc."
priority = 5

[categories.Gaming]
description = "Video game memes, game screenshots, gaming culture."
priority = 5

[categories.NSFW]
description = "Explicit sexual imagery, nudity, hentai."
priority = 100
```

Higher priority numbers are checked first. The `default_category` is the fallback when nothing else fits. Non-memes (personal photos, wallpapers, etc.) go into the `non_meme_folder`.

## How It Works

1. Dumps unsorted images from your meme folder root
2. Sends each to Ollama with a prompt built from your categories
3. Model returns `{"category": "Category Name"}` as JSON
4. Files get moved into the matching folder
5. Everything is tracked in a local SQLite database so you can undo or resume

Videos are handled by extracting frames, stitching them into a grid, and sending that to the model.

## The GUI

The GUI gives you:
- Live image preview as files are classified
- Model selection dropdown (auto-detects models from Ollama)
- Start/stop controls, dry-run toggle, file limit
- Progress bar with speed and ETA
- Log panel showing every classification decision
- Settings dialog for all config options

## Tips

- **Start with a dry run** to see how the model classifies things before committing
- **Fewer categories = better accuracy** — 15-20 is a sweet spot for 11B models
- **Write specific category descriptions** — the model sees them directly in the prompt, vague descriptions produce vague results
- **Use `recheck`** after tuning categories to re-sort files that may have been miscategorized
- **`undo` is your friend** — every run can be fully reverted

## License

MIT
