"""Live external integration tests for real EdgeTTS backend.

Marked with @pytest.mark.live to exclude from deterministic test suite.
"""

from pathlib import Path
import pytest

from app.media.tts.edge_tts_backend import EdgeTTSBackend


@pytest.mark.live
def test_edge_tts_real_live_synthesis(tmp_path: Path):
    """Perform a short real EdgeTTS synthesis against live Microsoft service."""
    backend = EdgeTTSBackend()
    out_audio = tmp_path / "tts_live_sample.mp3"

    res = backend.synthesize(
        text="SQLite Write Ahead Logging enables fast concurrent database operations.",
        output_path=out_audio,
        voice="en-US-GuyNeural",
        rate="+0%",
        pitch="+0Hz",
    )

    assert out_audio.exists()
    assert out_audio.stat().st_size > 1000
    assert res.duration_seconds > 0.0
    assert len(res.audio_sha256) == 64
    assert res.backend == "edge-tts"
