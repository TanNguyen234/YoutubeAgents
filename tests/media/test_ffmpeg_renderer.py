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


def test_ffmpeg_renderer_with_bgm_ducking(tmp_path: Path):
    """FFmpegRenderer must mix narration voice and background music with sidechain ducking."""
    img_path = tmp_path / "scene.png"
    img = Image.new("RGB", (1080, 1920), color=(15, 23, 42))
    img.save(img_path)
    img_sha256 = hashlib.sha256(img_path.read_bytes()).hexdigest()

    voice_path = tmp_path / "narration.wav"
    _create_dummy_wav(voice_path, duration_seconds=2.5)

    bgm_path = tmp_path / "music.wav"
    _create_dummy_wav(bgm_path, duration_seconds=4.0)

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Scene with BGM.",
        target_duration_seconds=2.5,
        visual_asset_path=str(img_path),
        visual_asset_sha256=img_sha256,
    )

    renderer = FFmpegRenderer()
    out_video = tmp_path / "output_with_bgm.mp4"

    res = renderer.render_video(
        project_id="proj-bgm-test",
        scene_plans=[plan],
        audio_path=voice_path,
        output_video_path=out_video,
        bgm_path=bgm_path,
    )

    assert out_video.exists()
    assert out_video.stat().st_size > 1000
    assert res.width == 1080
    assert res.height == 1920


def test_ffmpeg_renderer_with_video_input(tmp_path: Path):
    """FFmpegRenderer must support animated motion video files (.mp4) as visual scene assets."""
    # First generate a small base video to use as input asset
    renderer = FFmpegRenderer()
    base_img = tmp_path / "seed.png"
    Image.new("RGB", (1080, 1920), color=(30, 40, 60)).save(base_img)
    base_audio = tmp_path / "base_audio.wav"
    _create_dummy_wav(base_audio, duration_seconds=1.5)

    base_plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Base clip",
        target_duration_seconds=1.5,
        visual_asset_path=str(base_img),
        visual_asset_sha256=hashlib.sha256(base_img.read_bytes()).hexdigest(),
    )
    seed_video = tmp_path / "seed_motion.mp4"
    renderer.render_video("seed-proj", [base_plan], base_audio, seed_video)

    # Now use this seed_motion.mp4 as visual asset for a new scene
    motion_plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Testing motion video asset.",
        target_duration_seconds=2.0,
        visual_asset_path=str(seed_video),
        visual_asset_sha256=hashlib.sha256(seed_video.read_bytes()).hexdigest(),
    )

    out_motion_video = tmp_path / "final_motion_output.mp4"
    test_audio = tmp_path / "test_audio.wav"
    _create_dummy_wav(test_audio, duration_seconds=2.0)

    res = renderer.render_video(
        project_id="proj-motion-test",
        scene_plans=[motion_plan],
        audio_path=test_audio,
        output_video_path=out_motion_video,
    )

    assert out_motion_video.exists()
    assert out_motion_video.stat().st_size > 1000
    assert res.width == 1080
    assert res.height == 1920


def test_escape_ffmpeg_filter_path():
    """Verify that filter paths escape colons and single quotes correctly."""
    from app.media.ffmpeg_renderer import escape_ffmpeg_filter_path

    # Test Windows drive colon and single quotes in directory name
    p = Path("C:/workspace/it's_a_test/subtitles.ass")
    esc = escape_ffmpeg_filter_path(p)
    assert "\\:" in esc
    assert "\\'" in esc
    assert "it\\'s_a_test" in esc


def test_ffmpeg_renderer_with_3stem_and_ass_subtitles(tmp_path: Path):
    """FFmpegRenderer must compose 3 audio stems (Voiceover, BGM, SFX) and burn ASS subtitles."""
    img_path = tmp_path / "card.png"
    Image.new("RGB", (1080, 1920), color=(10, 20, 30)).save(img_path)
    img_sha = hashlib.sha256(img_path.read_bytes()).hexdigest()

    voice_path = tmp_path / "voice.wav"
    _create_dummy_wav(voice_path, duration_seconds=2.0)
    bgm_path = tmp_path / "bgm.wav"
    _create_dummy_wav(bgm_path, duration_seconds=3.0)
    sfx_path = tmp_path / "sfx.wav"
    _create_dummy_wav(sfx_path, duration_seconds=2.0)

    ass_path = tmp_path / "test.ass"
    ass_path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,76,&H0000FFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,1,0,1,6,3,2,60,60,720,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{\\k50}Kinetic {\\k50}Karaoke {\\k100}Shorts\n",
        encoding="utf-8",
    )

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="3-stem mix with ASS test.",
        target_duration_seconds=2.0,
        visual_asset_path=str(img_path),
        visual_asset_sha256=img_sha,
    )

    renderer = FFmpegRenderer()
    out_video = tmp_path / "output_3stem.mp4"

    res = renderer.render_video(
        project_id="proj-3stem-test",
        scene_plans=[plan],
        audio_path=voice_path,
        output_video_path=out_video,
        subtitle_path=ass_path,
        bgm_path=bgm_path,
        sfx_path=sfx_path,
    )

    assert out_video.exists()
    assert out_video.stat().st_size > 1000
    assert res.width == 1080
    assert res.height == 1920
    assert any("sidechaincompress" in " ".join(cmd) or "sidechaincompress" in str(cmd) for cmd in [res.ffmpeg_command])


