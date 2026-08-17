"""Tests for FFmpeg video composition and error handling."""

import hashlib
from pathlib import Path
import pytest
from PIL import Image

from app.media.ffmpeg_renderer import FFmpegRenderer, RenderError
from app.media.models import RenderProfile, SceneRenderPlan


def _create_dummy_wav(output_path: Path, duration_seconds: float = 2.0):
    """Generate a clean audible 440Hz sine wave WAV file for testing audio filters."""
    import math
    import struct
    import wave

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    frequency = 440.0
    amplitude = 16000  # -6 dB
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


def test_ffmpeg_renderer_composition(tmp_path: Path):
    """Execute real FFmpeg rendering on a short scene plan and assert output MP4."""
    # 1. Create a dummy image card
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(20, 30, 50))
    img.save(img_path)
    img_sha256 = hashlib.sha256(img_path.read_bytes()).hexdigest()

    # 2. Create audio wav
    audio_path = tmp_path / "voice.wav"
    _create_dummy_wav(audio_path, duration_seconds=3.0)

    # 3. Create SceneRenderPlan
    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Short test scene narration.",
        target_duration_seconds=3.0,
        visual_asset_path=str(img_path),
        visual_asset_sha256=img_sha256,
    )

    renderer = FFmpegRenderer()
    out_video = tmp_path / "output.mp4"

    res = renderer.render_video(
        project_id="proj-render-test",
        scene_plans=[plan],
        audio_path=audio_path,
        output_video_path=out_video,
    )

    assert out_video.exists()
    assert out_video.stat().st_size > 1000
    assert res.width == 1080
    assert res.height == 1920
    assert len(res.content_sha256) == 64


def test_ffmpeg_renderer_missing_audio_error(tmp_path: Path):
    """Missing audio path must raise RenderError."""
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(20, 30, 50))
    img.save(img_path)
    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Short test scene narration.",
        target_duration_seconds=3.0,
        visual_asset_path=str(img_path),
        visual_asset_sha256="test_hash",
    )

    renderer = FFmpegRenderer()
    with pytest.raises(RenderError, match="audio track missing"):
        renderer.render_video(
            project_id="proj-err",
            scene_plans=[plan],
            audio_path=tmp_path / "non_existent.mp3",
            output_video_path=tmp_path / "out.mp4",
        )


def test_ffmpeg_renderer_nonzero_exit_raises_render_error(monkeypatch, tmp_path: Path):
    """Non-zero return code from FFmpeg subprocess must raise typed RenderError."""
    import subprocess
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(20, 30, 50))
    img.save(img_path)
    audio_path = tmp_path / "voice.wav"
    _create_dummy_wav(audio_path, duration_seconds=1.0)

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Test",
        target_duration_seconds=1.0,
        visual_asset_path=str(img_path),
        visual_asset_sha256="hash",
    )

    def mock_subprocess_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["ffmpeg"],
            stderr="Simulated FFmpeg fatal error: invalid codec parameter",
        )

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)
    renderer = FFmpegRenderer()

    with pytest.raises(RenderError, match="failed with code 1"):
        renderer.render_video(
            project_id="proj-fail",
            scene_plans=[plan],
            audio_path=audio_path,
            output_video_path=tmp_path / "fail.mp4",
        )
