from .base import ModelSource, VersionInfo
from .ollama import OllamaSource
from .huggingface import HuggingFaceSource

SOURCE_REGISTRY: dict[str, type[ModelSource]] = {
    "ollama_registry": OllamaSource,
    "hf_hub": HuggingFaceSource,
}

__all__ = ["ModelSource", "VersionInfo", "OllamaSource", "HuggingFaceSource", "SOURCE_REGISTRY"]
