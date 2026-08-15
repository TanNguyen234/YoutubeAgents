"""Domain models representing the core entities of YouTube Autopilot."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.domain.enums import (
    AssetType,
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


class TopicCandidate(BaseModel):
    """Represents a candidate topic evaluated during Research and Topic Selection."""

    id: str = Field(description="Unique topic ID")
    channel_id: str = Field(description="Associated channel ID")
    keyword: str = Field(description="Main topic keyword/phrase")
    opportunity_score: float = Field(ge=0.0, le=10.0, description="Search volume / opportunity score (0-10)")
    authority_score: float = Field(ge=0.0, le=10.0, description="Niche authority alignment score (0-10)")
    estimated_cpm: Optional[float] = Field(default=None, ge=0.0, description="Estimated category CPM in USD")
    rationale: Optional[str] = Field(default=None, description="Topic selection reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchSource(BaseModel):
    """Verified source document or citation supporting claims."""

    id: str = Field(description="Unique source ID")
    url: str = Field(description="Source URL / URI")
    title: str = Field(description="Document / article title")
    authors: List[str] = Field(default_factory=list, description="Authors or publisher")
    content_sha256: str = Field(description="SHA-256 hash of fetched source text")
    license_type: str = Field(default="UNKNOWN", description="Usage terms")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Claim(BaseModel):
    """A factual statement extracted and verified against research sources."""

    id: str = Field(description="Unique claim ID")
    source_id: str = Field(description="Referenced source ID")
    statement: str = Field(description="Factual claim text")
    verified: bool = Field(default=False, description="Verification status")
    confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Verification confidence")
    notes: Optional[str] = Field(default=None, description="Fact checker annotations")


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

    index: int = Field(description="0-indexed scene sequence number")
    hook: Optional[str] = Field(default=None, description="Scene visual/verbal hook")
    narration: str = Field(description="Spoken narration text")
    target_duration_seconds: float = Field(ge=0.5, description="Target duration in seconds")
    visual_prompt: str = Field(description="Visual planning prompt for asset matching")
    transition: str = Field(default="fade", description="Scene transition effect")


class Script(BaseModel):
    """Structured video script containing narrative scenes and metadata."""

    id: str = Field(description="Unique script ID")
    title: str = Field(description="Video working title")
    hook: str = Field(description="Opening hook line")
    scenes: List[Scene] = Field(default_factory=list, description="Ordered scene sequence")
    total_word_count: int = Field(ge=1, description="Total word count")
    estimated_duration_seconds: float = Field(ge=1.0, description="Estimated total runtime")
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
