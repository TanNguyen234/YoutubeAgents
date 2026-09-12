"""Deterministic local fake TTS engine producing valid WAV, known duration, hashes, and timing events with zero network."""

import hashlib
import math
from pathlib import Path
import struct
from typing import Optional
import wave

from app.media.models import TTSResult
from app.media.tts.base import TTSBackend


def create_local_wav(output_path: Path, duration_seconds: float = 3.0, tag: str = "") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    freq_offset = int(hashlib.md5(tag.encode("utf-8")).hexdigest()[:4], 16) % 20
    frequency = 440.0 + freq_offset
    amplitude = 16000
    num_frames = int(sample_rate * duration_seconds)

    frames = bytearray()
    for i in range(num_frames):
        val = int(amplitude * math.sin(2.0 * math.pi * frequency * i / sample_rate))
        frames.extend(struct.pack("<hh", val, val))

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)


class LocalFakeTTSBackend:
    """Deterministic local fake TTS engine producing valid WAV, known duration, hashes, and timing events with zero network."""

    backend_name = "local-fake-tts"
    default_voice = "local-fake-voice"

    def __init__(self, duration_seconds: float = 3.0):
        self.duration = duration_seconds
        self.call_count = 0

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        language: str = "en",
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> TTSResult:
        self.call_count += 1
        resolved_voice = voice or self.default_voice
        actual_output = output_path.with_suffix(".wav") if output_path.suffix.lower() == ".mp3" else output_path
        create_local_wav(actual_output, duration_seconds=self.duration, tag=f"{resolved_voice}|{rate}|{pitch}|{text}")
        content_bytes = actual_output.read_bytes()

        words = text.strip().split()
        timing_events = []
        if words:
            interval = self.duration / len(words)
            for idx, w in enumerate(words):
                timing_events.append({
                    "offset": int(idx * interval * 1000),
                    "duration": int(interval * 1000),
                    "text": w,
                })

        return TTSResult(
            audio_path=str(actual_output),
            duration_seconds=self.duration,
            sample_rate=44100,
            backend=self.backend_name,
            voice=resolved_voice,
            rate=rate,
            pitch=pitch,
            timing_events=timing_events,
            canonical_narration_sha256=hashlib.sha256(text.strip().encode("utf-8")).hexdigest(),
            audio_sha256=hashlib.sha256(content_bytes).hexdigest(),
        )
