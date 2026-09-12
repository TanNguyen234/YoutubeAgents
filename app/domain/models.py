"""Domain models representing the core entities of YouTube Autopilot."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from app.domain.enums import (
    ApprovalOrigin,
    AssetType,
    ClaimVerificationVerdict,
    ContentFormat,
    EditorialSlotStatus,
    ExperimentStatus,
    HookAngle,
    PlatformFormat,
    PrimaryVideoGoal,
    PrivacyStatus,
    PublicationStatus,
    QualityStatus,
    RetentionCueType,
    ReviewAction,
    TitleVariantType,
    TonePreset,
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
    youtube_category_id: str = Field(default="28", description="YouTube category ID")
    made_for_kids: bool = Field(default=False, description="Whether channel content is made for kids")
    default_tags: List[str] = Field(default_factory=list, description="Default channel tags")
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
                else:
                    data["visual_prompt"] = "Motion graphic illustrating technical concepts."
        return data


# Alias for backward and forward compatibility with director pipeline
ScriptScene = Scene


class VideoCreativeBrief(BaseModel):
    """Creative constraints and narrative tone configuration for scriptwriting."""

    target_duration_seconds: float = Field(default=40.0, ge=5.0, description="Target video runtime in seconds")
    primary_goal: PrimaryVideoGoal = Field(default=PrimaryVideoGoal.WATCH_TIME, description="Primary video objective")
    tone: TonePreset = Field(default=TonePreset.CONVERSATIONAL, description="Audience delivery tone")
    desired_viewer_emotion: Optional[str] = Field(default=None, description="Key target emotional response")
    common_misconception: Optional[str] = Field(
        default=None, description="Grounded common misconception from user input or dossier evidence"
    )
    common_failure: Optional[str] = Field(
        default=None, description="Grounded common failure mode or bottleneck from user input or dossier evidence"
    )


def resolve_default_creative_brief(
    platform_format: PlatformFormat = PlatformFormat.SHORTS_9_16,
    content_format: ContentFormat = ContentFormat.EXPLAINER,
    requested_duration: Optional[float] = None,
    primary_goal: Optional[PrimaryVideoGoal] = None,
    dossier: Optional["ResearchDossier"] = None,
    common_misconception: Optional[str] = None,
    common_failure: Optional[str] = None,
) -> VideoCreativeBrief:
    """Provide deterministic defaults for creative brief based on format archetype."""
    if requested_duration and requested_duration > 0.0:
        duration = float(requested_duration)
    elif platform_format == PlatformFormat.LONG_FORM_16_9:
        duration = 240.0
    elif platform_format == PlatformFormat.SQUARE_1_1:
        duration = 50.0
    else:
        duration = 40.0

    format_profiles = {
        ContentFormat.NEWS: (TonePreset.SERIOUS, PrimaryVideoGoal.SHAREABILITY, "urgency and awareness"),
        ContentFormat.CASE_STUDY: (TonePreset.INVESTIGATIVE, PrimaryVideoGoal.AUTHORITY, "forensic curiosity"),
        ContentFormat.DEMO: (TonePreset.TECHNICAL, PrimaryVideoGoal.EDUCATE, "practical competence"),
        ContentFormat.COMPARISON: (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.EDUCATE, "analytical clarity"),
        ContentFormat.EXPERIMENT: (TonePreset.TECHNICAL, PrimaryVideoGoal.WATCH_TIME, "empirical suspense"),
        ContentFormat.BREAKDOWN: (TonePreset.TECHNICAL, PrimaryVideoGoal.EDUCATE, "deep architectural insight"),
        ContentFormat.MYTH_BUSTING: (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.SHAREABILITY, "eye-opening revelation"),
        ContentFormat.STORY: (TonePreset.CINEMATIC_STORY, PrimaryVideoGoal.WATCH_TIME, "narrative immersion"),
        ContentFormat.CHALLENGE: (TonePreset.EXCITED, PrimaryVideoGoal.WATCH_TIME, "high-stakes anticipation"),
        ContentFormat.RANKING: (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.WATCH_TIME, "evaluative engagement"),
        ContentFormat.BEFORE_AFTER: (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.EDUCATE, "transformational payoff"),
        ContentFormat.PROBLEM_SOLUTION: (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.LEAD_GENERATION, "problem-solving relief"),
        ContentFormat.EXPLAINER: (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.WATCH_TIME, "aha-moment enlightenment"),
    }
    tone, goal, emotion = format_profiles.get(
        content_format, (TonePreset.CONVERSATIONAL, PrimaryVideoGoal.WATCH_TIME, "curiosity and clarity")
    )

    if primary_goal is not None:
        goal = primary_goal
        if primary_goal == PrimaryVideoGoal.REVENUE:
            emotion = "commercial value and ROI"

    # Derive grounded misconception/failure if not explicitly supplied
    grounded_misconception = common_misconception
    grounded_failure = common_failure

    if dossier and (grounded_misconception is None or grounded_failure is None):
        import re
        misconception_keywords = ("misconception", "misunderstood", "myth", "mistakenly", "misinterpreted", "confused")
        failure_keywords = ("failure", "bottleneck", "crash", "outage", "flaw", "bug", "deadlock", "timeout", "latency spike")

        candidate_texts = []
        for c in getattr(dossier, "claims", []):
            if getattr(c, "statement", None):
                candidate_texts.append(c.statement)
        for s in getattr(dossier, "sources", []):
            if getattr(s, "content_snapshot", None):
                for sent in re.split(r"(?<=[.!?])\s+", s.content_snapshot):
                    if len(sent.split()) >= 4:
                        candidate_texts.append(sent.strip())

        for text in candidate_texts:
            t_lower = text.lower()
            if grounded_misconception is None and any(k in t_lower for k in misconception_keywords):
                grounded_misconception = text.rstrip(".")
            if grounded_failure is None and any(k in t_lower for k in failure_keywords):
                grounded_failure = text.rstrip(".")
            if grounded_misconception is not None and grounded_failure is not None:
                break

    return VideoCreativeBrief(
        target_duration_seconds=duration,
        primary_goal=goal,
        tone=tone,
        desired_viewer_emotion=emotion,
        common_misconception=grounded_misconception,
        common_failure=grounded_failure,
    )


class HookCandidate(BaseModel):
    """An individual candidate hook generated from a specific psychological angle."""

    text: str = Field(description="The spoken hook text (first 3-5 seconds)")
    angle: HookAngle = Field(description="Psychological hook strategy")
    promise: str = Field(description="Implicit or explicit viewer promise that must be resolved")
    required_claim_ids: List[str] = Field(default_factory=list, description="Claim IDs this hook depends upon")


class HookEvaluation(BaseModel):
    """Heuristic quality evaluation metrics for an individual hook candidate."""

    hook_index: int = Field(description="Index of the hook candidate")
    brevity_score: float = Field(ge=0.0, le=1.0, description="Score for concise delivery (3-5s target)")
    clarity_score: float = Field(ge=0.0, le=1.0, description="Score for cognitive clarity")
    curiosity_score: float = Field(ge=0.0, le=1.0, description="Score for curiosity gap induction")
    relevance_score: float = Field(ge=0.0, le=1.0, description="Score for topic alignment")
    promise_alignment_score: float = Field(ge=0.0, le=1.0, description="Score for clear viewer promise")
    factual_safe: bool = Field(description="Whether hook claims are strictly verified or non-empirical")
    penalties: List[str] = Field(default_factory=list, description="Specific penalty flags applied")
    total_score: float = Field(description="Composite evaluation score (-100 to 1.0)")


class RetentionCue(BaseModel):
    """Pacing and narrative cue anchored to normalized timeline position (0.0 - 1.0)."""

    cue_id: str = Field(description="Unique cue identifier")
    cue_type: RetentionCueType = Field(description="Pacing cue category")
    target_position_ratio: float = Field(ge=0.0, le=1.0, description="Target timeline position (0.0 to 1.0)")
    purpose: str = Field(description="Narrative or visual objective for this cue")
    anchor_text: Optional[str] = None
    linked_hook_promise: Optional[str] = None


class TimedRetentionCue(BaseModel):
    """Retention cue mapped to concrete playback timestamp in seconds after real TTS synthesis."""

    cue_id: str = Field(description="Unique cue identifier matching blueprint cue")
    cue_type: RetentionCueType = Field(description="Pacing cue category")
    timestamp_seconds: float = Field(ge=0.0, description="Resolved playback onset time in seconds")
    narration_anchor: Optional[str] = Field(default=None, description="Spoken text anchor phrase if available")


class RetentionBlueprint(BaseModel):
    """Structured narrative retention plan orchestrating pacing, open loops, and payoff."""

    hook: HookCandidate = Field(description="Winning hook candidate")
    cues: List[RetentionCue] = Field(default_factory=list, description="Sequenced retention cues")
    core_question: str = Field(description="Core unanswered question driving viewer curiosity")
    promised_payoff: str = Field(description="Definitive insight or resolution delivered by video conclusion")
    content_format: ContentFormat = Field(default=ContentFormat.EXPLAINER, description="Format grammar archetype")
    target_duration_seconds: float = Field(default=40.0, ge=5.0, description="Planned total duration")



def compose_canonical_narration(
    hook: Optional[str] = None,
    intro: Optional[str] = None,
    segments: Optional[List[Scene]] = None,
    cta: Optional[str] = None,
) -> str:
    """Deterministically compose the canonical continuous spoken voiceover narration from script sections."""
    parts = []
    if hook and hook.strip():
        parts.append(hook.strip())
    if intro and intro.strip():
        parts.append(intro.strip())
    if segments:
        for s in segments:
            if s.narration and s.narration.strip():
                parts.append(s.narration.strip())
    if cta and cta.strip():
        parts.append(cta.strip())
    return " ".join(parts)


class ScriptSections(BaseModel):
    """Typed script breakdown with discrete narrative sections."""

    hook: str = Field(description="Opening hook (first 3-5 seconds)")
    intro: str = Field(description="Context setup / problem statement")
    segments: List[Scene] = Field(default_factory=list, description="Ordered scene sequence")
    cta: str = Field(description="Call to action / outro")
    voiceover_text: str = Field(default="", description="Consolidated full spoken narration text")
    estimated_duration: float = Field(default=30.0, ge=1.0, description="Estimated total runtime in seconds")
    retention_blueprint: Optional[RetentionBlueprint] = Field(
        default=None, description="Format-aware narrative retention blueprint"
    )

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

    @model_validator(mode="after")
    def ensure_canonical_voiceover(self) -> "ScriptSections":
        self.voiceover_text = compose_canonical_narration(
            hook=self.hook,
            intro=self.intro,
            segments=self.segments,
            cta=self.cta,
        )
        return self


class Script(BaseModel):
    """Structured video script containing narrative scenes and metadata."""

    id: str = Field(description="Unique script ID")
    title: str = Field(description="Video working title")
    hook: str = Field(description="Opening hook line")
    scenes: List[Scene] = Field(default_factory=list, description="Ordered scene sequence")
    total_word_count: int = Field(ge=1, description="Total word count")
    estimated_duration_seconds: float = Field(ge=1.0, description="Estimated total runtime")
    sections: Optional[ScriptSections] = Field(default=None, description="Typed narrative sections breakdown")
    content_format: ContentFormat = Field(default=ContentFormat.EXPLAINER, description="Content format archetype")
    retention_blueprint: Optional[RetentionBlueprint] = Field(
        default=None, description="Format-aware narrative retention blueprint"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_canonical_narration(self) -> str:
        """Get the single authoritative canonical spoken voiceover narration."""
        if self.sections and self.sections.voiceover_text.strip():
            return self.sections.voiceover_text.strip()
        if self.scenes:
            parts = []
            if self.hook and self.hook.strip():
                parts.append(self.hook.strip())
            parts.extend(s.narration.strip() for s in self.scenes if s.narration and s.narration.strip())
            return " ".join(parts)
        return ""

    def compute_canonical_narration_hash(self) -> str:
        """Compute the deterministic SHA-256 hash of canonical narration."""
        import hashlib
        return hashlib.sha256(self.get_canonical_narration().strip().encode("utf-8")).hexdigest()



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


class ReviewRecord(BaseModel):
    """Human review decision and audit trail for a video project (Stage 12)."""

    id: str = Field(description="Unique review record ID (e.g. rev-001)")
    project_id: str = Field(description="Associated video project ID")
    operator: str = Field(description="Identifier or name of human reviewer")
    action: ReviewAction = Field(description="Review action taken (APPROVE, REJECT, RERENDER, BLOCK)")
    notes: Optional[str] = Field(default=None, description="Reviewer comments or revision guidance")
    approved_privacy_status: PrivacyStatus = Field(default=PrivacyStatus.PRIVATE, description="Approved publication privacy status")
    approval_origin: ApprovalOrigin = Field(default=ApprovalOrigin.AUTOMATION, description="Provenance of approval: HUMAN, AUTOMATION, or TEST")
    media_overrides: Dict[str, Any] = Field(default_factory=dict, description="Requested media/parameter overrides")
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of review")


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
    contains_synthetic_media: bool = Field(default=False, description="Whether this video contains synthetic or AI-generated media")
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalyticsSnapshot(BaseModel):
    """Performance metrics captured from YouTube Analytics API."""

    id: str = Field(description="Unique snapshot ID")
    project_id: str = Field(description="Associated project ID")
    youtube_video_id: Optional[str] = Field(default=None, description="YouTube video ID")
    snapshot_type: str = Field(default="REAL", description="REAL or SIMULATED")
    is_simulated: bool = Field(default=False, description="Whether this snapshot is simulated")
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
    content_format: ContentFormat = Field(default=ContentFormat.EXPLAINER, description="Content format archetype")
    script: Optional[Script] = Field(default=None)
    assets: List[Asset] = Field(default_factory=list)
    quality: Optional[QualityResult] = Field(default=None)
    metadata_tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContentSeries(BaseModel):
    """Represents a recurring thematic episodic series on a channel."""

    id: str = Field(description="Unique series identifier (e.g. ser-001)")
    channel_id: str = Field(description="Associated channel ID")
    title: str = Field(description="Series title (e.g. '60-Second Deep Tech')")
    description: Optional[str] = Field(default=None, description="Series editorial mandate")
    target_niche: str = Field(description="Primary niche covered by this series")
    default_format: PlatformFormat = Field(default=PlatformFormat.SHORTS_9_16)
    frequency_per_week: int = Field(default=3, ge=1, le=14, description="Target releases per week")
    playlist_id: Optional[str] = Field(default=None, description="Associated YouTube playlist ID")
    visual_style_preset: str = Field(default="modern_tech", description="Preset visual style")
    next_episode_number: int = Field(default=1, ge=1, description="Next episode counter")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EditorialSlot(BaseModel):
    """A scheduled publication slot on the channel's calendar."""

    id: str = Field(description="Unique slot ID (e.g. slot-20260908-1800)")
    channel_id: str = Field(description="Associated channel ID")
    series_id: Optional[str] = Field(default=None, description="Associated series ID if episodic")
    project_id: Optional[str] = Field(default=None, description="Assigned video project ID")
    slot_time: datetime = Field(description="Scheduled target release timestamp (UTC)")
    status: EditorialSlotStatus = Field(default=EditorialSlotStatus.PLANNED)
    target_topic: str = Field(description="Topic/keyword assigned for this release")
    episode_number: Optional[int] = Field(default=None, description="Episodic number in series")
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TitleVariant(BaseModel):
    """An individual CTR-optimized title variant targeting a specific psychological angle."""

    angle: TitleVariantType = Field(description="Psychological angle (CURIOSITY_GAP, DIRECT_VALUE, PROVOCATIVE_QUESTION)")
    title: str = Field(description="Title string (must adhere to <= 100 characters)")
    predicted_ctr_rationale: str = Field(description="Explanation of why this title drives click-through")


class Chapter(BaseModel):
    """A timestamped chapter marker in a video."""

    timestamp_seconds: float = Field(ge=0.0, description="Start timestamp in seconds")
    timestamp_formatted: str = Field(description="Formatted timestamp (e.g. '00:00' or '01:23')")
    title: str = Field(description="Chapter title")


class SEOPackage(BaseModel):
    """Complete metadata and algorithmic optimization package for a video."""

    id: str = Field(description="Unique SEO package ID (e.g. seo-001)")
    project_id: str = Field(description="Associated video project ID")
    primary_keyword: str = Field(description="Core search keyword")
    title_variants: List[TitleVariant] = Field(description="3 psychology-driven title angles")
    selected_title: str = Field(description="Primary chosen title for publication")
    description: str = Field(description="Fully packaged video description (hook, chapters, citations)")
    chapters: List[Chapter] = Field(default_factory=list, description="Extracted video chapters")
    tags: List[str] = Field(default_factory=list, description="Clustered tags (strictly <= 500 chars total)")
    pinned_comment: str = Field(description="Engagement-trigger question for the first pinned comment")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ThumbnailPackage(BaseModel):
    """Generated thumbnail package with multi-aspect renders and safe-zone adherence."""

    id: str = Field(description="Unique thumbnail package ID (e.g. thm-001)")
    project_id: str = Field(description="Associated video project ID")
    file_path_16_9: Optional[str] = Field(default=None, description="Path to 1280x720 thumbnail image")
    file_path_9_16: Optional[str] = Field(default=None, description="Path to 1080x1920 short cover image")
    headline_text: str = Field(description="High-contrast overlay text (2-4 punchy words)")
    content_sha256: str = Field(description="SHA-256 hash of thumbnail image")
    provenance: Dict[str, Any] = Field(default_factory=dict, description="Generation metadata and background source")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QuotaUsageRecord(BaseModel):
    """Daily YouTube Data API v3 quota consumption record."""

    id: str = Field(description="Unique quota record ID (e.g. qta-001)")
    operation: str = Field(description="YouTube API operation (videos.insert, thumbnails.set, etc.)")
    units_consumed: int = Field(ge=1, description="Quota units spent on this call")
    daily_budget: int = Field(default=10000, description="Total daily quota allowance")
    consumed_date: str = Field(description="Date string YYYY-MM-DD in America/Los_Angeles (PT) time")
    project_id: Optional[str] = Field(default=None, description="Associated project if applicable")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
