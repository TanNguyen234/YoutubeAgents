"""Real EdgeTTS backend implementation for high-quality speech synthesis."""

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Optional

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

from app.media.models import TTSResult


class TTSSynthesisError(RuntimeError):
    """Raised when TTS audio synthesis fails."""
    pass


class EdgeTTSBackend:
    """Real Text-To-Speech backend using Microsoft Edge TTS service."""

    DEFAULT_VOICES = {
        "en": "en-US-GuyNeural",
        "vi": "vi-VN-HoaiMyNeural",
    }

    def __init__(self, default_voice: Optional[str] = None):
        self.default_voice = default_voice

    def _resolve_voice(self, voice: Optional[str], language: str) -> str:
        if voice and voice.strip():
            return voice.strip()
        if self.default_voice:
            return self.default_voice
        return self.DEFAULT_VOICES.get(language.lower(), "en-US-GuyNeural")

    def _measure_audio_with_ffprobe(self, audio_path: Path) -> tuple[float, Optional[int]]:
        """Measure real audio duration and sample rate via ffprobe subprocess."""
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            raise TTSSynthesisError("ffprobe executable not found in PATH to verify synthesized audio duration.")

        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration:stream=sample_rate",
            "-of", "json",
            str(audio_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            data = json.loads(res.stdout)
            duration = float(data.get("format", {}).get("duration", 0.0))
            streams = data.get("streams", [])
            sample_rate = None
            if streams and "sample_rate" in streams[0]:
                sample_rate = int(streams[0]["sample_rate"])
            return duration, sample_rate
        except Exception as e:
            raise TTSSynthesisError(f"ffprobe audio measurement failed on {audio_path}: {e}") from e

    async def _synthesize_async(self, text: str, output_path: Path, voice: str, rate: str = "+0%", pitch: str = "+0Hz") -> None:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(str(output_path))

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        language: str = "en",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> TTSResult:
        """Synthesize canonical spoken narration into an audio file."""
        if not text or not text.strip():
            raise TTSSynthesisError("Cannot synthesize empty or whitespace-only spoken narration.")

        clean_text = text.strip()
        canonical_sha256 = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        voice_to_use = self._resolve_voice(voice, language)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            asyncio.run(self._synthesize_async(clean_text, output_path, voice_to_use, rate=rate, pitch=pitch))
        except Exception as e:
            raise TTSSynthesisError(f"EdgeTTS synthesis failed: {e}") from e

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise TTSSynthesisError(f"Synthesized audio file is missing or zero bytes at {output_path}.")

        audio_bytes = output_path.read_bytes()
        audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()

        duration, sample_rate = self._measure_audio_with_ffprobe(output_path)
        if duration <= 0.0:
            raise TTSSynthesisError(f"Measured audio duration is 0 or negative ({duration}s).")

        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            sample_rate=sample_rate,
            backend="edge-tts",
            voice=voice_to_use,
            rate=rate,
            pitch=pitch,
            canonical_narration_sha256=canonical_sha256,
            audio_sha256=audio_sha256,
        )
