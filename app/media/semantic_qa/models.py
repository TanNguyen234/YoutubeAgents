"""Domain models, enums, and typed contracts for Visual Semantic QA and Candidate Judging."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class VisualSemanticVerdict(str, Enum):
    """Overall semantic qualification verdict for a visual candidate."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    UNCERTAIN = "UNCERTAIN"


class VisualSemanticIssue(str, Enum):
    """Categorized semantic defect or intent mismatch."""

    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    ACTION_MISMATCH = "ACTION_MISMATCH"
    VISUAL_INTENT_MISMATCH = "VISUAL_INTENT_MISMATCH"

    EVIDENCE_NOT_VISIBLE = "EVIDENCE_NOT_VISIBLE"
    EVIDENCE_UNREADABLE = "EVIDENCE_UNREADABLE"
    WRONG_DOCUMENT_REGION = "WRONG_DOCUMENT_REGION"

    UI_STATE_NOT_SHOWN = "UI_STATE_NOT_SHOWN"
    MECHANISM_NOT_EXPLAINED = "MECHANISM_NOT_EXPLAINED"
    COMPARISON_NOT_VISIBLE = "COMPARISON_NOT_VISIBLE"
    COMPARISON_NOT_CLEAR = "COMPARISON_NOT_CLEAR"
    DATA_LABELS_UNREADABLE = "DATA_LABELS_UNREADABLE"
    DATA_UNREADABLE = "DATA_UNREADABLE"

    GENERIC_STOCK = "GENERIC_STOCK"
    DECORATIVE_ONLY = "DECORATIVE_ONLY"
    TEXT_REPEATS_NARRATION = "TEXT_REPEATS_NARRATION"
    VISUAL_CLUTTER = "VISUAL_CLUTTER"
    WEAK_COMPOSITION = "WEAK_COMPOSITION"
    LOW_INFORMATION_DENSITY = "LOW_INFORMATION_DENSITY"

    GENERATED_TEXT_ARTIFACT = "GENERATED_TEXT_ARTIFACT"
    VISUAL_CONTRADICTION = "VISUAL_CONTRADICTION"


class VisualSemanticQAMode(str, Enum):
    """Operational mode for Visual Semantic QA."""

    DISABLED = "DISABLED"
    ADVISORY = "ADVISORY"
    REQUIRED = "REQUIRED"


class CandidateVisualSample(BaseModel):
    """Representative visual frames sampled from a video or image candidate for inspection."""

    candidate_id: str = Field(description="Candidate asset identifier")
    sample_paths: List[str] = Field(default_factory=list, description="Local paths to representative frames")
    sample_sha256s: List[str] = Field(default_factory=list, description="SHA-256 digests of sampled frames")
    sampling_method: str = Field(description="Sampling strategy used (e.g. direct_image, ffmpeg_25_50_75)")


class VisualSemanticAssessment(BaseModel):
    """Typed semantic visual assessment produced by VLM / visual judge.

    Strict Trust Boundary:
    Contains ONLY heuristic visual QA scores, issues, and evaluation metadata.
    MUST NOT contain or mutate factual provenance:
    source_ref, claim_id, claim_verified, source_url, license_type.
    """

    model_config = {"extra": "forbid"}

    candidate_id: str = Field(description="Evaluated candidate asset ID")
    shot_id: str = Field(description="Evaluated storyboard shot ID")

    verdict: VisualSemanticVerdict = Field(description="ACCEPT, REJECT, or UNCERTAIN")

    semantic_relevance: float = Field(ge=0.0, le=1.0, description="Visual relevance to narration and topic")
    visual_intent_match: float = Field(ge=0.0, le=1.0, description="Fulfillment of requested visual intent")
    subject_match: float = Field(ge=0.0, le=1.0, description="Presence and prominence of primary subject")
    action_match: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Portrayal of requested action or mechanism")

    readability: float = Field(ge=0.0, le=1.0, description="Legibility of text/labels at video resolution")
    composition_quality: float = Field(ge=0.0, le=1.0, description="Framing, balance, and aesthetic clarity")
    information_value: float = Field(ge=0.0, le=1.0, description="Non-decorative informational contribution")

    evidence_visibility: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Visibility of cited evidence excerpt")
    interface_state_match: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Accuracy of depicted UI/app state")
    mechanism_clarity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Clarity in explaining mechanism or flow")
    comparison_clarity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Clarity in showing comparative difference")
    data_readability: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Readability of chart axes and values")

    generic_slop_score: float = Field(ge=0.0, le=1.0, description="Penalty score for generic/stock cliches")

    issues: List[VisualSemanticIssue] = Field(default_factory=list, description="Detected semantic issues or mismatches")

    concise_reason: str = Field(description="Brief explanation of verdict and score justification")

    evaluator_backend: str = Field(description="Backend identifier (e.g. antigravity_cli, mock_test)")
    evaluator_model: Optional[str] = Field(default=None, description="Model identifier used for evaluation")
    evaluator_policy_version: str = Field(description="Semantic QA rubric/policy version")

    candidate_sha256: str = Field(description="SHA-256 digest of evaluated candidate asset")
    semantic_input_hash: str = Field(description="Stable deterministic hash of semantic inputs")


class VisualSemanticQAError(RuntimeError):
    """Raised when Visual Semantic QA encounters an unrecoverable failure in REQUIRED mode."""
    pass


class FrameSamplingError(VisualSemanticQAError):
    """Raised when candidate visual frame extraction fails."""
    pass


class FFmpegUnavailableError(FrameSamplingError):
    """Raised when ffmpeg or ffprobe binaries are not available."""
    error_code: str = "FFMPEG_UNAVAILABLE"


class FFprobeFailedError(FrameSamplingError):
    """Raised when ffprobe execution fails to inspect video file."""
    error_code: str = "FFPROBE_FAILED"


class InvalidVideoError(FrameSamplingError):
    """Raised when video file is missing, empty, or unreadable."""
    error_code: str = "INVALID_VIDEO"


class FrameExtractionFailedError(FrameSamplingError):
    """Raised when ffmpeg fails to decode/extract real visual frames."""
    error_code: str = "FRAME_SAMPLING_FAILED"

