"""Multi-Agent Framework: pluggable multi-agent simulation framework."""

from .memory_compressor import LLMMemoryCompressor, MemoryCompressor
from .version import __version__

__all__ = ["MemoryCompressor", "LLMMemoryCompressor", "__version__"]
