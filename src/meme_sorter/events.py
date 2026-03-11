"""Event bus for decoupling core sorting logic from UI."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any
from collections import defaultdict


@dataclass
class FileProcessed:
    path: Path
    current_category: str
    new_category: str
    is_meme: bool
    moved: bool
    error: str | None = None
    dest_path: Path | None = None


@dataclass
class ProgressUpdate:
    current: int
    total: int
    elapsed: float


@dataclass
class RunStarted:
    total: int
    mode: str  # "sort", "recheck", "rescan"


@dataclass
class RunComplete:
    processed: int
    moved: int
    kept: int
    errors: int
    duration: float
    stats: dict[str, int] = field(default_factory=dict)


Event = FileProcessed | ProgressUpdate | RunStarted | RunComplete


class EventBus:
    def __init__(self):
        self._handlers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> None:
        for handler in self._handlers[type(event)]:
            handler(event)
