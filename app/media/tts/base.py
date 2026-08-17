"""Base interface and protocol definition for text-to-speech backends."""

from pathlib import Path
from typing import Optional, Protocol
from app.media.models import TTSResult


class TTSBackend(Protocol):
    """Protocol defining the TTS synthesis contract."""

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        language: str = "en",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> TTSResult:
        """Synthesize canonical spoken text into an audio file with measured duration and SHA-256."""
        ...
