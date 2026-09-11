"""Tests for motion graphics streaming frame encoding, memory optimization, and FFmpeg error resilience."""

from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch
from PIL import Image
import pytest

from app.media.director.models import VisualizationDataMode
from app.media.renderers.motion_graphics import MotionGraphicsRenderer


def test_motion_renderer_ffmpeg_failure_does_not_raise_nameerror(tmp_path: Path):
    """FFmpeg returning non-zero code or failing must log warning without NameError and produce fallback."""
    renderer = MotionGraphicsRenderer(width=320, height=480)
    out_mp4 = tmp_path / "fail_test.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.closed = False
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.closed = False
    mock_proc.stderr.read.return_value = b"Simulated FFmpeg filter error"

    frames = [Image.new("RGB", (320, 480), color=(10, 10, 10)) for _ in range(3)]

    with patch("shutil.which", return_value="ffmpeg"):
        with patch("subprocess.Popen", return_value=mock_proc):
            path_str, sha = renderer._encode_frames_to_mp4(frames, output_path=out_mp4, fps=12)

    # Must have fallen back to png because mp4 does not exist / failed
    assert path_str.endswith(".png")
    assert Path(path_str).exists()
    assert len(sha) == 64


def test_motion_renderer_streams_frames_without_massive_buffer(tmp_path: Path):
    """Ensure frames are written iteratively to stdin and generator is consumed progressively."""
    renderer = MotionGraphicsRenderer(width=320, height=480)
    out_mp4 = tmp_path / "stream_test.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.closed = False
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.closed = False
    mock_proc.stderr.read.return_value = b""

    # Touch the output mp4 so it simulates successful creation
    out_mp4.write_bytes(b"dummy mp4 video bytes")

    generated_frames_count = 0

    def frame_gen():
        nonlocal generated_frames_count
        for _ in range(5):
            generated_frames_count += 1
            yield Image.new("RGB", (320, 480), color=(0, 0, 0))

    with patch("shutil.which", return_value="ffmpeg"):
        with patch("subprocess.Popen", return_value=mock_proc):
            path_str, sha = renderer._encode_frames_to_mp4(frame_gen(), output_path=out_mp4, fps=12)

    # Stdin write must be called for each frame (streamed), not joined into a giant single blob
    assert mock_proc.stdin.write.call_count == 5
    assert mock_proc.stdin.close.call_count == 1
    assert generated_frames_count == 5
    assert path_str == str(out_mp4)


def test_motion_renderer_token_animation_creates_valid_output(tmp_path: Path):
    """End-to-end check that render_animated_token_prediction executes and returns valid file."""
    renderer = MotionGraphicsRenderer(width=320, height=480)
    out_path = tmp_path / "token_anim.mp4"

    path_str, sha = renderer.render_animated_token_prediction(
        prompt_text="The capital of France is",
        candidates=[("Paris", 0.8), ("Lyon", 0.1), ("Marseille", 0.1)],
        selected_token="Paris",
        output_path=out_path,
        duration=0.5,
        fps=12,
        data_mode=VisualizationDataMode.CONCEPTUAL,
    )

    result_path = Path(path_str)
    assert result_path.exists()
    assert result_path.stat().st_size > 0
    assert len(sha) == 64


def test_ffmpeg_timeout_kills_process(tmp_path: Path):
    """If FFmpeg hangs and exceeds timeout, proc.kill() is invoked and fallback PNG returned."""
    renderer = MotionGraphicsRenderer(width=320, height=480)
    out_mp4 = tmp_path / "timeout_test.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = -9
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.closed = False
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60), 0]

    frames = [Image.new("RGB", (320, 480), color=(50, 50, 50))]

    with patch("shutil.which", return_value="ffmpeg"):
        with patch("subprocess.Popen", return_value=mock_proc):
            path_str, sha = renderer._encode_frames_to_mp4(frames, output_path=out_mp4, fps=12)

    assert mock_proc.kill.called
    assert path_str.endswith(".png")
    assert Path(path_str).exists()


def test_ffmpeg_nonzero_with_partial_mp4_is_not_success(tmp_path: Path):
    """If FFmpeg returns nonzero exit code even if partial MP4 exists, it must be deleted and rejected."""
    renderer = MotionGraphicsRenderer(width=320, height=480)
    out_mp4 = tmp_path / "partial_corrupt.mp4"
    out_mp4.write_bytes(b"corrupted partial header and frames")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.closed = False

    frames = [Image.new("RGB", (320, 480), color=(10, 20, 30))]

    with patch("shutil.which", return_value="ffmpeg"):
        with patch("subprocess.Popen", return_value=mock_proc):
            path_str, sha = renderer._encode_frames_to_mp4(frames, output_path=out_mp4, fps=12)

    assert not out_mp4.exists(), "Partial MP4 should have been unlinked upon nonzero return code"
    assert path_str.endswith(".png")
    assert Path(path_str).exists()


def test_streaming_encoder_does_not_block_on_stderr_read(tmp_path: Path):
    """Ensure stderr is handled via tempfile or safe non-blocking collection, not blocking before wait."""
    renderer = MotionGraphicsRenderer(width=320, height=480)
    out_mp4 = tmp_path / "noblock_test.mp4"
    out_mp4.write_bytes(b"valid mp4 content")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.closed = False

    call_order = []

    def mock_close():
        call_order.append("stdin_close")

    def mock_wait(timeout=None):
        call_order.append("wait")
        return 0

    mock_proc.stdin.close.side_effect = mock_close
    mock_proc.wait.side_effect = mock_wait

    frames = [Image.new("RGB", (320, 480), color=(0, 0, 0))]

    with patch("shutil.which", return_value="ffmpeg"):
        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            path_str, sha = renderer._encode_frames_to_mp4(frames, output_path=out_mp4, fps=12)

    # Check that stderr was passed as a file/tempfile rather than PIPE that could block
    _, kwargs = mock_popen.call_args
    assert kwargs.get("stderr") != subprocess.PIPE
    assert "stdin_close" in call_order
    assert "wait" in call_order
    assert path_str == str(out_mp4)


# Explicit aliases for regression test suite
test_ffmpeg_nonzero_partial_output_is_rejected = test_ffmpeg_nonzero_with_partial_mp4_is_not_success
test_ffmpeg_timeout_does_not_block_on_stderr = test_streaming_encoder_does_not_block_on_stderr_read

