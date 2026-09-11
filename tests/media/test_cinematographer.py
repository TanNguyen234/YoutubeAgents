"""Tests for CinematographerService (Ken Burns motion & BGM audio ducking)."""

from pathlib import Path
import pytest

from app.media.cinematographer import CinematographerService


@pytest.fixture
def cinematographer() -> CinematographerService:
    return CinematographerService()


def test_build_ken_burns_zoom_filter(cinematographer: CinematographerService):
    vf_str = cinematographer.build_ken_burns_filter(
        duration_seconds=5.0,
        fps=30,
        width=1080,
        height=1920,
        motion_type="zoom_in",
    )
    assert "zoompan" in vf_str
    assert "fps=30" in vf_str
    assert "s=1080x1920" in vf_str
    # 5.0 * 30 = 150 frames
    assert "d=150" in vf_str


def test_build_ken_burns_pan_filter(cinematographer: CinematographerService):
    vf_str = cinematographer.build_ken_burns_filter(
        duration_seconds=4.0,
        fps=30,
        width=1080,
        height=1920,
        motion_type="pan_left",
    )
    assert "zoompan" in vf_str
    assert "d=120" in vf_str


def test_build_audio_ducking_filter_graph(cinematographer: CinematographerService, tmp_path: Path):
    bgm = tmp_path / "bgm.mp3"
    bgm.touch()
    voice = tmp_path / "voice.wav"
    voice.touch()

    filter_graph = cinematographer.build_ducked_audio_complex_filter(
        voiceover_input_index=0,
        bgm_input_index=1,
        ducking_volume=0.15,  # -16dB
        total_duration_seconds=30.0,
    )
    assert "volume=0.15" in filter_graph
    assert "amix" in filter_graph


def test_generate_synthetic_sfx(cinematographer: CinematographerService, tmp_path: Path):
    whoosh = tmp_path / "whoosh.wav"
    impact = tmp_path / "impact.wav"

    cinematographer.generate_synthetic_whoosh_sfx(whoosh, duration=0.20)
    cinematographer.generate_synthetic_impact_sfx(impact, duration=0.15)

    assert whoosh.exists()
    assert whoosh.stat().st_size > 0
    assert impact.exists()
    assert impact.stat().st_size > 0

