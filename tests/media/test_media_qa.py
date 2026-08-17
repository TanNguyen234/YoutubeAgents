"""Tests for FFprobe technical QA inspection and EBU R128 loudness verification."""

from pathlib import Path
import pytest
from PIL import Image

from app.domain.enums import QualityStatus
from app.media.ffmpeg_renderer import FFmpegRenderer
from app.media.models import RenderProfile, SceneRenderPlan
from app.media.qa import MediaQAInspector
from tests.media.test_ffmpeg_renderer import _create_dummy_wav


def test_media_qa_passes_on_valid_video(tmp_path: Path):
    """Real FFmpeg rendered video passes QA and returns accurate metrics."""
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(10, 20, 30))
    img.save(img_path)

    audio_path = tmp_path / "voice.wav"
    _create_dummy_wav(audio_path, duration_seconds=2.0)

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Test narration",
        target_duration_seconds=2.0,
        visual_asset_path=str(img_path),
        visual_asset_sha256="test_hash",
    )

    renderer = FFmpegRenderer()
    out_video = tmp_path / "test_valid.mp4"
    renderer.render_video("p-qa-01", [plan], audio_path, out_video)

    inspector = MediaQAInspector()
    quality, qa_res = inspector.inspect_video(
        project_id="p-qa-01",
        video_path=out_video,
        expected_narration_hash="valid_hash",
        actual_narration_hash="valid_hash",
    )

    assert qa_res.passed is True
    assert quality.status == QualityStatus.PASSED
    assert qa_res.width == 1080
    assert qa_res.height == 1920
    assert qa_res.video_codec in ("h264", "libx264")
    assert qa_res.audio_codec in ("aac",)
    assert qa_res.video_duration > 0.0
    assert len(quality.issues) == 0


def test_media_qa_fails_on_hash_mismatch(tmp_path: Path):
    """Narration hash discrepancy must trigger QA failure."""
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(10, 20, 30))
    img.save(img_path)
    audio_path = tmp_path / "voice.wav"
    _create_dummy_wav(audio_path, duration_seconds=2.0)

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Test",
        target_duration_seconds=2.0,
        visual_asset_path=str(img_path),
        visual_asset_sha256="hash",
    )
    renderer = FFmpegRenderer()
    out_video = tmp_path / "test_hash.mp4"
    renderer.render_video("p-qa-02", [plan], audio_path, out_video)

    inspector = MediaQAInspector()
    quality, qa_res = inspector.inspect_video(
        project_id="p-qa-02",
        video_path=out_video,
        expected_narration_hash="expected_hash_123",
        actual_narration_hash="tampered_hash_456",
    )

    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert any("hash mismatch" in issue.lower() for issue in quality.issues)


def test_media_qa_fails_on_missing_file(tmp_path: Path):
    """Non-existent video file must fail QA cleanly."""
    inspector = MediaQAInspector()
    quality, qa_res = inspector.inspect_video(
        project_id="p-missing",
        video_path=tmp_path / "does_not_exist.mp4",
        expected_narration_hash="hash",
        actual_narration_hash="hash",
    )
    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert any("does not exist" in issue for issue in quality.issues)


def test_media_qa_fails_on_empty_zero_byte_video(tmp_path: Path):
    """Empty or zero-byte file must fail QA."""
    zero_file = tmp_path / "zero.mp4"
    zero_file.write_bytes(b"")
    inspector = MediaQAInspector()
    quality, qa_res = inspector.inspect_video(
        project_id="p-zero",
        video_path=zero_file,
        expected_narration_hash="hash",
        actual_narration_hash="hash",
    )
    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert any("minimum threshold" in issue for issue in quality.issues)


def test_media_qa_fails_on_wrong_resolution(tmp_path: Path):
    """Rendered video with unexpected resolution must fail QA."""
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(10, 20, 30))
    img.save(img_path)
    audio_path = tmp_path / "voice.wav"
    _create_dummy_wav(audio_path, duration_seconds=1.5)

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Test",
        target_duration_seconds=1.5,
        visual_asset_path=str(img_path),
        visual_asset_sha256="hash",
    )
    renderer = FFmpegRenderer()
    out_video = tmp_path / "test_res.mp4"
    renderer.render_video("p-qa-res", [plan], audio_path, out_video)

    # Inspect with profile expecting 720x1280
    custom_profile = RenderProfile(width=720, height=1280)
    inspector = MediaQAInspector()
    quality, qa_res = inspector.inspect_video(
        project_id="p-qa-res",
        video_path=out_video,
        expected_narration_hash="valid",
        actual_narration_hash="valid",
        expected_profile=custom_profile,
    )
    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert any("Resolution mismatch" in issue for issue in quality.issues)


def test_media_qa_fails_on_provenance_hash_mismatch(tmp_path: Path):
    """Discrepancy between TTS/subtitle/render provenance hashes must fail QA."""
    img_path = tmp_path / "card.png"
    img = Image.new("RGB", (1080, 1920), color=(10, 20, 30))
    img.save(img_path)
    audio_path = tmp_path / "voice.wav"
    _create_dummy_wav(audio_path, duration_seconds=1.5)

    plan = SceneRenderPlan(
        scene_index=0,
        narration_segment="Test",
        target_duration_seconds=1.5,
        visual_asset_path=str(img_path),
        visual_asset_sha256="hash",
    )
    renderer = FFmpegRenderer()
    out_video = tmp_path / "test_prov.mp4"
    renderer.render_video("p-qa-prov", [plan], audio_path, out_video)

    inspector = MediaQAInspector()
    quality, qa_res = inspector.inspect_video(
        project_id="p-qa-prov",
        video_path=out_video,
        expected_narration_hash="canonical_hash_abc",
        actual_narration_hash="canonical_hash_abc",
        tts_input_hash="tampered_tts_hash_xyz",
    )
    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert any("TTS input hash" in issue for issue in quality.issues)
