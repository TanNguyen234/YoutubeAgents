"""Automated Video Director package."""

from app.media.director.beat_decomposer import BeatDecomposer
from app.media.director.director_service import AutoDirectorService, DirectorError
from app.media.director.modality_router import VisualModalityRouter
from app.media.director.models import (
    BeatPurpose,
    ChannelCreativeProfile,
    ContentFormat,
    NarrativeBeat,
    OverlaySpec,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VideoQualityReport,
    VisualEvaluation,
    VisualIntent,
    VisualModality,
)
from app.media.director.profiles import (
    BENCHMARK_ANALYSIS_PROFILE,
    CODE_TUTORIAL_PROFILE,
    EDITORIAL_TECH_PROFILE,
    TECH_DOCUMENTARY_PROFILE,
    get_channel_profile_for_niche,
)
from app.media.director.quality_evaluator import (
    VisualShotEvaluator,
    calculate_narration_duplication,
)
from app.media.director.storyboard_planner import StoryboardPlanner

__all__ = [
    "AutoDirectorService",
    "BeatDecomposer",
    "BeatPurpose",
    "BENCHMARK_ANALYSIS_PROFILE",
    "CODE_TUTORIAL_PROFILE",
    "ChannelCreativeProfile",
    "ContentFormat",
    "DirectorError",
    "EDITORIAL_TECH_PROFILE",
    "NarrativeBeat",
    "OverlaySpec",
    "ShotSpec",
    "ShotTimeline",
    "Storyboard",
    "StoryboardPlanner",
    "TECH_DOCUMENTARY_PROFILE",
    "TimelineShot",
    "VideoQualityReport",
    "VisualEvaluation",
    "VisualIntent",
    "VisualModality",
    "VisualModalityRouter",
    "VisualShotEvaluator",
    "calculate_narration_duplication",
    "get_channel_profile_for_niche",
]
