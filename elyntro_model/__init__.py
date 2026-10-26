"""Elyntro's experimental, trainable decoder-only language model."""

from .config import ModelConfig
from .tokenizer import ByteTokenizer


def __getattr__(name):
    # Keep tokenizer/data tooling usable without installing the large PyTorch runtime.
    if name == "ElyntroLanguageModel":
        from .model import ElyntroLanguageModel
        return ElyntroLanguageModel
    raise AttributeError(name)


__all__ = ["ModelConfig", "ElyntroLanguageModel", "ByteTokenizer"]
