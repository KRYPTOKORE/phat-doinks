"""Ollama vision model interaction for image classification."""

import json
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


def classify_image(
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

    category_names = set(config.categories.keys())

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

            category = result.get("category", config.default_category)

            # Handle "Not a Meme" as a category
            not_meme_variants = {"not a meme", "not meme", "none", "n/a"}
            is_meme = category.lower().strip() not in not_meme_variants
            if not is_meme:
                return ClassificationResult(is_meme=False, category="Not a Meme")

            # Also support legacy is_meme field
            if "is_meme" in result and not result["is_meme"]:
                return ClassificationResult(is_meme=False, category="Not a Meme")

            # Validate category
            if category not in category_names:
                cat_lower = {c.lower(): c for c in category_names}
                if category.lower() in cat_lower:
                    category = cat_lower[category.lower()]
                else:
                    category = config.default_category

            return ClassificationResult(is_meme=is_meme, category=category)

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
