"""Image and video encoding for vision model input."""

import base64
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from meme_sorter.models import VideoConfig


def extract_video_frames(
    path: Path, config: VideoConfig | None = None
) -> bytes | None:
    """Extract multiple frames from a video and stitch into a grid as PNG bytes."""
    if config is None:
        config = VideoConfig()

    num_frames = config.num_frames
    thumb_size = config.thumbnail_size

    # Get duration
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(
            json.loads(result.stdout).get("format", {}).get("duration", 5)
        )
    except Exception:
        duration = 5

    # Pick evenly spaced timestamps (skip first/last 10%)
    start = duration * 0.1
    end = duration * 0.9
    if end <= start:
        timestamps = [duration * 0.5]
    else:
        timestamps = [
            start + (end - start) * i / (num_frames - 1)
            for i in range(num_frames)
        ]

    frames = []
    for ts in timestamps:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(ts), "-i", str(path),
                    "-frames:v", "1", tmp_path,
                ],
                capture_output=True, timeout=15,
            )
            img = Image.open(tmp_path).convert("RGB")
            frames.append(img)
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if not frames:
        return None

    # Stitch into a 2x2 grid (or fewer if less frames)
    w, h = frames[0].size
    thumb_w = min(w, thumb_size)
    thumb_h = min(h, thumb_size)
    for i, f in enumerate(frames):
        frames[i] = f.resize((thumb_w, thumb_h))

    cols = 2 if len(frames) >= 2 else 1
    rows = (len(frames) + cols - 1) // cols
    grid = Image.new("RGB", (thumb_w * cols, thumb_h * rows))
    for i, f in enumerate(frames):
        grid.paste(f, ((i % cols) * thumb_w, (i // cols) * thumb_h))

    buf = io.BytesIO()
    grid.save(buf, format="PNG")
    return buf.getvalue()


def _downscale_encode(path: Path, max_bytes: int = 3_750_000) -> str:
    """Downscale an image until it fits within max_bytes for base64 encoding."""
    img = Image.open(path)
    img = img.convert("RGB")
    # Try progressively smaller sizes
    for scale in (0.75, 0.5, 0.35, 0.25):
        w, h = int(img.width * scale), int(img.height * scale)
        resized = img.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=85)
        if buf.tell() <= max_bytes:
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    # Last resort: tiny thumbnail
    resized = img.resize((512, 512), Image.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def encode_image(
    path: Path,
    video_extensions: set[str],
    video_config: VideoConfig | None = None,
) -> str:
    """Encode image/video to base64 string for the vision model."""
    suffix = path.suffix.lower()

    if suffix in video_extensions:
        grid_bytes = extract_video_frames(path, video_config)
        if grid_bytes:
            return base64.b64encode(grid_bytes).decode("utf-8")
        raise ValueError("Could not extract frames from video")

    if suffix == ".gif":
        img = Image.open(path)
        img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    # Check file size — Claude API has a 5MB base64 limit (~3.75MB raw)
    file_size = path.stat().st_size
    if file_size > 3_750_000:
        return _downscale_encode(path)

    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
