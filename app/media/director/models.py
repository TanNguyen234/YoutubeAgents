"""Domain models, enums, and typed contracts for the automated video director pipeline."""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class BeatPurpose(str, Enum):
    """Semantic storytelling purpose of an individual narrative beat."""

    HOOK = "HOOK"
    EXPLAIN = "EXPLAIN"
    DEMONSTRATE = "DEMONSTRATE"
    COMPARE = "COMPARE"
    PROVE = "PROVE"
    CONTEXT = "CONTEXT"
    STORY = "STORY"
    TRANSITION = "TRANSITION"
    PAYOFF = "PAYOFF"
    CTA = "CTA"


class CreativeFallbackPolicy(str, Enum):
    """Policy governing whether production pipeline can fallback to legacy slides when AutoDirector fails."""

    FAIL_CLOSED = "FAIL_CLOSED"
    ALLOW_LEGACY_PREVIEW = "ALLOW_LEGACY_PREVIEW"


class VisualIntent(str, Enum):
    """What the viewer should visually observe or comprehend (distinct from spoken audio)."""

    SHOW_MECHANISM = "SHOW_MECHANISM"
    SHOW_RESULT = "SHOW_RESULT"
    SHOW_DIFFERENCE = "SHOW_DIFFERENCE"
    SHOW_EVIDENCE = "SHOW_EVIDENCE"
    SHOW_PROCESS = "SHOW_PROCESS"
    SHOW_SCALE = "SHOW_SCALE"
    SHOW_TIMELINE = "SHOW_TIMELINE"
    SHOW_LOCATION = "SHOW_LOCATION"
    SHOW_INTERFACE = "SHOW_INTERFACE"
    SHOW_CODE = "SHOW_CODE"
    SHOW_DATA = "SHOW_DATA"
    SHOW_CHARACTER_ACTION = "SHOW_CHARACTER_ACTION"
    ESTABLISH_CONTEXT = "ESTABLISH_CONTEXT"
    CREATE_EMOTION = "CREATE_EMOTION"


class VisualModality(str, Enum):
    """Visual medium / rendering modality for executing a video shot."""

    SCREEN_CAPTURE = "SCREEN_CAPTURE"
    UI_SIMULATION = "UI_SIMULATION"
    CODE_ANIMATION = "CODE_ANIMATION"
    DIAGRAM = "DIAGRAM"
    DATA_VISUALIZATION = "DATA_VISUALIZATION"
    SCREENSHOT = "SCREENSHOT"
    DOCUMENT_EVIDENCE = "DOCUMENT_EVIDENCE"
    STOCK_VIDEO = "STOCK_VIDEO"
    GENERATED_VIDEO = "GENERATED_VIDEO"
    GENERATED_IMAGE = "GENERATED_IMAGE"
    IMAGE_TO_VIDEO = "IMAGE_TO_VIDEO"
    MOTION_GRAPHICS = "MOTION_GRAPHICS"
    KINETIC_TYPOGRAPHY = "KINETIC_TYPOGRAPHY"
    MAP = "MAP"
    TIMELINE = "TIMELINE"
    COMPARISON = "COMPARISON"
    STATIC_DIAGRAM = "STATIC_DIAGRAM"
    STATIC_CHART = "STATIC_CHART"
    STATIC_TERMINAL = "STATIC_TERMINAL"
    STATIC_CARD = "STATIC_CARD"  # Explicitly low-priority fallback


class VisualizationDataMode(str, Enum):
    """Semantic data grounding mode: GROUNDED requires verified evidence; CONCEPTUAL prohibits empirical claims."""

    GROUNDED = "GROUNDED"
    CONCEPTUAL = "CONCEPTUAL"


class ChartDatumOrigin(str, Enum):
    """Origin category of a chart datum point."""

    VERIFIED_CLAIM = "VERIFIED_CLAIM"
    EXTERNAL_SOURCE = "EXTERNAL_SOURCE"
    CONCEPTUAL = "CONCEPTUAL"


from app.domain.enums import ContentFormat, RetentionCueType
from app.domain.models import TimedRetentionCue


class MissingGroundedVisualData(ValueError):
    """Raised when an empirical visual modality is requested without grounded factual data."""
    pass


class DirectorOutputError(RuntimeError):
    """Raised when AutoDirector returns empty or invalid timeline/storyboard output."""
    pass



class ChartDatum(BaseModel):
    """Grounded datum point for data visualization shots."""

    label: str = Field(description="Category or series label")
    value: float = Field(description="Numerical value")
    unit: Optional[str] = Field(default=None, description="Metric unit (e.g. %, ms, GB, $B)")
    source_ref: Optional[str] = Field(default=None, description="Citation or source reference ID")
    origin: ChartDatumOrigin = Field(default=ChartDatumOrigin.CONCEPTUAL, description="Provenance origin of the datum")
    claim_id: Optional[str] = Field(default=None, description="Associated verified Claim ID")


class ComparisonColumn(BaseModel):
    """Grounded column details for comparison shots."""

    label: str = Field(description="Subject or architecture name being compared")
    points: List[str] = Field(default_factory=list, description="Explicit verified points or characteristics")
    source_refs: List[str] = Field(default_factory=list, description="Source references verifying these points")


class EvidenceBinding(BaseModel):
    """Factually grounded source binding for DOCUMENT_EVIDENCE shots."""

    claim_id: Optional[str] = Field(default=None, description="Associated claim identifier")
    source_ref: str = Field(description="Internal source reference or ID")
    source_title: str = Field(description="Document title or publication source")
    source_url: str = Field(description="Verified URL of original source")
    claim_text: str = Field(default="", description="The specific claim statement or paraphrase")
    source_excerpt: Optional[str] = Field(default=None, description="Exact quotation or excerpt from source text")
    excerpt_is_verbatim: bool = Field(default=False, description="Whether excerpt is a verbatim extract from document")
    claim_verified: bool = Field(default=False, description="Whether associated claim is verified by fact checker")
    quote_or_excerpt: Optional[str] = Field(default=None, description="Legacy field for backward compatibility")


class ProposedNarrativeBeat(BaseModel):
    """Untrusted LLM-proposed narrative beat before server-side provenance materialization.

    Strictly isolated from trust-sensitive factual provenance fields.
    MUST NOT contain: key_claim, source_refs, claim_id, claim_verified, evidence_binding,
    chart_data, ChartDatum, ChartDatumOrigin, source_url, source_excerpt.
    """

    model_config = {"extra": "forbid"}

    beat_id: str = Field(description="Unique beat identifier (e.g. b_01, b_02)")
    scene_index: int = Field(default=0, ge=0, description="Parent script scene sequence index")
    narration: str = Field(description="Spoken narration text corresponding to this beat")
    start_hint: Optional[float] = Field(default=None, ge=0.0, description="Estimated start time in seconds")
    duration_hint: Optional[float] = Field(default=None, ge=0.1, description="Estimated duration in seconds")
    purpose: BeatPurpose = Field(default=BeatPurpose.EXPLAIN, description="Storytelling role of this beat")
    key_entities: List[str] = Field(default_factory=list, description="Key subjects/tools/concepts mentioned")
    visual_intent: VisualIntent = Field(default=VisualIntent.SHOW_MECHANISM, description="Desired visual goal")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Visual priority weight")
    preferred_modalities: List[VisualModality] = Field(default_factory=list, description="Priority visual modalities")
    avoid_modalities: List[VisualModality] = Field(
        default_factory=lambda: [VisualModality.STATIC_CARD], description="Modalities that dilute impact"
    )
    creative_rationale: Optional[str] = Field(default=None, description="Optional LLM creative rationale")
    suggested_subject: Optional[str] = Field(default=None, description="Optional LLM suggested subject")
    suggested_action: Optional[str] = Field(default=None, description="Optional LLM suggested visual action")


class ProposedBeatsPayload(BaseModel):
    """Structured LLM payload for proposed narrative beats."""

    beats: List[ProposedNarrativeBeat] = Field(default_factory=list)


class NarrativeBeat(BaseModel):
    """Intermediate representation between a script scene and concrete visual shots."""

    beat_id: str = Field(description="Unique beat identifier (e.g. b_01, b_02)")
    scene_index: int = Field(default=0, ge=0, description="Parent script scene sequence index")
    narration: str = Field(description="Spoken narration text corresponding to this beat")
    start_hint: Optional[float] = Field(default=None, ge=0.0, description="Estimated start time in seconds")
    duration_hint: Optional[float] = Field(default=None, ge=0.1, description="Estimated duration in seconds")
    purpose: BeatPurpose = Field(default=BeatPurpose.EXPLAIN, description="Storytelling role of this beat")
    key_claim: Optional[str] = Field(default=None, description="Core claim or insight delivered")
    key_entities: List[str] = Field(default_factory=list, description="Key subjects/tools/concepts mentioned")
    visual_intent: VisualIntent = Field(default=VisualIntent.SHOW_MECHANISM, description="Desired visual goal")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Visual priority weight")
    requires_evidence: bool = Field(default=False, description="Whether claim needs citation/benchmark evidence")
    preferred_modalities: List[VisualModality] = Field(default_factory=list, description="Priority visual modalities")
    avoid_modalities: List[VisualModality] = Field(
        default_factory=lambda: [VisualModality.STATIC_CARD], description="Modalities that dilute impact"
    )
    source_refs: List[str] = Field(default_factory=list, description="Associated source citations")
    chart_data: List[ChartDatum] = Field(default_factory=list, description="Explicit grounded chart numbers")
    evidence_binding: Optional[EvidenceBinding] = Field(default=None, description="Verified source binding")
    visual_data_mode: VisualizationDataMode = Field(
        default=VisualizationDataMode.GROUNDED, description="Grounding data mode: GROUNDED vs CONCEPTUAL"
    )


class MotionCue(BaseModel):
    """Temporal animation cue specifying internal motion timing and targets."""

    cue_type: str = Field(description="Animation primitive: 'type_prompt', 'reveal_candidates', 'grow_bars', 'highlight_pulse', 'append_token', 'terminal_typing', 'line_reveal', 'wipe'")
    start: float = Field(description="Start time in seconds relative to shot onset")
    duration: float = Field(description="Duration in seconds of this motion cue")
    target: Optional[str] = Field(default=None, description="Target element, token, or layer")
    value: Optional[Union[str, float]] = Field(default=None, description="Target value or content string")


class ShotSpec(BaseModel):
    """Specification of an individual video shot ready for asset generation or rendering."""

    shot_id: str = Field(description="Unique shot identifier (e.g. s_01_01)")
    beat_id: str = Field(description="Associated narrative beat ID")
    scene_index: int = Field(default=0, ge=0, description="Parent script scene index")
    narration_segment: str = Field(description="Spoken voiceover covering this shot duration")
    purpose: str = Field(default="explain", description="Short description of shot function")
    duration_seconds: float = Field(ge=0.2, description="Exact duration in seconds")
    subject: Optional[str] = Field(default=None, description="Primary visual subject")
    action: Optional[str] = Field(default=None, description="Visible motion, state change, or progression")
    environment: Optional[str] = Field(default=None, description="Visual background or setting")
    visual_modality: VisualModality = Field(description="Selected visual modality")
    composition: Optional[str] = Field(default=None, description="Framing / layout composition")
    camera_motion: Optional[str] = Field(default=None, description="Camera movement instruction")
    asset_query: Optional[str] = Field(default=None, description="Stock footage query if applicable")
    generation_prompt: Optional[str] = Field(default=None, description="AI generative model prompt")
    screen_instruction: Optional[str] = Field(default=None, description="UI / screen simulator instruction")
    diagram_instruction: Optional[str] = Field(default=None, description="Diagram layout and node connection spec")
    code_instruction: Optional[str] = Field(default=None, description="Code snippet or terminal command spec")
    chart_instruction: Optional[str] = Field(default=None, description="Chart type, labels, and grounded numbers")
    evidence_instruction: Optional[str] = Field(default=None, description="Document quotation / benchmark highlight")
    motion_graphic_instruction: Optional[str] = Field(default=None, description="Motion graphics animation spec")
    headline_text: Optional[str] = Field(default=None, description="Concise label or punchline (NOT repeating narration)")
    continuity_refs: List[str] = Field(default_factory=list, description="Style or entity continuity references")
    source_refs: List[str] = Field(default_factory=list, description="Source provenance citations")
    importance: float = Field(default=0.5, ge=0.0, le=1.0, description="Visual importance score")
    chart_data: List[ChartDatum] = Field(default_factory=list, description="Structured grounded chart data points")
    comparison_left: Optional[ComparisonColumn] = Field(default=None, description="Verified left comparison column")
    comparison_right: Optional[ComparisonColumn] = Field(default=None, description="Verified right comparison column")
    evidence_binding: Optional[EvidenceBinding] = Field(default=None, description="Grounded source evidence binding")
    code_output_lines: List[str] = Field(default_factory=list, description="Verified output lines for terminal execution")
    terminal_mode: str = Field(default="ILLUSTRATIVE_TERMINAL", description="REAL_TERMINAL vs ILLUSTRATIVE_TERMINAL")
    requested_modality: Optional[VisualModality] = Field(default=None, description="Original requested modality before fallback")
    visual_data_mode: VisualizationDataMode = Field(
        default=VisualizationDataMode.GROUNDED, description="Grounding data mode: GROUNDED vs CONCEPTUAL"
    )
    motion_cues: List[MotionCue] = Field(default_factory=list, description="Explicit temporal animation cues for true motion graphics")
    intentional_callback: bool = Field(default=False, description="Whether shot deliberately repeats an earlier visual for continuity")


class ShotAssetResult(BaseModel):
    """Result of visual asset generation capturing true modality accounting and fallback provenance."""

    path: Path = Field(description="Local filepath of generated asset")
    sha256: str = Field(description="SHA-256 digest of generated asset")
    requested_modality: VisualModality = Field(description="Original modality requested by storyboard")
    actual_modality: VisualModality = Field(description="Actual modality produced after fallback resolution")
    provider: str = Field(description="Name of provider or renderer that generated the asset")
    fallback_reason: Optional[str] = Field(default=None, description="Reason for modality fallback if applicable")
    source_type: Optional[str] = Field(default=None, description="Visual source type (e.g. RESEARCH_SOURCE, DOCUMENT, RENDERED)")
    source_url: Optional[str] = Field(default=None, description="Source provenance URL")
    license_type: Optional[str] = Field(default=None, description="Asset license terms")
    attribution: Optional[str] = Field(default=None, description="Author or source attribution")
    acquisition_method: Optional[str] = Field(default=None, description="Acquisition service or renderer method")
    is_synthetic: bool = Field(default=False, description="Whether asset is AI-generated synthetic media")
    evidence_claim_ids: List[str] = Field(default_factory=list, description="Linked verified claim IDs")

    def __iter__(self):
        """Allow tuple unpacking (path, sha256) for backward compatibility."""
        return iter((self.path, self.sha256))


class OverlaySpec(BaseModel):
    """Supporting text, stat, or badge overlay rendered on top of the shot."""

    overlay_type: str = Field(default="badge", description="badge, stat, arrow, highlight, or label")
    content: str = Field(description="Overlay text or numerical value")
    position: str = Field(default="top_left", description="top_left, center, bottom_right, etc.")
    start_time: float = Field(default=0.0, ge=0.0, description="Relative start offset within shot in seconds")
    duration: float = Field(default=1.5, ge=0.1, description="Display duration in seconds")
    style: Dict[str, Any] = Field(default_factory=dict, description="Custom styling parameters")


class TimelineShot(BaseModel):
    """Concrete timed shot placed on the final video composition timeline."""

    shot_id: str = Field(description="Shot identifier matching ShotSpec")
    scene_index: int = Field(default=0, ge=0)
    beat_id: str = Field(description="Beat identifier")
    start: float = Field(ge=0.0, description="Absolute start timestamp in seconds on timeline")
    end: float = Field(ge=0.0, description="Absolute end timestamp in seconds on timeline")
    duration: float = Field(ge=0.1, description="Duration in seconds")
    asset_path: str = Field(description="Local file path to rendered image or video asset")
    asset_sha256: str = Field(description="SHA-256 hash of asset file")
    modality: VisualModality = Field(description="Modality used for this shot")
    is_animated: bool = Field(default=False, description="Whether shot asset is a true temporal video/animation rather than still frame")
    transition_in: Optional[str] = Field(default=None, description="Incoming transition effect")
    transition_out: Optional[str] = Field(default=None, description="Outgoing transition effect")
    overlays: List[OverlaySpec] = Field(default_factory=list, description="Overlays active during this shot")
    asset_source_type: Optional[str] = Field(default=None, description="Visual source type")
    asset_source_url: Optional[str] = Field(default=None, description="Provenance source URL")
    asset_license: Optional[str] = Field(default=None, description="Asset license terms")
    asset_attribution: Optional[str] = Field(default=None, description="Author or source attribution")
    asset_acquisition_method: Optional[str] = Field(default=None, description="Acquisition method or renderer")
    asset_is_synthetic: bool = Field(default=False, description="Whether asset is AI-generated synthetic media")



class ShotTimeline(BaseModel):
    """Complete ordered timeline of shots spanning the entire video audio track."""

    shots: List[TimelineShot] = Field(default_factory=list, description="Ordered timeline shots")
    total_duration: float = Field(default=0.0, ge=0.0, description="Total video duration in seconds")

    def validate_continuity(self, tolerance_seconds: float = 0.05) -> List[str]:
        """Verify there are no unintended gaps or negative overlaps between consecutive shots."""
        issues = []
        if not self.shots:
            return ["Timeline contains zero shots."]
        if self.shots[0].start > tolerance_seconds:
            issues.append(f"Timeline starts with a gap of {self.shots[0].start:.3f}s.")
        for i in range(len(self.shots) - 1):
            s1 = self.shots[i]
            s2 = self.shots[i + 1]
            drift = s2.start - s1.end
            if abs(drift) > tolerance_seconds:
                issues.append(f"Shot gap/overlap between '{s1.shot_id}' (ends {s1.end:.3f}s) and '{s2.shot_id}' (starts {s2.start:.3f}s): drift {drift:.3f}s.")
        last_end = self.shots[-1].end
        if abs(last_end - self.total_duration) > tolerance_seconds and self.total_duration > 0.0:
            issues.append(f"Timeline end ({last_end:.3f}s) does not match total duration ({self.total_duration:.3f}s).")
        return issues


class Storyboard(BaseModel):
    """Complete storyboard artifact connecting script, beats, shots, and modality choices."""

    project_id: str = Field(description="Associated project ID")
    total_duration: float = Field(ge=0.0, description="Planned total audio runtime in seconds")
    content_format: ContentFormat = Field(default=ContentFormat.EXPLAINER)
    profile_name: str = Field(default="Editorial Tech Shorts", description="Resolved channel creative profile name")
    beats: List[NarrativeBeat] = Field(default_factory=list, description="Ordered narrative beats")
    shots: List[ShotSpec] = Field(default_factory=list, description="Ordered shot specs")
    modality_counts: Dict[str, int] = Field(default_factory=dict, description="Count of shots per modality")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def compute_hash(self) -> str:
        """Compute a deterministic hash of the storyboard shots, modalities, timing, and profile."""
        import hashlib
        raw = f"{self.project_id}|{self.total_duration:.3f}|{self.content_format.value}|{self.profile_name}|" + "|".join(
            f"{s.shot_id}:{s.visual_modality.value}:{s.duration_seconds:.3f}:{(s.headline_text or '').strip()}:"
            f"{(s.chart_instruction or '').strip()}:{(s.diagram_instruction or '').strip()}:{(s.code_instruction or '').strip()}"
            for s in self.shots
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_director_output(timeline: Optional[ShotTimeline], storyboard: Optional[Storyboard]) -> None:
    """Validate that AutoDirector produced non-empty, continuous, and valid timeline and storyboard."""
    if timeline is None:
        raise DirectorOutputError("Director returned None timeline.")
    if storyboard is None:
        raise DirectorOutputError("Director returned None storyboard.")
    if not timeline.shots:
        raise DirectorOutputError("Director returned timeline with zero shots.")
    if not storyboard.shots:
        raise DirectorOutputError("Director returned storyboard with zero shots.")
    if len(timeline.shots) != len(storyboard.shots):
        raise DirectorOutputError(
            f"Director output mismatch: timeline has {len(timeline.shots)} shots, "
            f"storyboard has {len(storyboard.shots)} shots."
        )
    if timeline.total_duration <= 0.0:
        raise DirectorOutputError(
            f"Director timeline total_duration ({timeline.total_duration}) must be greater than zero."
        )


class VisualEvaluation(BaseModel):
    """Quality evaluation metrics for an individual visual shot."""

    visual_relevance: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Estimated visual relevance (None if not VLM-evaluated)")
    narration_duplication: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Penalty for repeating narration verbatim on screen (0 = distinct, 1 = duplicate slide)",
    )
    information_value: float = Field(default=0.8, ge=0.0, le=1.0, description="Heuristic score of how much information visuals add")
    motion_value: float = Field(default=0.7, ge=0.0, le=1.0, description="Heuristic motion potential score")
    continuity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Estimated continuity (None if not VLM-evaluated)")
    evidence_strength: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Grounding / empirical proof score")
    readability: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Estimated readability (None if not VLM-evaluated)")
    aesthetic_quality: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Estimated aesthetic quality (None if not VLM-evaluated)")
    evaluation_mode: str = Field(default="METADATA_HEURISTIC", description="Evaluation engine: METADATA_HEURISTIC or VLM_INSPECTED")
    overall_score: float = Field(default=0.8, ge=0.0, le=1.0, description="Weighted composite score")
    issues: List[str] = Field(default_factory=list, description="Detected visual defects or warnings")
    recommendation: str = Field(default="ACCEPT", description="ACCEPT, REGENERATE, or REPLAN")


class VideoQualityReport(BaseModel):
    """Comprehensive visual quality assessment report for the completed video production."""

    total_shots: int = Field(ge=0, description="Total number of discrete visual shots")
    average_shot_duration: float = Field(ge=0.0, description="Mean shot length in seconds")
    static_card_ratio: float = Field(ge=0.0, le=1.0, description="Proportion of runtime spent on static cards")
    static_semantic_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="Proportion of runtime on static diagrams/charts")
    ken_burns_only_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="Proportion of runtime on stills with pan/zoom only")
    true_motion_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="Proportion of runtime on true animated/video assets")
    modality_distribution: Dict[str, int] = Field(default_factory=dict, description="Count of shots by modality")
    visual_dead_air_warnings: List[str] = Field(default_factory=list, description="Warnings for excessively long static shots")
    narration_duplication_warnings: List[str] = Field(default_factory=list, description="Warnings for slides repeating spoken words")
    failed_asset_attempts: int = Field(default=0, ge=0, description="Number of failed generation attempts")
    overall_visual_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Overall director quality score")
    creative_status: str = Field(default="PASS", description="Creative QA release status: PASS, PASS_WITH_WARNINGS, or FAIL")
    critical_failures: List[str] = Field(default_factory=list, description="Hard failures preventing release")
    warnings: List[str] = Field(default_factory=list, description="Non-blocking warnings")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChannelCreativeProfile(BaseModel):
    """Configurable channel-level visual style and anti-AI-slop creative policy."""

    name: str = Field(default="Tech Engineering Channel")
    niche: List[str] = Field(default_factory=lambda: ["technology", "software_engineering", "ai"])
    content_formats: List[ContentFormat] = Field(
        default_factory=lambda: [ContentFormat.EXPLAINER, ContentFormat.DEMO, ContentFormat.COMPARISON]
    )
    primary_style: str = Field(default="editorial-tech", description="Primary visual style")
    secondary_style: str = Field(default="clean-motion-graphics", description="Secondary style")
    target_average_shot_length: float = Field(default=2.4, ge=1.0, le=6.0, description="Target pacing in seconds")
    preferred_modalities: List[VisualModality] = Field(
        default_factory=lambda: [
            VisualModality.UI_SIMULATION,
            VisualModality.DIAGRAM,
            VisualModality.CODE_ANIMATION,
            VisualModality.DATA_VISUALIZATION,
            VisualModality.MOTION_GRAPHICS,
            VisualModality.DOCUMENT_EVIDENCE,
            VisualModality.GENERATED_VIDEO,
        ]
    )
    avoid_modalities: List[VisualModality] = Field(default_factory=lambda: [VisualModality.STATIC_CARD])
    avoid_tropes: List[str] = Field(
        default_factory=lambda: [
            "generic cyberpunk",
            "glowing AI brain",
            "humanoid robot",
            "meaningless code backgrounds",
            "random neon lights",
            "generic businessman",
            "generic office stock",
            "text slides repeating narration",
        ]
    )
    music_mood: str = Field(default="anime_lofi", description="BGM musical mood / aesthetic")
    music_bpm: int = Field(default=85, ge=40, le=180, description="Target tempo for background music")
    sfx_intensity: float = Field(default=1.0, ge=0.0, le=2.0, description="Relative sound effect mixing intensity")
