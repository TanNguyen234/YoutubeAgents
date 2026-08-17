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


def test_media_qa_fails_on_loudness_parse_error(tmp_path: Path, monkeypatch):
    """When FFmpeg loudnorm measurement fails or cannot parse input_i, QA must FAIL and never fabricate -14.0."""
    from app.media.qa import MediaQAError

    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake_loud_err.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "2.0", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    def mock_measure_fail(video_path):
        raise MediaQAError("Simulated FFmpeg loudnorm parse crash: corrupt json")

    monkeypatch.setattr("subprocess.run", mock_probe)
    monkeypatch.setattr(inspector, "_measure_real_loudness", mock_measure_fail)

    quality, qa_res = inspector.inspect_video(
        project_id="p-qa-loud-err",
        video_path=fake_video,
        expected_narration_hash="valid",
        actual_narration_hash="valid",
    )

    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert quality.loudness_lufs == -999.0
    assert any("Loudness measurement failed" in issue for issue in quality.issues)


def test_media_qa_fails_on_loudness_outside_tolerance(tmp_path: Path, monkeypatch):
    """When measured loudness is outside the [-15.5, -12.5] LUFS tolerance envelope, QA must FAIL."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake_loud_out.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "2.0", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)
    # Mock measured loudness to -22.5 LUFS (too quiet, outside [-15.5, -12.5])
    monkeypatch.setattr(inspector, "_measure_real_loudness", lambda vp: -22.5)

    quality, qa_res = inspector.inspect_video(
        project_id="p-qa-loud-out",
        video_path=fake_video,
        expected_narration_hash="valid",
        actual_narration_hash="valid",
    )

    assert qa_res.passed is False
    assert quality.status == QualityStatus.FAILED
    assert any("outside acceptable tolerance range" in issue for issue in quality.issues)


def test_media_qa_fails_on_wrong_video_codec(tmp_path: Path, monkeypatch):
    """Video with unexpected codec (e.g. vp9 or hevc when h264 is required) must fail QA."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    import subprocess
    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "vp9", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "2.0", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)
    monkeypatch.setattr(inspector, "_measure_real_loudness", lambda vp: -14.0)

    quality, qa_res = inspector.inspect_video("p-codec", fake_video, "h", "h")
    assert qa_res.passed is False
    assert any("Invalid video codec 'vp9'" in issue for issue in quality.issues)


def test_media_qa_fails_on_wrong_audio_codec(tmp_path: Path, monkeypatch):
    """Audio with unexpected codec (e.g. mp3 or opus when aac is required) must fail QA."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "2.0", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "mp3", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)
    monkeypatch.setattr(inspector, "_measure_real_loudness", lambda vp: -14.0)

    quality, qa_res = inspector.inspect_video("p-acodec", fake_video, "h", "h")
    assert qa_res.passed is False
    assert any("Invalid audio codec 'mp3'" in issue for issue in quality.issues)


def test_media_qa_fails_on_wrong_pixel_format(tmp_path: Path, monkeypatch):
    """Pixel format other than yuv420p (e.g. yuv444p) must fail QA."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "2.0", "pix_fmt": "yuv444p"}, {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)
    monkeypatch.setattr(inspector, "_measure_real_loudness", lambda vp: -14.0)

    quality, qa_res = inspector.inspect_video("p-pixfmt", fake_video, "h", "h")
    assert qa_res.passed is False
    assert any("Invalid pixel format 'yuv444p'" in issue for issue in quality.issues)


def test_media_qa_fails_on_wrong_fps(tmp_path: Path, monkeypatch):
    """FPS outside tolerance (e.g. 15fps when 30fps is expected) must fail QA."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "15/1", "duration": "2.0", "pix_fmt": "yuv420p"}, {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)
    monkeypatch.setattr(inspector, "_measure_real_loudness", lambda vp: -14.0)

    quality, qa_res = inspector.inspect_video("p-fps", fake_video, "h", "h")
    assert qa_res.passed is False
    assert any("Frame rate mismatch" in issue for issue in quality.issues)


def test_media_qa_fails_on_missing_video_stream(tmp_path: Path, monkeypatch):
    """Container without video stream must fail QA."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "audio", "codec_name": "aac", "duration": "2.0"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)
    monkeypatch.setattr(inspector, "_measure_real_loudness", lambda vp: -14.0)

    quality, qa_res = inspector.inspect_video("p-novid", fake_video, "h", "h")
    assert qa_res.passed is False
    assert any("Missing video stream" in issue for issue in quality.issues)


def test_media_qa_fails_on_missing_audio_stream(tmp_path: Path, monkeypatch):
    """Container without audio stream must fail QA."""
    inspector = MediaQAInspector()
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"dummy video bytes exceeding 1000 length" * 30)

    def mock_probe(*args, **kwargs):
        class MockRes:
            stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920, "r_frame_rate": "30/1", "duration": "2.0", "pix_fmt": "yuv420p"}], "format": {"duration": "2.0"}}'
        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_probe)

    quality, qa_res = inspector.inspect_video("p-noaud", fake_video, "h", "h")
    assert qa_res.passed is False
    assert any("Missing audio stream" in issue for issue in quality.issues)
