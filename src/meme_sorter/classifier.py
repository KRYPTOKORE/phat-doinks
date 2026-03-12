"""Vision model interaction for image classification."""

import base64
import json
import mimetypes
import re
import time
from pathlib import Path

import requests

from meme_sorter.models import AppConfig, ClassificationResult
from meme_sorter.media import encode_image


def _extract_json(text: str) -> dict | None:
    """Try multiple strategies to extract JSON from model output."""
    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:  # odd-indexed parts are inside fences
            inner = part.strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                continue

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find JSON object anywhere in text
    match = re.search(r'\{[^{}]*"category"\s*:\s*"[^"]+"\s*\}', text, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    match = re.search(r'\{[^{}]*"is_meme"\s*:\s*(true|false)[^{}]*\}', text, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Extract from markdown bold/plain text patterns
    text_lower = text.lower()
    is_meme = None
    category = None

    if "is_meme" in text_lower or "is meme" in text_lower:
        if any(x in text_lower for x in ("false", "no", "not a meme")):
            is_meme = False
        elif any(x in text_lower for x in ("true", "yes")):
            is_meme = True

    cat_match = re.search(r'["\*]*category["\*]*\s*[:=]\s*["\*]*([^"\n\*,}]+)', text, re.IGNORECASE)
    if cat_match:
        category = cat_match.group(1).strip()

    if is_meme is not None and category:
        return {"is_meme": is_meme, "category": category}

    return None


def _sanitize_filename(name: str) -> str | None:
    """Clean up a model-generated filename."""
    if not name:
        return None
    # Strip extension if the model added one
    for ext in (".jpg", ".png", ".gif", ".mp4", ".webm", ".jpeg", ".webp"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
    # Replace spaces/hyphens with underscores, strip non-alphanumeric
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    return name[:60] if name else None


def _resolve_category(result: dict, config: AppConfig) -> ClassificationResult:
    """Common logic to resolve a parsed JSON result into a ClassificationResult."""
    category = result.get("category", config.default_category)
    filename = _sanitize_filename(result.get("filename", ""))

    # Handle "Not a Meme" as a category
    not_meme_variants = {"not a meme", "not meme", "none", "n/a"}
    is_meme = category.lower().strip() not in not_meme_variants
    if not is_meme:
        return ClassificationResult(is_meme=False, category="Not a Meme", filename=filename)

    # Also support legacy is_meme field
    if "is_meme" in result and not result["is_meme"]:
        return ClassificationResult(is_meme=False, category="Not a Meme", filename=filename)

    # Validate category
    category_names = set(config.categories.keys())
    if category not in category_names:
        cat_lower = {c.lower(): c for c in category_names}
        if category.lower() in cat_lower:
            category = cat_lower[category.lower()]
        else:
            category = config.default_category

    return ClassificationResult(is_meme=is_meme, category=category, filename=filename)


def _classify_ollama(
    image_path: Path,
    prompt: str,
    config: AppConfig,
) -> ClassificationResult:
    """Send an image to Ollama and get a classification result."""
    video_exts = {"." + e for e in config.processing.video_extensions}

    try:
        b64 = encode_image(image_path, video_exts, config.video)
    except Exception as e:
        return ClassificationResult(is_meme=True, category=config.default_category, error=f"Can't read file: {e}")

    url = f"{config.ollama.endpoint}/api/generate"
    payload = {
        "model": config.ollama.model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": config.ollama.temperature,
            "num_predict": config.ollama.max_tokens,
        },
    }

    for attempt in range(config.ollama.retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=config.ollama.timeout)
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()

            result = _extract_json(text)
            if result is None:
                if attempt < config.ollama.retries:
                    continue
                return ClassificationResult(
                    is_meme=True,
                    category=config.default_category,
                    error=f"JSON parse failed: {text[:100]}",
                )

            return _resolve_category(result, config)

        except requests.exceptions.Timeout:
            if attempt < config.ollama.retries:
                continue
            return ClassificationResult(
                is_meme=True,
                category=config.default_category,
                error="Timeout",
            )

        except Exception as e:
            if attempt < config.ollama.retries:
                time.sleep(1)
                continue
            return ClassificationResult(
                is_meme=True,
                category=config.default_category,
                error=str(e),
            )


def _detect_b64_media_type(b64: str) -> str:
    """Detect media type from the first few bytes of base64-encoded data."""
    # Decode just enough to check magic bytes (16 bytes = ~24 base64 chars)
    header = base64.b64decode(b64[:24])
    if header.startswith(b"\x89PNG"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"GIF8"):
        return "image/gif"
    if header.startswith(b"RIFF") and b"WEBP" in header:
        return "image/webp"
    if header.startswith(b"BM"):
        return "image/bmp"
    return "image/png"


def _get_media_type(path: Path) -> str:
    """Get the MIME type by inspecting file magic bytes, with extension fallback."""
    # Check actual file content — Claude API validates content vs declared type
    _SIGNATURES = [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF", "image/webp"),  # RIFF....WEBP
        (b"BM", "image/bmp"),
    ]
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        for sig, mime in _SIGNATURES:
            if header.startswith(sig):
                # Extra check for WEBP (RIFF container)
                if sig == b"RIFF" and b"WEBP" not in header:
                    continue
                return mime
    except OSError:
        pass
    # Fallback to extension
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
    }.get(suffix, "image/png")


def _classify_claude(
    image_path: Path,
    prompt: str,
    config: AppConfig,
) -> ClassificationResult:
    """Send an image to Claude API and get a classification result."""
    try:
        import anthropic
    except ImportError:
        return ClassificationResult(
            is_meme=True,
            category=config.default_category,
            error="anthropic package not installed. Run: pip install anthropic",
        )

    video_exts = {"." + e for e in config.processing.video_extensions}

    try:
        b64 = encode_image(image_path, video_exts, config.video)
    except Exception as e:
        return ClassificationResult(is_meme=True, category=config.default_category, error=f"Can't read file: {e}")

    # Detect media type from the actual encoded data (may differ from file
    # extension if the image was downscaled to JPEG to fit size limits)
    media_type = _detect_b64_media_type(b64)
    # Videos/GIFs get converted to PNG grids
    if image_path.suffix.lower() in video_exts or image_path.suffix.lower() == ".gif":
        media_type = "image/png"

    import os
    api_key = config.claude.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ClassificationResult(
            is_meme=True,
            category=config.default_category,
            error="No API key. Set claude.api_key in config.toml or ANTHROPIC_API_KEY env var.",
        )
    client = anthropic.Anthropic(api_key=api_key)

    for attempt in range(config.claude.retries + 1):
        try:
            message = client.messages.create(
                model=config.claude.model,
                max_tokens=config.claude.max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )

            text = message.content[0].text.strip()

            result = _extract_json(text)
            if result is None:
                if attempt < config.claude.retries:
                    continue
                return ClassificationResult(
                    is_meme=True,
                    category=config.default_category,
                    error=f"JSON parse failed: {text[:100]}",
                )

            return _resolve_category(result, config)

        except Exception as e:
            err_str = str(e)
            # Fatal errors that won't resolve by retrying
            _FATAL_CODES = (401, 403, 429)
            _FATAL_PHRASES = (
                "authentication", "invalid.*api.*key", "permission",
                "insufficient", "credit", "billing", "rate.*limit",
                "exceeded.*quota",
            )
            is_fatal = False
            if hasattr(e, "status_code") and e.status_code in _FATAL_CODES:
                is_fatal = True
            elif any(re.search(p, err_str, re.IGNORECASE) for p in _FATAL_PHRASES):
                is_fatal = True

            if is_fatal:
                return ClassificationResult(
                    is_meme=True,
                    category=config.default_category,
                    error=err_str,
                    fatal=True,
                )

            if attempt < config.claude.retries:
                time.sleep(1)
                continue
            return ClassificationResult(
                is_meme=True,
                category=config.default_category,
                error=err_str,
            )


def classify_image(
    image_path: Path,
    prompt: str,
    config: AppConfig,
) -> ClassificationResult:
    """Classify an image using the configured backend."""
    if config.backend == "claude":
        return _classify_claude(image_path, prompt, config)
    return _classify_ollama(image_path, prompt, config)
