"""Data models for meme-sorter."""

from dataclasses import dataclass, field


@dataclass
class Category:
    name: str
    description: str = ""
    priority: int = 5


@dataclass
class OllamaConfig:
    endpoint: str = "http://localhost:11434"
    model: str = "llama3.2-vision"
    timeout: int = 120
    temperature: float = 0.1
    max_tokens: int = 100
    retries: int = 2


@dataclass
class ProcessingConfig:
    workers: int = 3
    save_interval: int = 25
    image_extensions: list[str] = field(
        default_factory=lambda: ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "webp"]
    )
    video_extensions: list[str] = field(
        default_factory=lambda: ["mp4", "webm", "mov", "mkv", "avi"]
    )


@dataclass
class VideoConfig:
    num_frames: int = 4
    thumbnail_size: int = 512


@dataclass
class AppConfig:
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    default_category: str = "Shitpost"
    non_meme_folder: str = "# Not Memes"
    categories: dict[str, Category] = field(default_factory=dict)
    prompt_preamble: str = ""


@dataclass
class ClassificationResult:
    is_meme: bool
    category: str
    error: str | None = None
