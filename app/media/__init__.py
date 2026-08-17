"""Media production package for TTS, subtitles, visual composition, and FFmpeg rendering."""

from app.media.capabilities import MediaCapabilities, check_media_capabilities
from app.media.ffmpeg_renderer import FFmpegRenderer, RenderError
from app.media.models import (
    AudioTrack,
    MediaQAResult,
    RenderManifest,
    RenderProfile,
    RenderResult,
    SceneRenderPlan,
    SubtitleCue,
    SubtitleTrack,
    TTSResult,
)
from app.media.pipeline import MediaProductionError, MediaProductionPipeline
from app.media.qa import MediaQAError, MediaQAInspector
from app.media.scene_planner import ScenePlanner, ScenePlanningError
from app.media.subtitles import SubtitleGenerationError, SubtitleGenerator
from app.media.tts import EdgeTTSBackend, TTSBackend, TTSSynthesisError

__all__ = [
    "MediaCapabilities",
    "check_media_capabilities",
    "RenderProfile",
    "AudioTrack",
    "SubtitleCue",
    "SubtitleTrack",
    "SceneRenderPlan",
    "TTSResult",
    "MediaQAResult",
    "RenderResult",
    "RenderManifest",
    "TTSBackend",
    "EdgeTTSBackend",
    "TTSSynthesisError",
    "SubtitleGenerator",
    "SubtitleGenerationError",
    "ScenePlanner",
    "ScenePlanningError",
    "FFmpegRenderer",
    "RenderError",
    "MediaQAInspector",
    "MediaQAError",
    "MediaProductionPipeline",
    "MediaProductionError",
]
