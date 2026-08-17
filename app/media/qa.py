"""Real FFprobe technical QA inspector and EBU R128 loudness analyzer."""

import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from typing import List, Optional, Tuple

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

from app.domain.enums import QualityStatus
from app.domain.models import QualityResult
from app.media.models import MediaQAResult, RenderProfile


class MediaQAError(RuntimeError):
    """Raised when technical QA inspection fails to execute."""
    pass


class MediaQAInspector:
    """Performs deterministic technical quality analysis of rendered MP4 files using FFprobe and FFmpeg."""

    def __init__(self, profile: Optional[RenderProfile] = None):
        self.profile = profile or RenderProfile()

    def _resolve_bins(self) -> Tuple[str, str]:
        ffprobe = shutil.which("ffprobe")
        ffmpeg = shutil.which("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise MediaQAError("ffprobe and ffmpeg binaries are required for technical QA inspection.")
        return ffmpeg, ffprobe

    def _measure_real_loudness(self, video_path: Path) -> float:
        """Measure actual integrated loudness in LUFS using FFmpeg loudnorm filter output.
        
        Fail-closed: raises MediaQAError if loudness cannot be measured or parsed from audio.
        Never fabricates or returns hardcoded fallback LUFS values.
        """
        ffmpeg_bin, _ = self._resolve_bins()
        cmd = [
            ffmpeg_bin,
            "-i", str(video_path),
            "-af", "loudnorm=print_format=json",
            "-f", "null",
            "-",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            raise MediaQAError(f"FFmpeg loudnorm loudness analysis failed: {e}") from e

        stderr = res.stderr or ""
        json_match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.DOTALL)
        if not json_match:
            err_snip = stderr[-300:].strip() if stderr else "Empty stderr"
            raise MediaQAError(f"Could not parse 'input_i' from FFmpeg loudnorm output: {err_snip}")

        try:
            data = json.loads(json_match.group(0))
            if "input_i" not in data:
                raise MediaQAError("Missing 'input_i' key in loudnorm JSON output.")
            return float(data["input_i"])
        except Exception as e:
            raise MediaQAError(f"Failed to decode loudnorm JSON: {e}") from e

    def inspect_video(
        self,
        project_id: str,
        video_path: Path,
        expected_narration_hash: str,
        actual_narration_hash: str,
        expected_profile: Optional[RenderProfile] = None,
        tts_input_hash: Optional[str] = None,
        subtitle_source_hash: Optional[str] = None,
        render_input_hash: Optional[str] = None,
    ) -> Tuple[QualityResult, MediaQAResult]:
        """Perform comprehensive technical QA on the final rendered video file."""
        profile = expected_profile or self.profile
        ffmpeg_bin, ffprobe_bin = self._resolve_bins()
        issues: List[str] = []

        # 1. Existence and size sanity
        if not video_path.exists():
            issues.append(f"Rendered file does not exist at {video_path}")
            return (
                QualityResult(
                    id=f"qa-{project_id}",
                    project_id=project_id,
                    status=QualityStatus.FAILED,
                    loudness_lufs=-999.0,
                    duration_seconds=0.0,
                    sync_drift_ms=0.0,
                    issues=issues,
                ),
                MediaQAResult(
                    passed=False,
                    file_path=str(video_path),
                    file_size_bytes=0,
                    video_duration=0.0,
                    audio_duration=0.0,
                    duration_drift=0.0,
                    width=0,
                    height=0,
                    fps=0.0,
                    video_codec="none",
                    audio_codec="none",
                    pixel_format="none",
                    loudness_lufs=-999.0,
                    issues=issues,
                ),
            )

        file_size = video_path.stat().st_size
        if file_size < 1000:
            issues.append(f"File size {file_size} bytes is below minimum threshold (1000 bytes).")

        # 2. Immutable Narration Hash & Provenance Assertions
        if expected_narration_hash != actual_narration_hash:
            issues.append(
                f"Canonical narration hash mismatch! Expected {expected_narration_hash}, but rendered with {actual_narration_hash}."
            )
        if tts_input_hash and tts_input_hash != expected_narration_hash:
            issues.append(f"TTS input hash ({tts_input_hash}) does not match canonical narration ({expected_narration_hash}).")
        if subtitle_source_hash and subtitle_source_hash != expected_narration_hash:
            issues.append(f"Subtitle source hash ({subtitle_source_hash}) does not match canonical narration ({expected_narration_hash}).")
        if render_input_hash and render_input_hash != expected_narration_hash:
            issues.append(f"Render input hash ({render_input_hash}) does not match canonical narration ({expected_narration_hash}).")

        # 3. Probe container & streams with ffprobe
        probe_cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json",
            str(video_path),
        ]
        try:
            res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True, timeout=15)
            data = json.loads(res.stdout)
        except Exception as e:
            issues.append(f"FFprobe inspection failed: {e}")
            data = {"format": {}, "streams": []}

        streams = data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not v_stream:
            issues.append("Missing video stream in MP4 container.")
        if not a_stream:
            issues.append("Missing audio stream in MP4 container.")

        # Inspect video stream properties
        width = int(v_stream.get("width", 0)) if v_stream else 0
        height = int(v_stream.get("height", 0)) if v_stream else 0
        v_codec = str(v_stream.get("codec_name", "unknown")).lower() if v_stream else "none"
        pix_fmt = str(v_stream.get("pix_fmt", "unknown")).lower() if v_stream else "none"

        # Check video codec (must be h264 / libx264)
        if v_stream and v_codec not in ("h264", "libx264"):
            issues.append(f"Invalid video codec '{v_codec}'; expected 'h264' or 'libx264'.")

        # Check pixel format (must match profile.pixel_format, e.g. yuv420p)
        if v_stream and pix_fmt != profile.pixel_format.lower():
            issues.append(f"Invalid pixel format '{pix_fmt}'; expected '{profile.pixel_format}'.")

        # Calculate & check FPS
        fps = 0.0
        if v_stream and "r_frame_rate" in v_stream:
            try:
                num, den = v_stream["r_frame_rate"].split("/")
                fps = float(num) / float(den) if float(den) != 0 else 0.0
            except Exception:
                fps = 0.0

        if v_stream and abs(fps - profile.fps) > profile.min_fps_tolerance:
            issues.append(f"Frame rate mismatch: measured {fps:.2f} fps, expected {profile.fps} fps.")

        # Audio stream properties & codec check (must be aac)
        a_codec = str(a_stream.get("codec_name", "unknown")).lower() if a_stream else "none"
        if a_stream and a_codec not in ("aac",):
            issues.append(f"Invalid audio codec '{a_codec}'; expected 'aac'.")

        # Duration inspection
        format_info = data.get("format", {})
        container_duration = float(format_info.get("duration", 0.0))
        v_duration = float(v_stream.get("duration", container_duration)) if v_stream else container_duration
        a_duration = float(a_stream.get("duration", container_duration)) if a_stream else 0.0
        duration_drift = abs(v_duration - a_duration) if (v_stream and a_stream) else 999.0
        sync_drift_ms = round(duration_drift * 1000.0, 2)

        # Check resolution
        if width != profile.width or height != profile.height:
            issues.append(f"Resolution mismatch: expected {profile.width}x{profile.height}, got {width}x{height}.")

        # Check duration drift
        if duration_drift > profile.max_duration_drift_seconds:
            issues.append(
                f"Audio-video duration drift ({duration_drift:.3f}s) exceeds maximum tolerance ({profile.max_duration_drift_seconds}s)."
            )

        # Check minimum duration
        if container_duration <= 0.0:
            issues.append(f"Invalid non-positive video duration ({container_duration}s).")

        # 4. Measure Real Loudness LUFS (Fail-Closed)
        measured_loudness = -999.0
        if a_stream and video_path.exists():
            try:
                measured_loudness = self._measure_real_loudness(video_path)
                min_lufs = profile.target_loudness_lufs - profile.loudness_tolerance_lu
                max_lufs = profile.target_loudness_lufs + profile.loudness_tolerance_lu
                if not (min_lufs <= measured_loudness <= max_lufs):
                    issues.append(
                        f"Measured integrated loudness ({measured_loudness:.2f} LUFS) is outside acceptable tolerance range [{min_lufs:.1f}, {max_lufs:.1f}] LUFS."
                    )
            except Exception as e:
                issues.append(f"Loudness measurement failed: {e}")
        else:
            issues.append("Cannot measure loudness: missing audio stream or file not found.")

        qa_passed = len(issues) == 0
        overall_status = QualityStatus.PASSED if qa_passed else QualityStatus.FAILED

        quality_domain = QualityResult(
            id=f"qa-{project_id}",
            project_id=project_id,
            status=overall_status,
            loudness_lufs=round(measured_loudness, 2),
            duration_seconds=round(container_duration, 2),
            sync_drift_ms=sync_drift_ms,
            issues=issues,
        )

        qa_result = MediaQAResult(
            passed=qa_passed,
            file_path=str(video_path),
            file_size_bytes=file_size,
            video_duration=round(v_duration, 2),
            audio_duration=round(a_duration, 2),
            duration_drift=round(duration_drift, 3),
            width=width,
            height=height,
            fps=round(fps, 2),
            video_codec=v_codec,
            audio_codec=a_codec,
            pixel_format=pix_fmt,
            loudness_lufs=round(measured_loudness, 2),
            issues=issues,
        )

        return quality_domain, qa_result
