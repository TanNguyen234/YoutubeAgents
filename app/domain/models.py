"""Domain models representing the core entities of YouTube Autopilot."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from app.domain.enums import (
    AssetType,
    ClaimVerificationVerdict,
    ExperimentStatus,
    PlatformFormat,
    PrivacyStatus,
    PublicationStatus,
    QualityStatus,
    VideoLifecycleState,
)


class Channel(BaseModel):
    """Represents a YouTube channel and its target niche metadata."""

    id: str = Field(description="Unique channel identifier (e.g. chan-001)")
    title: str = Field(description="Channel display name")
    handle: str = Field(description="YouTube handle (e.g. @ChannelHandle)")
    niche: str = Field(description="Primary content niche")
    target_audience: str = Field(description="Audience persona definition")
    default_language: str = Field(default="en", description="Default content language code")
    is_active: bool = Field(default=True, description="Channel active status")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TopicScoreBreakdown(BaseModel):
    """Multi-criteria scoring breakdown for candidate topics."""

    demand: float = Field(ge=0.0, le=10.0, description="Search volume / audience appetite (0-10)")
    freshness: float = Field(ge=0.0, le=10.0, description="Timeliness / trend momentum (0-10)")
    competition: float = Field(ge=0.0, le=10.0, description="Market saturation / opportunity score (0-10)")
    channel_fit: float = Field(ge=0.0, le=10.0, description="Niche persona alignment (0-10)")
    originality: float = Field(ge=0.0, le=10.0, description="Unique angle / novelty (0-10)")
    evidence_quality: float = Field(ge=0.0, le=10.0, description="Availability of verifiable source citations (0-10)")
    production_feasibility: float = Field(ge=0.0, le=10.0, description="Ease of asset sourcing / rendering (0-10)")
    historical_fit: Optional[float] = Field(default=None, ge=0.0, le=10.0, description="Past topic performance correlation (0-10)")
    composite_score: float = Field(ge=0.0, le=10.0, description="Weighted composite score (0-10)")
    score_reasons: Dict[str, str] = Field(default_factory=dict, description="Detailed rationale per dimension")


class TopicCandidate(BaseModel):
    """Represents a candidate topic evaluated during Research and Topic Selection."""

    id: str = Field(description="Unique topic ID")
    channel_id: str = Field(description="Associated channel ID")
    keyword: str = Field(description="Main topic keyword/phrase")
    opportunity_score: float = Field(ge=0.0, le=10.0, description="Search volume / opportunity score (0-10)")
    authority_score: float = Field(ge=0.0, le=10.0, description="Niche authority alignment score (0-10)")
    estimated_cpm: Optional[float] = Field(default=None, ge=0.0, description="Estimated category CPM in USD")
    rationale: Optional[str] = Field(default=None, description="Topic selection reasoning")
    score_breakdown: Optional[TopicScoreBreakdown] = Field(default=None, description="Detailed dimension scores")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchSource(BaseModel):
    """Verified source document or citation supporting claims with real provenance."""

    id: str = Field(description="Unique source ID")
    url: str = Field(description="Source URL requested")
    final_url: Optional[str] = Field(default=None, description="Resolved final URL after redirects")
    http_status: Optional[int] = Field(default=None, description="HTTP status code from fetch")
    title: str = Field(description="Document / article title")
    authors: List[str] = Field(default_factory=list, description="Authors or publisher")
    content_sha256: str = Field(description="SHA-256 hash of actual fetched source text")
    content_snapshot: Optional[str] = Field(default=None, description="Raw text snapshot for fact checking")
    content_snapshot_path: Optional[str] = Field(default=None, description="Local path to persisted evidence snapshot")
    license_type: str = Field(default="UNKNOWN", description="Usage terms")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Claim(BaseModel):
    """A factual statement extracted and verified against research sources."""

    id: str = Field(description="Unique claim ID")
    source_id: Optional[str] = Field(default=None, description="Referenced source ID if known")
    statement: str = Field(description="Factual claim text")
    verified: bool = Field(default=False, description="Verification status")
    verdict: ClaimVerificationVerdict = Field(default=ClaimVerificationVerdict.UNVERIFIABLE, description="Resolution verdict")
    confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Verification confidence")
    cited_url: Optional[str] = Field(default=None, description="Real source URL validating claim")
    cited_excerpt: Optional[str] = Field(default=None, description="Verbatim quote/excerpt from source text")
    notes: Optional[str] = Field(default=None, description="Fact checker annotations / reasoning")


class ResearchDossier(BaseModel):
    """Compiled evidence dossier for a chosen topic."""

    id: str = Field(description="Unique dossier ID")
    topic_id: str = Field(description="Associated topic ID")
    sources: List[ResearchSource] = Field(default_factory=list, description="Verified sources")
    claims: List[Claim] = Field(default_factory=list, description="Extracted claims")
    summary: str = Field(description="Executive summary of evidence")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Scene(BaseModel):
    """Individual scene / segment inside a video script."""

    index: int = Field(default=0, description="0-indexed scene sequence number")
    hook: Optional[str] = Field(default=None, description="Scene visual/verbal hook")
    narration: str = Field(description="Spoken narration text")
    target_duration_seconds: float = Field(default=10.0, ge=0.5, description="Target duration in seconds")
    visual_prompt: str = Field(default="Contextual footage depicting topic concept", description="Visual planning prompt for asset matching")
    transition: str = Field(default="fade", description="Scene transition effect")

    @model_validator(mode="before")
    @classmethod
    def normalize_scene_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Handle narration aliases
            if "narration" not in data:
                for k in ("text", "script", "voiceover", "content", "audio", "speech", "dialogue"):
                    if k in data:
                        data["narration"] = data[k]
                        break
                if "narration" not in data:
                    data["narration"] = "Technical explanation segment."
            # Handle duration aliases
            if "target_duration_seconds" not in data:
                if "duration_seconds" in data:
                    data["target_duration_seconds"] = data["duration_seconds"]
                elif "duration" in data:
                    data["target_duration_seconds"] = data["duration"]
                else:
                    data["target_duration_seconds"] = 10.0
            # Handle visual prompt aliases
            if "visual_prompt" not in data:
                if "visual_description" in data:
                    data["visual_prompt"] = data["visual_description"]
                elif "visual" in data:
                    data["visual_prompt"] = data["visual"]
                elif "on_screen_text" in data:
                    data["visual_prompt"] = data["on_screen_text"]
                else:
                    data["visual_prompt"] = "Motion graphic illustrating technical concepts."
        return data


class ScriptSections(BaseModel):
    """Typed script breakdown with discrete narrative sections."""

    hook: str = Field(description="Opening hook (first 3-5 seconds)")
    intro: str = Field(description="Context setup / problem statement")
    segments: List[Scene] = Field(default_factory=list, description="Ordered scene sequence")
    cta: str = Field(description="Call to action / outro")
    voiceover_text: str = Field(description="Consolidated full spoken narration text")
    estimated_duration: float = Field(default=30.0, ge=1.0, description="Estimated total runtime in seconds")

    @model_validator(mode="before")
    @classmethod
    def normalize_sections_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "estimated_duration" not in data:
                if "duration_seconds" in data:
                    data["estimated_duration"] = data["duration_seconds"]
                elif "duration" in data:
                    data["estimated_duration"] = data["duration"]
        return data


class Script(BaseModel):
    """Structured video script containing narrative scenes and metadata."""

    id: str = Field(description="Unique script ID")
    title: str = Field(description="Video working title")
    hook: str = Field(description="Opening hook line")
    scenes: List[Scene] = Field(default_factory=list, description="Ordered scene sequence")
    total_word_count: int = Field(ge=1, description="Total word count")
    estimated_duration_seconds: float = Field(ge=1.0, description="Estimated total runtime")
    sections: Optional[ScriptSections] = Field(default=None, description="Typed narrative sections breakdown")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FactCheckReport(BaseModel):
    """Audit report generated by FactChecker validating all script claims."""

    id: str = Field(description="Unique audit report ID")
    project_id: str = Field(description="Associated project ID")
    claims: List[Claim] = Field(default_factory=list, description="Evaluated claims")
    verified_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    overall_verdict: QualityStatus = Field(default=QualityStatus.PENDING)
    audit_summary: str = Field(description="Fact checker evaluation summary")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Asset(BaseModel):
    """Media asset used in video composition with verified provenance."""

    id: str = Field(description="Unique asset ID")
    project_id: str = Field(description="Associated video project ID")
    asset_type: AssetType = Field(description="Type of media asset")
    file_path: str = Field(description="Local file system path")
    source_url: str = Field(description="Provenance source URL")
    license_type: str = Field(description="Asset license rights")
    content_sha256: str = Field(description="SHA-256 hash of asset file")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QualityResult(BaseModel):
    """Automated QA diagnostic metrics for a rendered video."""

    id: str = Field(description="Unique QA result ID")
    project_id: str = Field(description="Associated project ID")
    status: QualityStatus = Field(default=QualityStatus.PENDING, description="QA verdict (defaults safely to PENDING)")
    loudness_lufs: float = Field(description="Integrated loudness in LUFS (-14 standard)")
    duration_seconds: float = Field(description="Actual final video duration")
    sync_drift_ms: float = Field(default=0.0, description="Audio-video sync drift in ms")
    issues: List[str] = Field(default_factory=list, description="Detected warnings or errors")
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PublicationJob(BaseModel):
    """YouTube publication and scheduling task."""

    id: str = Field(description="Unique publication job ID")
    project_id: str = Field(description="Associated project ID")
    channel_id: str = Field(description="Target channel ID")
    status: PublicationStatus = Field(default=PublicationStatus.PENDING)
    privacy_status: PrivacyStatus = Field(default=PrivacyStatus.PRIVATE, description="Default private upload")
    scheduled_publish_time: Optional[datetime] = Field(default=None)
    youtube_video_id: Optional[str] = Field(default=None)
    published_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalyticsSnapshot(BaseModel):
    """Performance metrics captured from YouTube Analytics API."""

    id: str = Field(description="Unique snapshot ID")
    project_id: str = Field(description="Associated project ID")
    youtube_video_id: str = Field(description="YouTube video ID")
    views: int = Field(default=0, ge=0)
    watch_time_hours: float = Field(default=0.0, ge=0.0)
    ctr_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    average_view_duration_seconds: float = Field(default=0.0, ge=0.0)
    retention_at_3s_percent: Optional[float] = Field(default=None)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Experiment(BaseModel):
    """A/B and strategy tuning experiment."""

    id: str = Field(description="Unique experiment ID")
    project_id: str = Field(description="Associated project ID")
    hypothesis: str = Field(description="Experiment hypothesis")
    variant_details: Dict[str, Any] = Field(default_factory=dict)
    status: ExperimentStatus = Field(default=ExperimentStatus.DRAFT)
    result_summary: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VideoProject(BaseModel):
    """Central entity managing the lifecycle of an autonomous video from inception to publication."""

    id: str = Field(description="Unique project ID (e.g. proj-001)")
    channel_id: str = Field(description="Associated channel ID")
    title: str = Field(description="Video project title")
    format: PlatformFormat = Field(default=PlatformFormat.SHORTS_9_16)
    state: VideoLifecycleState = Field(default=VideoLifecycleState.CREATED)
    script: Optional[Script] = Field(default=None)
    assets: List[Asset] = Field(default_factory=list)
    quality: Optional[QualityResult] = Field(default=None)
    metadata_tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
