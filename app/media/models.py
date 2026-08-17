"""Domain models and typed contracts for media production, TTS, subtitles, and FFmpeg rendering."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.domain.enums import PlatformFormat, QualityStatus


class RenderProfile(BaseModel):
    """Configuration profile for video rendering."""

    name: str = Field(default="SHORTS_9_16", description="Profile identifier name")
    format: PlatformFormat = Field(default=PlatformFormat.SHORTS_9_16)
    width: int = Field(default=1080, description="Video frame width in pixels")
    height: int = Field(default=1920, description="Video frame height in pixels")
    fps: int = Field(default=30, description="Frames per second")
    video_codec: str = Field(default="libx264", description="Video encoder codec")
    audio_codec: str = Field(default="aac", description="Audio encoder codec")
    pixel_format: str = Field(default="yuv420p", description="Pixel color format for web player compatibility")
    audio_bitrate: str = Field(default="192k", description="Target audio bitrate")
    audio_sample_rate: int = Field(default=44100, description="Target audio sampling rate in Hz")
    target_loudness_lufs: float = Field(default=-14.0, description="EBU R128 integrated loudness target in LUFS")
    loudness_tolerance_lu: float = Field(default=1.5, description="Allowed deviation in LU from target loudness (acceptable range: -15.5 to -12.5 LUFS)")
    min_fps_tolerance: float = Field(default=0.5, description="Allowed frame rate deviation in fps")
    max_duration_drift_seconds: float = Field(default=0.5, description="Allowed duration mismatch between audio and video")


class AudioTrack(BaseModel):
    """Audio asset resulting from TTS synthesis."""

    file_path: str = Field(description="Local file path to audio file")
    duration_seconds: float = Field(ge=0.0, description="Accurate duration in seconds measured via ffprobe")
    content_sha256: str = Field(description="SHA-256 hash of the audio file")
    sample_rate: Optional[int] = Field(default=None, description="Audio sample rate in Hz")
    channels: int = Field(default=2, description="Audio channel count")


class SubtitleCue(BaseModel):
    """Individual subtitle caption cue."""

    index: int = Field(ge=1, description="1-based subtitle sequence index")
    start_time: float = Field(ge=0.0, description="Start timestamp in seconds")
    end_time: float = Field(ge=0.0, description="End timestamp in seconds")
    text: str = Field(description="Spoken caption text")


class SubtitleTrack(BaseModel):
    """Subtitle track containing sequenced cues and saved file path."""

    file_path: str = Field(description="Local path to .srt or .ass file")
    content_sha256: str = Field(description="SHA-256 hash of subtitle file")
    cue_count: int = Field(ge=0, description="Total number of subtitle cues")
    cues: List[SubtitleCue] = Field(default_factory=list, description="Ordered subtitle cues")


class SceneRenderPlan(BaseModel):
    """Plan connecting a script scene to visual card, audio duration, and subtitle cues."""

    scene_index: int = Field(ge=0, description="0-indexed scene sequence index")
    narration_segment: str = Field(description="Authoritative spoken narration for this scene")
    target_duration_seconds: float = Field(ge=0.5, description="Computed scene duration in seconds")
    visual_asset_path: str = Field(description="Path to background/visual card image")
    visual_asset_sha256: str = Field(description="SHA-256 hash of visual asset")
    subtitle_cues: List[SubtitleCue] = Field(default_factory=list, description="Subtitles occurring in this scene")
    transition: str = Field(default="fade", description="Scene transition effect")


class TTSResult(BaseModel):
    """Result of real text-to-speech synthesis."""

    audio_path: str = Field(description="Path to synthesized audio file")
    duration_seconds: float = Field(ge=0.0, description="Real measured duration from ffprobe")
    sample_rate: Optional[int] = Field(default=None, description="Audio sample rate in Hz")
    backend: str = Field(description="TTS engine used (e.g. edge-tts)")
    voice: str = Field(description="TTS voice identifier")
    rate: str = Field(default="+0%", description="TTS speed rate")
    pitch: str = Field(default="+0Hz", description="TTS pitch")
    timing_events: List[Dict[str, Any]] = Field(default_factory=list, description="Optional word/sentence boundary events")
    canonical_narration_sha256: str = Field(description="SHA-256 of the input canonical narration")
    audio_sha256: str = Field(description="SHA-256 of the output audio file")


class MediaQAResult(BaseModel):
    """Detailed technical QA inspection metrics evaluated from actual rendered MP4."""

    passed: bool = Field(description="Overall technical QA pass verdict")
    file_path: str = Field(description="Path to rendered MP4 video")
    file_size_bytes: int = Field(ge=0, description="File size in bytes")
    video_duration: float = Field(ge=0.0, description="Video stream duration in seconds")
    audio_duration: float = Field(ge=0.0, description="Audio stream duration in seconds")
    duration_drift: float = Field(description="Absolute drift between video and audio duration")
    width: int = Field(ge=0, description="Measured video width")
    height: int = Field(ge=0, description="Measured video height")
    fps: float = Field(ge=0.0, description="Measured video frame rate")
    video_codec: str = Field(description="Detected video codec")
    audio_codec: str = Field(description="Detected audio codec")
    pixel_format: str = Field(default="yuv420p", description="Detected pixel format")
    loudness_lufs: float = Field(description="Measured integrated loudness in LUFS (-14 standard)")
    issues: List[str] = Field(default_factory=list, description="List of detected anomalies or errors")
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RenderResult(BaseModel):
    """Result of authoritative FFmpeg video composition."""

    project_id: str = Field(description="Associated project ID")
    video_path: str = Field(description="Path to final rendered MP4 file")
    content_sha256: str = Field(description="SHA-256 hash of final MP4 file")
    file_size_bytes: int = Field(ge=0, description="File size in bytes")
    duration_seconds: float = Field(ge=0.0, description="Video duration in seconds")
    width: int = Field(default=1080)
    height: int = Field(default=1920)
    fps: int = Field(default=30)
    ffmpeg_command: List[str] = Field(default_factory=list, description="Exact FFmpeg command executed")
    rendered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RenderManifest(BaseModel):
    """Auditable machine-readable manifest of a video render execution."""

    project_id: str = Field(description="Unique project identifier")
    source_commit: Optional[str] = Field(default=None, description="Git commit SHA of source code")
    script_id: str = Field(description="Script ID used for render")
    canonical_narration_sha256: str = Field(description="SHA-256 of immutable canonical narration")
    production_fingerprint: str = Field(description="Deterministic SHA-256 of all production inputs")
    tts_input_sha256: Optional[str] = Field(default=None, description="SHA-256 of text passed to TTS")
    subtitle_source_sha256: Optional[str] = Field(default=None, description="SHA-256 of text passed to subtitle generator")
    render_input_narration_sha256: Optional[str] = Field(default=None, description="SHA-256 of narration used in render")
    render_profile: str = Field(description="Name of render profile used")
    tts_backend: str = Field(description="TTS backend name")
    voice: str = Field(description="TTS voice identifier")
    tts_rate: str = Field(default="+0%")
    tts_pitch: str = Field(default="+0Hz")
    audio_path: str = Field(description="Audio track file path")
    audio_sha256: str = Field(description="Audio track SHA-256")
    audio_duration: float = Field(description="Audio duration in seconds")
    subtitle_path: str = Field(description="Subtitle file path")
    subtitle_sha256: str = Field(description="Subtitle track SHA-256")
    subtitle_format: str = Field(default="srt")
    subtitle_cue_count: int = Field(default=0)
    scene_count: int = Field(description="Number of visual scenes composed")
    visual_assets: List[Dict[str, Any]] = Field(default_factory=list, description="Visual assets metadata")
    ffmpeg_version: Optional[str] = Field(default=None)
    ffprobe_version: Optional[str] = Field(default=None)
    ffmpeg_command: List[str] = Field(default_factory=list)
    final_video_path: str = Field(description="Final MP4 file path")
    final_video_sha256: str = Field(description="Final MP4 SHA-256")
    final_video_size_bytes: int = Field(description="Final MP4 size in bytes")
    video_duration: float = Field(description="Final video duration in seconds")
    video_codec: str = Field(default="h264")
    audio_codec: str = Field(default="aac")
    resolution: str = Field(default="1080x1920")
    fps: float = Field(default=30.0)
    measured_loudness_lufs: float = Field(description="Real measured EBU R128 loudness in LUFS")
    qa_verdict: str = Field(description="PASSED or FAILED")
    qa_issues: List[str] = Field(default_factory=list)
    lifecycle: List[str] = Field(default_factory=lambda: ["VERIFIED", "PRODUCING", "RENDERED", "READY_FOR_REVIEW"])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def compute_production_fingerprint(
    canonical_narration_sha256: str,
    render_profile_name: str,
    tts_backend: str,
    voice: str,
    tts_rate: str = "+0%",
    tts_pitch: str = "+0Hz",
    subtitle_format: str = "srt",
    ordered_scene_asset_hashes: Optional[List[str]] = None,
) -> str:
    """Compute a deterministic SHA-256 fingerprint uniquely identifying a production combination."""
    import hashlib
    raw = (
        f"{canonical_narration_sha256}|{render_profile_name}|{tts_backend}|{voice}|"
        f"{tts_rate}|{tts_pitch}|{subtitle_format}|{','.join(ordered_scene_asset_hashes or [])}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
