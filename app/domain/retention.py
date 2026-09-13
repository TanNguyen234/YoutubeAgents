"""Domain contracts for format-aware narrative retention evaluation and pacing audits."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.domain.enums import (
    ConcreteAnchorType,
    PsychologicalMechanism,
    RetentionCueType,
)


class DropRisk(BaseModel):
    """Identified viewer drop-off hazard with severity and suggested remedy."""

    start_ratio: float = Field(ge=0.0, le=1.0, description="Normalized starting position of risk")
    end_ratio: float = Field(ge=0.0, le=1.0, description="Normalized ending position of risk")
    reason: str = Field(description="Diagnostic reason for drop risk")
    severity: str = Field(description="HIGH, MEDIUM, or LOW")
    rewrite_hint: Optional[str] = Field(default=None, description="Actionable hint for script rewrite")


class OpenLoopAudit(BaseModel):
    """Audit record for a narrative open loop (curiosity gap or unanswered question)."""

    question: str = Field(description="The open loop question or curiosity gap")
    opened_at_ratio: float = Field(ge=0.0, le=1.0, description="Where loop was initiated")
    closed_at_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Where loop was resolved")
    resolved: bool = Field(default=False, description="Whether open loop reached payoff")


class ConcreteAnchorAudit(BaseModel):
    """Audit record distinguishing grounded concrete proof/demonstration from textual cues."""

    anchor_type: ConcreteAnchorType = Field(description="Concrete anchor archetype")
    scene_index: int = Field(ge=0, description="0-indexed scene containing anchor")
    text: str = Field(description="Extracted anchor text or phrase")
    grounded: bool = Field(default=False, description="Whether anchor is verified against evidence/claims")
    claim_ids: List[str] = Field(default_factory=list, description="Associated verified claim IDs")
    source_refs: List[str] = Field(default_factory=list, description="Associated research source references/URLs")


class RetentionMoment(BaseModel):
    """Strong positive retention milestone detected in script."""

    position_ratio: float = Field(ge=0.0, le=1.0, description="Normalized script position ratio")
    timestamp_seconds: Optional[float] = Field(
        default=None, ge=0.0, description="Mapped timestamp in seconds from real audio"
    )
    type: RetentionCueType
    reason: str
    psychological_mechanism: PsychologicalMechanism = Field(
        default=PsychologicalMechanism.CURIOSITY_GAP,
        description="Descriptive audience engagement mechanism",
    )
    narration_anchor: Optional[str] = Field(
        default=None,
        description="Spoken narration text or exact phrase anchor used for TTS timestamp matching",
    )


class ScriptRetentionReport(BaseModel):
    """Heuristic quality report assessing narrative retention and pacing.

    NOTE: These scores are internal heuristics, NOT real or predicted YouTube analytics.
    """

    passed: bool = Field(description="Whether script satisfies retention quality gate")
    hook_quality_score: float = Field(ge=0.0, le=1.0, description="Hook retention heuristic score")
    progression_score: float = Field(ge=0.0, le=1.0, description="Narrative progression heuristic score")
    payoff_alignment_score: float = Field(ge=0.0, le=1.0, description="Promise vs payoff alignment heuristic score")
    drop_risks: List[DropRisk] = Field(default_factory=list)
    open_loops: List[OpenLoopAudit] = Field(default_factory=list)
    strongest_moments: List[RetentionMoment] = Field(default_factory=list)
    concrete_anchors: List[ConcreteAnchorAudit] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    rewrite_instructions: List[str] = Field(default_factory=list)

    def populate_timestamps(
        self,
        total_duration_seconds: float,
        timing_events: Optional[List[Dict[str, Any]]] = None,
        canonical_narration: Optional[str] = None,
    ) -> "ScriptRetentionReport":
        """Populate actual timestamps on retention moments after real TTS."""
        self.strongest_moments = map_retention_moments_to_timestamps(
            moments=self.strongest_moments,
            total_duration_seconds=total_duration_seconds,
            timing_events=timing_events,
            canonical_narration=canonical_narration,
        )
        return self


def map_retention_moments_to_timestamps(
    moments: List[RetentionMoment],
    total_duration_seconds: float,
    timing_events: Optional[List[Dict[str, Any]]] = None,
    canonical_narration: Optional[str] = None,
) -> List[RetentionMoment]:
    """Map retention moments to actual timestamps after real TTS audio duration and word boundaries.

    Timestamp matching priority:
    1. narration_anchor + timing events
    2. nearest exact word/phrase span in canonical_narration
    3. ratio-based fallback (position_ratio * total_duration_seconds)
    Never uses RetentionMoment.reason as a timing source.
    """
    if total_duration_seconds <= 0.0 or not moments:
        return moments

    updated_moments: List[RetentionMoment] = []
    for m in moments:
        matched_time = None
        anchor_text = (m.narration_anchor or "").strip()

        # 1. narration_anchor + timing events
        if anchor_text and timing_events:
            anchor_words = [w.lower() for w in re.findall(r"\b\w+\b", anchor_text) if len(w) > 1]
            if anchor_words:
                for w in anchor_words:
                    for evt in timing_events:
                        w_evt = (evt.get("word") or evt.get("text") or "").strip().lower()
                        w_clean = re.sub(r"[^\w\s]", "", w_evt)
                        if w_clean == w:
                            if "start" in evt:
                                matched_time = float(evt["start"])
                            elif "offset" in evt:
                                matched_time = round(float(evt["offset"]) / 10_000_000.0, 3)
                            break
                    if matched_time is not None:
                        break

        # 2. Nearest exact word/phrase span in canonical_narration
        if matched_time is None and anchor_text and canonical_narration and len(canonical_narration) > 0:
            idx = canonical_narration.lower().find(anchor_text.lower()[:30])
            if idx >= 0:
                ratio = idx / len(canonical_narration)
                matched_time = round(ratio * total_duration_seconds, 3)

        # 3. Ratio-based calculation fallback
        if matched_time is None:
            matched_time = round(m.position_ratio * total_duration_seconds, 3)

        clamped_time = max(0.0, min(total_duration_seconds, matched_time))
        updated_moments.append(
            RetentionMoment(
                position_ratio=m.position_ratio,
                timestamp_seconds=clamped_time,
                type=m.type,
                reason=m.reason,
                psychological_mechanism=m.psychological_mechanism,
                narration_anchor=m.narration_anchor,
            )
        )
    return updated_moments
