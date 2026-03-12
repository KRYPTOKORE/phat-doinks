"""Core sorting orchestrator."""

import hashlib
import shutil
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from meme_sorter.classifier import classify_image
from meme_sorter.config import get_state_dir
from meme_sorter.events import (
    EventBus,
    FileProcessed,
    ProgressUpdate,
    RunComplete,
    RunStarted,
)
from meme_sorter.models import AppConfig
from meme_sorter.prompt import build_prompt
from meme_sorter.state import StateStore


def _get_media_extensions(config: AppConfig) -> set[str]:
    img = {"." + e for e in config.processing.image_extensions}
    vid = {"." + e for e in config.processing.video_extensions}
    return img | vid


def _safe_move(src: Path, dest_dir: Path, new_name: str | None = None) -> Path:
    """Move a file to dest_dir, optionally renaming it. Handles collisions."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix
    stem = new_name if new_name else src.stem
    dest = dest_dir / f"{stem}{ext}"
    if dest.exists() and dest != src:
        h = hashlib.md5(str(src).encode()).hexdigest()[:6]
        dest = dest_dir / f"{stem}_{h}{ext}"
    shutil.move(str(src), str(dest))
    return dest


def collect_unsorted(meme_dir: Path, config: AppConfig) -> list[Path]:
    """Collect unsorted media files from the root meme directory."""
    exts = _get_media_extensions(config)
    return sorted(
        f for f in meme_dir.iterdir()
        if f.is_file() and f.suffix.lower() in exts
    )


def collect_for_recheck(
    meme_dir: Path, config: AppConfig, folder: str | None = None
) -> list[tuple[Path, str]]:
    """Collect files from category folders for rechecking.

    Returns list of (file_path, current_category).
    """
    exts = _get_media_extensions(config)
    folders = [folder] if folder else list(config.categories.keys()) + [config.non_meme_folder]

    files = []
    for cat in folders:
        cat_dir = meme_dir / cat
        if not cat_dir.is_dir():
            continue
        for f in sorted(cat_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in exts:
                files.append((f, cat))
    return files


def run_sort(
    meme_dir: Path,
    config: AppConfig,
    bus: EventBus,
    state: StateStore,
    dry_run: bool = False,
    limit: int = 0,
    resume_run_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Sort unsorted memes from the root directory."""
    images = collect_unsorted(meme_dir, config)
    prompt = build_prompt(config)
    non_meme_dir = meme_dir / config.non_meme_folder

    if resume_run_id:
        run_id = resume_run_id
        already_done = state.get_processed_paths(run_id)
        images = [f for f in images if str(f) not in already_done]
    else:
        run_id = state.new_run("sort")

    if limit > 0:
        images = images[:limit]

    if not dry_run:
        non_meme_dir.mkdir(parents=True, exist_ok=True)
        for cat in config.categories:
            (meme_dir / cat).mkdir(parents=True, exist_ok=True)

    bus.emit(RunStarted(total=len(images), mode="sort"))

    errors = 0
    moved = 0
    start_time = time.time()
    lock = threading.Lock()

    def process_one(img_path: Path):
        return img_path, classify_image(img_path, prompt, config)

    processed = 0
    with ThreadPoolExecutor(max_workers=config.processing.workers) as executor:
        futures = [executor.submit(process_one, img) for img in images]

        for i, future in enumerate(futures):
            # Poll with timeout so we can check stop_event
            while True:
                if stop_event and stop_event.is_set():
                    break
                try:
                    img_path, result = future.result(timeout=2)
                    break
                except TimeoutError:
                    continue

            if stop_event and stop_event.is_set():
                # Cancel remaining futures and exit
                for f in futures[i:]:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                break

            if result.error and result.category == config.default_category:
                errors += 1

            if result.fatal:
                bus.emit(FileProcessed(
                    path=img_path,
                    current_category="(unsorted)",
                    new_category=result.category,
                    is_meme=result.is_meme,
                    moved=False,
                    error=f"FATAL: {result.error}",
                    dest_path=None,
                ))
                # Cancel everything and stop
                for f in futures[i + 1:]:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                break

            if result.is_meme:
                dest_dir = meme_dir / result.category
            else:
                dest_dir = non_meme_dir

            did_move = False
            dest = None
            if not dry_run:
                dest = _safe_move(img_path, dest_dir, result.filename)
                did_move = True
                with lock:
                    state.record_move(
                        run_id, str(img_path), str(dest),
                        result.category, result.is_meme,
                    )

            with lock:
                state.mark_processed(
                    str(img_path), result.category,
                    result.is_meme, config.ollama.model, run_id,
                )
                moved += 1 if did_move else 0
                processed = i + 1

            bus.emit(FileProcessed(
                path=img_path,
                current_category="(unsorted)",
                new_category=result.category,
                is_meme=result.is_meme,
                moved=did_move,
                error=result.error,
                dest_path=dest,
            ))
            bus.emit(ProgressUpdate(
                current=i + 1,
                total=len(images),
                elapsed=time.time() - start_time,
            ))

    duration = time.time() - start_time
    state.finish_run(run_id, processed, moved, errors)
    bus.emit(RunComplete(
        processed=processed, moved=moved, kept=0,
        errors=errors, duration=duration,
    ))


def run_recheck(
    meme_dir: Path,
    config: AppConfig,
    bus: EventBus,
    state: StateStore,
    dry_run: bool = False,
    limit: int = 0,
    folder: str | None = None,
    resume_run_id: str | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Re-classify files already in category folders."""
    images = collect_for_recheck(meme_dir, config, folder)
    prompt = build_prompt(config)
    non_meme_dir = meme_dir / config.non_meme_folder

    if resume_run_id:
        run_id = resume_run_id
        already_done = state.get_processed_paths(run_id)
        images = [(f, c) for f, c in images if str(f) not in already_done]
    else:
        run_id = state.new_run("recheck")

    if limit > 0:
        images = images[:limit]

    if not dry_run:
        for cat in config.categories:
            (meme_dir / cat).mkdir(parents=True, exist_ok=True)

    bus.emit(RunStarted(total=len(images), mode="recheck"))

    errors = 0
    moved = 0
    kept = 0
    start_time = time.time()
    lock = threading.Lock()

    def process_one(item):
        img_path, current_cat = item
        return img_path, current_cat, classify_image(img_path, prompt, config)

    processed = 0
    with ThreadPoolExecutor(max_workers=config.processing.workers) as executor:
        futures = [executor.submit(process_one, item) for item in images]

        for i, future in enumerate(futures):
            # Poll with timeout so we can check stop_event
            while True:
                if stop_event and stop_event.is_set():
                    break
                try:
                    img_path, current_cat, result = future.result(timeout=2)
                    break
                except TimeoutError:
                    continue

            if stop_event and stop_event.is_set():
                for f in futures[i:]:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                break

            if result.error and result.category == config.default_category:
                errors += 1

            if result.fatal:
                bus.emit(FileProcessed(
                    path=img_path,
                    current_category=current_cat,
                    new_category=result.category,
                    is_meme=result.is_meme,
                    moved=False,
                    error=f"FATAL: {result.error}",
                    dest_path=None,
                ))
                for f in futures[i + 1:]:
                    f.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                break

            new_cat = result.category if result.is_meme else config.non_meme_folder
            needs_move = new_cat != current_cat

            dest = None
            if needs_move:
                with lock:
                    moved += 1
                if not dry_run:
                    dest_dir = meme_dir / new_cat if result.is_meme else non_meme_dir
                    dest = _safe_move(img_path, dest_dir, result.filename)
                    with lock:
                        state.record_move(
                            run_id, str(img_path), str(dest),
                            new_cat, result.is_meme,
                        )
            else:
                with lock:
                    kept += 1

            with lock:
                state.mark_processed(
                    str(img_path), new_cat,
                    result.is_meme, config.ollama.model, run_id,
                )
                processed = i + 1

            bus.emit(FileProcessed(
                path=img_path,
                current_category=current_cat,
                new_category=new_cat,
                is_meme=result.is_meme,
                moved=needs_move,
                error=result.error,
                dest_path=dest,
            ))
            bus.emit(ProgressUpdate(
                current=i + 1,
                total=len(images),
                elapsed=time.time() - start_time,
            ))

    duration = time.time() - start_time
    state.finish_run(run_id, processed, moved, errors)
    bus.emit(RunComplete(
        processed=len(images), moved=moved, kept=kept,
        errors=errors, duration=duration,
    ))


def run_undo(state: StateStore, run_id: str | None = None) -> int:
    """Undo moves from a run. Returns number of files reverted."""
    batch = state.get_undo_batch(run_id)
    reverted = 0
    move_ids = []

    for move in batch:
        dest = Path(move["dest_path"])
        src_dir = Path(move["source_path"]).parent

        if dest.exists():
            src_dir.mkdir(parents=True, exist_ok=True)
            target = src_dir / dest.name
            shutil.move(str(dest), str(target))
            reverted += 1

        move_ids.append(move["id"])

    state.mark_undone(move_ids)
    return reverted
