"""Unit tests for AI music generation and BGM composition."""

import wave
from pathlib import Path
import pytest

from app.media.music_generator import MusicGenerator, MusicGenerationError


def test_music_generator_creates_valid_audio(tmp_path: Path):
    """MusicGenerator must create a valid 44.1kHz stereo audio WAV file with matching duration."""
    generator = MusicGenerator()
    out_wav = tmp_path / "test_bgm.wav"

    duration = 4.5
    res_path, sha256 = generator.generate_track(
        duration_seconds=duration,
        output_path=out_wav,
        mood="anime_lofi",
        bpm=85,
    )

    assert out_wav.exists()
    assert str(out_wav) == res_path
    assert len(sha256) == 64
    assert out_wav.stat().st_size > 10000

    # Verify audio properties using wave module
    with wave.open(str(out_wav), "rb") as wf:
        assert wf.getnchannels() == 2
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 44100
        n_frames = wf.getnframes()
        actual_dur = n_frames / 44100
        assert abs(actual_dur - duration) < 0.1


def test_music_generator_invalid_duration(tmp_path: Path):
    """Zero or negative duration must raise MusicGenerationError."""
    generator = MusicGenerator()
    with pytest.raises(MusicGenerationError, match="must be positive"):
        generator.generate_track(
            duration_seconds=0.0,
            output_path=tmp_path / "invalid.wav",
        )
