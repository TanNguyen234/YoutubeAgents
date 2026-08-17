"""TTS module exposing base protocol and implementations."""

from app.media.tts.base import TTSBackend
from app.media.tts.edge_tts_backend import EdgeTTSBackend, TTSSynthesisError

__all__ = [
    "TTSBackend",
    "EdgeTTSBackend",
    "TTSSynthesisError",
]
