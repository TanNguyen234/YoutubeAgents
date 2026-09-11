"""Tests for GFlow Media Provider (Google Flow MCP/CLI integration)."""

from datetime import datetime
from pathlib import Path
import tempfile
import pytest
from PIL import Image

from app.domain.enums import AssetType
from app.media.gflow_provider import (
    GFlowBlockerError,
    GFlowError,
    GFlowMediaProvider,
)


def test_gflow_provider_initialization():
    provider = GFlowMediaProvider()
    assert provider.profile == "tanntd-2005"
    assert provider.executable is not None


def test_gflow_capabilities_probe():
    provider = GFlowMediaProvider()
    caps = provider.check_capabilities()
    assert "available" in caps
    assert "credits" in caps
    if caps.get("available"):
        assert caps["credits"] > 0
        assert caps["authenticated"] is True
        assert caps["profile"] == "tanntd-2005"


def test_create_asset_record():
    provider = GFlowMediaProvider()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"dummy_bytes_for_hash")
        tmp_path = f.name

    asset = provider.create_asset_record(
        project_id="proj-gflow-01",
        file_path=tmp_path,
        content_sha256="abc123hash",
        prompt="A high tech SQLite WAL data architecture diagram",
    )

    assert asset.project_id == "proj-gflow-01"
    assert asset.asset_type == AssetType.IMAGE
    assert asset.license_type == "AI_GENERATED_GFLOW"
    assert asset.content_sha256 == "abc123hash"
    assert "SQLite" in asset.source_url


def test_missing_executable_raises_blocker():
    provider = GFlowMediaProvider(executable_path=Path("C:/non_existent/path/gflow.exe"))
    with pytest.raises(GFlowBlockerError):
        provider.generate_image(
            prompt="Test prompt",
            output_path=Path("dummy.png"),
        )
    with pytest.raises(GFlowBlockerError):
        provider.generate_video(
            prompt="Test video prompt",
            output_path=Path("dummy.mp4"),
        )


def test_gflow_create_video_asset_record():
    provider = GFlowMediaProvider()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"dummy_video_bytes")
        tmp_path = f.name

    asset = provider.create_video_asset_record(
        project_id="proj-gflow-video-01",
        file_path=tmp_path,
        content_sha256="video123hash",
        prompt="Cinematic tracking shot of high-tech servers",
        model="omni-flash",
    )

    assert asset.project_id == "proj-gflow-video-01"
    assert asset.asset_type == AssetType.VIDEO_CLIP
    assert asset.license_type == "AI_GENERATED_GFLOW_VEO"
    assert asset.content_sha256 == "video123hash"
    assert "omni-flash" in asset.source_url


def test_gflow_generate_video_t2v_mocked(monkeypatch, tmp_path):
    import subprocess
    provider = GFlowMediaProvider()
    out_video = tmp_path / "test_video.mp4"

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        # Simulate video file creation
        out_video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"A" * 2000)
        class DummyCompleted:
            returncode = 0
            stdout = '{"status": "success"}'
            stderr = ""
        return DummyCompleted()

    monkeypatch.setattr(subprocess, "run", mock_run)

    file_path, sha256, meta = provider.generate_video(
        prompt="Slow dolly-in on neon city street",
        output_path=out_video,
        model="omni-flash",
        aspect="9:16",
        duration=6,
    )

    assert Path(file_path).exists()
    assert len(sha256) == 64
    assert meta["model"] == "omni-flash"
    assert meta["duration"] == 6
    assert meta["aspect"] == "9:16"

    # Verify executed CLI flags
    cmd_str = " ".join(captured_cmds[0])
    assert "video t2v" in cmd_str
    assert "--model omni-flash" in cmd_str
    assert "--aspect 9:16" in cmd_str
    assert "--duration 6" in cmd_str


def test_gflow_generate_video_i2v_mocked(monkeypatch, tmp_path):
    import subprocess
    provider = GFlowMediaProvider()
    out_video = tmp_path / "test_i2v.mp4"
    frame_img = tmp_path / "initial_frame.png"
    frame_img.write_bytes(b"PNG_INITIAL_FRAME_DATA")

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        out_video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"B" * 2000)
        class DummyCompleted:
            returncode = 0
            stdout = '{"status": "success"}'
            stderr = ""
        return DummyCompleted()

    monkeypatch.setattr(subprocess, "run", mock_run)

    file_path, sha256, meta = provider.generate_video(
        prompt="Animate camera pulling back",
        output_path=out_video,
        initial_frame=frame_img,
        model="omni-flash",
        duration=8,
    )

    assert Path(file_path).exists()
    cmd_str = " ".join(captured_cmds[0])
    assert "video i2v" in cmd_str
    assert "--initial-frame" in cmd_str
    assert str(frame_img) in cmd_str
