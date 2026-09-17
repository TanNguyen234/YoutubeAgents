"""Domain models, enums, and typed contracts for visual acquisition and evidence sourcing."""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.media.director.models import EvidenceBinding, VisualIntent, VisualModality


class VisualSourceType(str, Enum):
    """Origin category of a visual media asset."""

    RESEARCH_SOURCE = "RESEARCH_SOURCE"
    DOCUMENT = "DOCUMENT"
    WEB_PAGE = "WEB_PAGE"
    LOCAL_WEB_APP = "LOCAL_WEB_APP"
    LOCAL_FILE = "LOCAL_FILE"
    CODE_OUTPUT = "CODE_OUTPUT"
    STOCK_MEDIA = "STOCK_MEDIA"
    GENERATED = "GENERATED"
    RENDERED = "RENDERED"
    FALLBACK_CARD = "FALLBACK_CARD"


class BrowserActionType(str, Enum):
    """Allowed deterministic browser automation actions."""

    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    WAIT_FOR = "WAIT_FOR"
    SCROLL = "SCROLL"
    SCREENSHOT = "SCREENSHOT"


class BrowserAction(BaseModel):
    """Typed browser interaction command (arbitrary JS execution is strictly prohibited)."""

    action_type: BrowserActionType = Field(description="Action primitive to execute")
    selector: Optional[str] = Field(default=None, description="CSS or text selector")
    text: Optional[str] = Field(default=None, description="Text to type or match")
    timeout_ms: int = Field(default=3000, ge=100, le=30000, description="Max execution timeout in ms")
    value: Optional[str] = Field(default=None, description="Optional parameter (e.g. scroll distance or URL)")


class VisualAcquisitionRequest(BaseModel):
    """Specification of what visual evidence or media must be acquired for a video shot."""

    project_id: str = Field(description="Associated project ID")
    shot_id: str = Field(description="Shot identifier (e.g. s_01_01)")
    modality: VisualModality = Field(description="Requested visual modality")
    visual_intent: VisualIntent = Field(description="Semantic visual purpose")
    subject: str = Field(description="Primary visual subject or concept")
    action: Optional[str] = Field(default=None, description="Visual action, state change, or mechanism")
    environment: Optional[str] = Field(default=None, description="Visual context or environment")
    query: Optional[str] = Field(default=None, description="Search query or keyword instruction")
    evidence_binding: Optional[EvidenceBinding] = Field(default=None, description="Grounded source evidence binding")
    source_refs: List[str] = Field(default_factory=list, description="Associated research source references")
    preferred_source_types: List[VisualSourceType] = Field(
        default_factory=list, description="Priority list of source origins"
    )
    target_width: int = Field(default=1080, ge=100)
    target_height: int = Field(default=1920, ge=100)
    duration_seconds: Optional[float] = Field(default=None, ge=0.1)
    target_url: Optional[str] = Field(default=None, description="Explicit trusted URL or local route")
    interaction_plan: List[BrowserAction] = Field(
        default_factory=list, description="Typed browser action plan for LOCAL_WEB_APP capture"
    )
    intentional_callback: bool = Field(
        default=False, description="Whether shot intentionally reuses an earlier visual asset"
    )


class VisualAssetCandidate(BaseModel):
    """An acquired or generated visual asset candidate eligible for scoring and selection."""

    candidate_id: str = Field(description="Unique candidate identifier")
    source_type: VisualSourceType = Field(description="Origin category of this candidate")
    file_path: str = Field(description="Local filesystem path to candidate image or video")
    source_url: Optional[str] = Field(default=None, description="Source URL if acquired from web/document")
    source_ref: Optional[str] = Field(default=None, description="ResearchSource ID if linked to evidence")
    license_type: Optional[str] = Field(default=None, description="License terms (e.g. CC-BY, Pexels, Proprietary)")
    attribution: Optional[str] = Field(default=None, description="Author or publisher attribution")
    content_sha256: str = Field(description="SHA-256 hash of candidate file")
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)
    duration_seconds: Optional[float] = Field(default=None, ge=0.0)
    acquisition_method: str = Field(description="Provider or service that produced this candidate")
    evidence_claim_ids: List[str] = Field(default_factory=list, description="Verified claim IDs proven by this asset")
    is_synthetic: bool = Field(default=False, description="Whether asset is AI-generated (GFlow/Veo/Imagen)")
    raw_metadata: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific metadata")
    actual_modality: Optional[VisualModality] = Field(default=None, description="Actual visual modality resolved for this candidate")


def resolve_candidate_actual_modality(
    candidate: VisualAssetCandidate,
    requested_modality: VisualModality,
) -> VisualModality:
    """Derive the actual visual modality of an acquired or rendered candidate asset."""
    if candidate.actual_modality:
        return candidate.actual_modality

    st = candidate.source_type
    if st in (VisualSourceType.RESEARCH_SOURCE, VisualSourceType.DOCUMENT, VisualSourceType.WEB_PAGE):
        return VisualModality.DOCUMENT_EVIDENCE
    if st == VisualSourceType.LOCAL_WEB_APP:
        return VisualModality.SCREEN_CAPTURE
    if st == VisualSourceType.FALLBACK_CARD:
        return VisualModality.STATIC_CARD
    if st == VisualSourceType.STOCK_MEDIA:
        return VisualModality.STOCK_VIDEO

    if st == VisualSourceType.RENDERED:
        meth = (candidate.acquisition_method or "").lower()
        if "diagram_renderer" in meth or "diagram" in meth:
            return VisualModality.DIAGRAM
        if "chart_renderer" in meth or "chart" in meth:
            return VisualModality.DATA_VISUALIZATION
        if "motion_renderer" in meth or "motion" in meth:
            return VisualModality.MOTION_GRAPHICS

        # Check explicit producer metadata in raw_metadata
        if candidate.raw_metadata and "modality" in candidate.raw_metadata:
            val = candidate.raw_metadata["modality"]
            if isinstance(val, VisualModality):
                return val
            if isinstance(val, str) and val in VisualModality.__members__:
                return VisualModality[val]

        if requested_modality in (
            VisualModality.DIAGRAM,
            VisualModality.STATIC_DIAGRAM,
            VisualModality.DATA_VISUALIZATION,
            VisualModality.STATIC_CHART,
            VisualModality.MOTION_GRAPHICS,
            VisualModality.CODE_ANIMATION,
            VisualModality.UI_SIMULATION,
            VisualModality.COMPARISON,
        ):
            return requested_modality
        return VisualModality.DIAGRAM

    if st == VisualSourceType.GENERATED:
        if candidate.raw_metadata and "modality" in candidate.raw_metadata:
            val = candidate.raw_metadata["modality"]
            if isinstance(val, VisualModality):
                return val
            if isinstance(val, str) and val in VisualModality.__members__:
                return VisualModality[val]

        meth = (candidate.acquisition_method or "").lower()
        if "video" in meth:
            return VisualModality.GENERATED_VIDEO
        if "image" in meth or "art" in meth:
            return VisualModality.GENERATED_IMAGE

        if requested_modality in (VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE):
            return requested_modality
        return VisualModality.GENERATED_IMAGE

    return requested_modality


class VisualAcquisitionResult(BaseModel):
    """Result of visual acquisition containing candidates, winning selection, and audit trail."""

    request: VisualAcquisitionRequest = Field(description="Input acquisition request")
    candidates: List[VisualAssetCandidate] = Field(default_factory=list, description="Acquired candidate assets")
    selected_candidate_id: Optional[str] = Field(default=None, description="ID of winning selected candidate")
    actual_modality: Optional[VisualModality] = Field(default=None, description="Actual visual modality resolved after acquisition/fallback")
    failure_reasons: List[str] = Field(default_factory=list, description="Reasons for failures during acquisition")
    semantic_audit: Dict[str, Any] = Field(default_factory=dict, description="Audit metadata from semantic QA candidate judging")

    @property
    def selected_candidate(self) -> Optional[VisualAssetCandidate]:
        """Return the winning candidate object if selected."""
        if not self.selected_candidate_id:
            return None
        for c in self.candidates:
            if c.candidate_id == self.selected_candidate_id:
                return c
        return None


class VisualAssetProvenance(BaseModel):
    """Immutable audit provenance record for a final selected visual asset."""

    shot_id: str = Field(description="Timeline shot identifier")
    asset_path: str = Field(description="Path to asset file")
    asset_sha256: str = Field(description="SHA-256 digest of asset file")
    source_type: VisualSourceType = Field(description="Origin category")
    source_url: Optional[str] = Field(default=None, description="Canonical source URL")
    source_ref: Optional[str] = Field(default=None, description="Internal ResearchSource ID")
    license_type: Optional[str] = Field(default=None, description="Usage license")
    attribution: Optional[str] = Field(default=None, description="Attribution text")
    evidence_claim_ids: List[str] = Field(default_factory=list, description="Linked verified claim IDs")
    acquisition_method: str = Field(description="Acquisition service/renderer name")
    synthetic: bool = Field(default=False, description="Whether asset contains AI synthetic media")
