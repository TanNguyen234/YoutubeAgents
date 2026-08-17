"""Tests for media capability detection."""

from pathlib import Path
import pytest

from app.media.capabilities import check_media_capabilities, MediaCapabilities


def test_real_media_capabilities_detection(tmp_path: Path):
    """Inspect real system media capabilities."""
    caps = check_media_capabilities(output_dir=tmp_path)
    assert isinstance(caps, MediaCapabilities)
    assert caps.output_writable is True
    assert caps.pillow_available is True
    assert caps.tts_available is True
    assert caps.ffmpeg_available is True
    assert caps.ffprobe_available is True
    assert caps.is_production_ready is True
    assert len(caps.blockers) == 0


def test_missing_binary_reporting(monkeypatch, tmp_path: Path):
    """Simulate missing ffmpeg binary and assert typed blocker reporting."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    caps = check_media_capabilities(output_dir=tmp_path)
    assert caps.ffmpeg_available is False
    assert caps.ffprobe_available is False
    assert caps.is_production_ready is False
    assert any("FFmpeg" in b for b in caps.blockers)
    assert any("FFprobe" in b for b in caps.blockers)
