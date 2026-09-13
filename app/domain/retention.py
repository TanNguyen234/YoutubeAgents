"""Domain contracts for format-aware narrative retention evaluation and pacing audits."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

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
    valid_retention_anchor: bool = Field(
        default=False, description="Whether anchor is a valid retention illustration"
    )
    evidence_grounded: bool = Field(
        default=False, description="Whether anchor is verified against factual evidence/claims"
    )
    claim_ids: List[str] = Field(default_factory=list, description="Associated verified claim IDs")
    source_refs: List[str] = Field(default_factory=list, description="Associated research source references/URLs")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_grounded(cls, data: Any) -> Any:
        if isinstance(data, dict) and "grounded" in data:
            old_grounded = bool(data.get("grounded"))
            if "valid_retention_anchor" not in data:
                data["valid_retention_anchor"] = old_grounded
            if "evidence_grounded" not in data:
                claim_ids = data.get("claim_ids") or []
                source_refs = data.get("source_refs") or []
                data["evidence_grounded"] = old_grounded and bool(claim_ids or source_refs)
            data.pop("grounded", None)
        return data

    @property
    def grounded(self) -> bool:
        """Backward-compatibility property mapping to valid_retention_anchor."""
        return self.valid_retention_anchor


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


class WordTiming(BaseModel):
    """Normalized word timing token representation."""

    token: str
    start_seconds: float
    end_seconds: Optional[float] = None


def parse_word_timings(timing_events: Optional[List[Dict[str, Any]]]) -> List[WordTiming]:
    """Parse various TTS timing event formats into normalized WordTiming instances."""
    if not timing_events:
        return []
    parsed: List[WordTiming] = []
    for evt in timing_events:
        raw_text = evt.get("word") if "word" in evt else evt.get("text")
        if raw_text is None:
            continue
        clean_token = re.sub(r"[^\w]", "", str(raw_text).strip().lower())
        if not clean_token:
            continue

        start_sec = None
        if "start_time" in evt:
            start_sec = float(evt["start_time"])
        elif "start" in evt:
            start_sec = float(evt["start"])
        elif "offset" in evt:
            start_sec = round(float(evt["offset"]) / 10_000_000.0, 3)

        if start_sec is None:
            continue

        end_sec = None
        if "end_time" in evt:
            end_sec = float(evt["end_time"])
        elif "end" in evt:
            end_sec = float(evt["end"])
        elif "offset" in evt and "duration" in evt:
            end_sec = round((float(evt["offset"]) + float(evt["duration"])) / 10_000_000.0, 3)

        parsed.append(WordTiming(token=clean_token, start_seconds=start_sec, end_seconds=end_sec))
    return parsed


def find_nearest_phrase_timestamp(
    anchor_text: str,
    expected_time: float,
    timing_events: Optional[List[Dict[str, Any]]] = None,
    canonical_narration: Optional[str] = None,
    total_duration_seconds: float = 0.0,
) -> Optional[float]:
    """Align narration anchor against nearest TTS timing phrase or fallback sequence.

    Matching hierarchy:
    1. Full contiguous anchor phrase
    2. Longest meaningful contiguous token span (>= 2 tokens, or 1 if anchor only has 1)
    3. Canonical narration location mapped proportionally
    4. None (caller falls back to position_ratio * total_duration_seconds)
    """
    clean_anchor = (anchor_text or "").strip()
    if not clean_anchor:
        return None

    words = [re.sub(r"[^\w]", "", w.lower()) for w in re.findall(r"\b\w+\b", clean_anchor)]
    anchor_tokens = [w for w in words if w]

    word_timings = parse_word_timings(timing_events)
    if anchor_tokens and word_timings:
        event_tokens = [wt.token for wt in word_timings]
        m_len = len(anchor_tokens)
        n_len = len(event_tokens)

        # 1. Full contiguous match
        full_matches = []
        for i in range(n_len - m_len + 1):
            if event_tokens[i : i + m_len] == anchor_tokens:
                full_matches.append(word_timings[i].start_seconds)

        if full_matches:
            return min(full_matches, key=lambda t: abs(t - expected_time))

        # 2. Longest meaningful contiguous subsequence
        min_subseq_len = 1 if m_len == 1 else 2
        for sub_len in range(m_len - 1, min_subseq_len - 1, -1):
            sub_matches = []
            for s_idx in range(m_len - sub_len + 1):
                sub_slice = anchor_tokens[s_idx : s_idx + sub_len]
                for i in range(n_len - sub_len + 1):
                    if event_tokens[i : i + sub_len] == sub_slice:
                        sub_matches.append(word_timings[i].start_seconds)

            if sub_matches:
                return min(sub_matches, key=lambda t: abs(t - expected_time))

    # 3. Canonical narration proportional mapping
    if canonical_narration and len(canonical_narration) > 0 and total_duration_seconds > 0.0:
        search_target = clean_anchor.lower()[:30]
        idx = canonical_narration.lower().find(search_target)
        if idx >= 0:
            return round((idx / len(canonical_narration)) * total_duration_seconds, 3)

    return None


def map_retention_moments_to_timestamps(
    moments: List[RetentionMoment],
    total_duration_seconds: float,
    timing_events: Optional[List[Dict[str, Any]]] = None,
    canonical_narration: Optional[str] = None,
) -> List[RetentionMoment]:
    """Map retention moments to actual timestamps after real TTS audio duration and word boundaries.

    Timestamp matching priority:
    1. Full contiguous narration_anchor phrase
    2. Longest meaningful contiguous token span
    3. Canonical narration location mapped proportionally
    4. position_ratio * total_duration_seconds
    Never uses RetentionMoment.reason as a timing source.
    """
    if total_duration_seconds <= 0.0 or not moments:
        return moments

    updated_moments: List[RetentionMoment] = []
    for m in moments:
        expected_time = m.position_ratio * total_duration_seconds
        anchor_text = (m.narration_anchor or "").strip()

        matched_time = None
        if anchor_text:
            matched_time = find_nearest_phrase_timestamp(
                anchor_text=anchor_text,
                expected_time=expected_time,
                timing_events=timing_events,
                canonical_narration=canonical_narration,
                total_duration_seconds=total_duration_seconds,
            )

        # 4. Ratio-based calculation fallback
        if matched_time is None:
            matched_time = round(expected_time, 3)

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
