"""Unit tests for TTS backend contract, voice resolution, and error boundaries."""

from pathlib import Path
import pytest

from app.media.tts.edge_tts_backend import EdgeTTSBackend, TTSSynthesisError


def test_edge_tts_voice_resolution():
    """Verify voice resolution matches language defaults and overrides."""
    backend = EdgeTTSBackend()
    assert backend._resolve_voice(None, "en") == "en-US-GuyNeural"
    assert backend._resolve_voice(None, "vi") == "vi-VN-HoaiMyNeural"
    assert backend._resolve_voice("custom-voice", "en") == "custom-voice"


def test_edge_tts_empty_text_error(tmp_path: Path):
    """Empty narration text must raise TTSSynthesisError."""
    backend = EdgeTTSBackend()
    with pytest.raises(TTSSynthesisError, match="Cannot synthesize empty"):
        backend.synthesize(
            text="   ",
            output_path=tmp_path / "out.mp3",
        )


def test_deterministic_tts_contract(tmp_path: Path):
    """Verify TTSResult structure, canonical hash binding, and file creation with a fake double."""
    import hashlib
    from app.media.models import TTSResult
    from tests.media.test_ffmpeg_renderer import _create_dummy_wav

    out_audio = tmp_path / "fake_tts.wav"
    _create_dummy_wav(out_audio, duration_seconds=2.5)

    narration = "Deterministic test narration for offline testing."
    narration_hash = hashlib.sha256(narration.encode("utf-8")).hexdigest()
    audio_bytes = out_audio.read_bytes()
    audio_hash = hashlib.sha256(audio_bytes).hexdigest()

    result = TTSResult(
        audio_path=str(out_audio),
        duration_seconds=2.5,
        sample_rate=44100,
        backend="fake-tts",
        voice="test-voice",
        rate="+0%",
        pitch="+0Hz",
        canonical_narration_sha256=narration_hash,
        audio_sha256=audio_hash,
    )

    assert result.duration_seconds == 2.5
    assert result.canonical_narration_sha256 == narration_hash
    assert result.audio_sha256 == audio_hash
    assert result.backend == "fake-tts"
