"""Deterministic policy and domain models for Selective Visual Retry and Reacquisition."""

from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.media.director.models import ShotSpec, VisualModality
from app.media.semantic_qa.models import VisualSemanticIssue

logger = logging.getLogger(__name__)

MAX_SEMANTIC_CORRECTIVE_RETRIES_PER_SHOT = 1

UNRETRYABLE_FAILURE_KEYWORDS = {
    "UNTRUSTED_VISUAL_SOURCE",
    "UNTRUSTED_SOURCE_URL",
    "PRIVATE_IP_BLOCKED",
    "DISALLOWED_SCHEME",
    "DNS_RESOLUTION_FAILED",
    "VISUAL_ASSET_SHA_MISMATCH",
    "MISSING_FILE",
    "EMPTY_FILE",
    "UNSUPPORTED_MEDIA_TYPE",
    "REAL_REQUIRED_VIOLATION",
    "INTEGRITY_CHECK_FAILED",
    "SECURITY_VIOLATION",
    "HTTP_ERROR",
    "SEMANTIC_QA_BACKEND_FAILURE",
    "SEMANTIC_QA_OUTPUT_IDENTITY_MISMATCH",
    "FFMPEG_UNAVAILABLE",
    "FFPROBE_FAILED",
    "INVALID_VIDEO",
    "FRAME_SAMPLING_FAILED",
}


class VisualRetryAction(str, Enum):
    """Deterministic corrective action to recover from semantic rejection."""

    NO_RETRY = "NO_RETRY"
    REACQUIRE = "REACQUIRE"
    REGENERATE = "REGENERATE"
    RERENDER = "RERENDER"
    SWITCH_TO_DIAGRAM = "SWITCH_TO_DIAGRAM"


class VisualRetryDecision(BaseModel):
    """Structured decision output from the deterministic retry policy."""

    shot_id: str
    original_candidate_id: Optional[str] = None
    original_modality: VisualModality
    action: VisualRetryAction
    issues: List[VisualSemanticIssue] = Field(default_factory=list)
    reason: str
    attempt_index: int
    target_modality: Optional[VisualModality] = None
    corrected_instruction: Optional[str] = None
    duplicate_output_detected: bool = False


class VisualRetryPolicy:
    """Evaluates semantic QA rejections and selects bounded corrective actions deterministically."""

    @staticmethod
    def is_trust_or_integrity_failure(
        failure_reasons: Optional[List[str]] = None,
        concise_reason: Optional[str] = None,
    ) -> bool:
        """Check if rejection was caused by security, integrity, or provider unreliability."""
        all_reasons = list(failure_reasons or [])
        if concise_reason:
            all_reasons.append(concise_reason)

        for text in all_reasons:
            for kw in UNRETRYABLE_FAILURE_KEYWORDS:
                if kw in text:
                    return True
        return False

    @classmethod
    def evaluate_decision(
        cls,
        shot: ShotSpec,
        actual_modality: VisualModality,
        issues: List[VisualSemanticIssue],
        concise_reason: str = "",
        attempt_index: int = 0,
        original_candidate_id: Optional[str] = None,
        candidate_sha: Optional[str] = None,
        failure_reasons: Optional[List[str]] = None,
        provider_capabilities: Optional[Dict[str, Any]] = None,
    ) -> VisualRetryDecision:
        """Determine single bounded corrective action from candidate actual modality and semantic issues."""
        # 1. Enforce strict attempt budget: at most 1 corrective retry (attempt_index 0 -> 1)
        if attempt_index >= MAX_SEMANTIC_CORRECTIVE_RETRIES_PER_SHOT:
            return VisualRetryDecision(
                shot_id=shot.shot_id,
                original_candidate_id=original_candidate_id,
                original_modality=actual_modality,
                action=VisualRetryAction.NO_RETRY,
                issues=issues,
                reason=f"Retry budget exhausted: attempt_index {attempt_index} >= max {MAX_SEMANTIC_CORRECTIVE_RETRIES_PER_SHOT}",
                attempt_index=attempt_index,
            )

        # 2. Hard Security & Trust Boundary: Reliability/integrity failures must NEVER be retried semantically
        if cls.is_trust_or_integrity_failure(failure_reasons=failure_reasons, concise_reason=concise_reason):
            return VisualRetryDecision(
                shot_id=shot.shot_id,
                original_candidate_id=original_candidate_id,
                original_modality=actual_modality,
                action=VisualRetryAction.NO_RETRY,
                issues=issues,
                reason="Trust, security, or integrity failure cannot be retried semantically.",
                attempt_index=attempt_index,
            )

        # 3. Decision mapping by candidate actual modality
        # CASE A: GENERATED_IMAGE or GENERATED_VIDEO
        if actual_modality in (VisualModality.GENERATED_IMAGE, VisualModality.GENERATED_VIDEO):
            actionable_issues = {
                VisualSemanticIssue.SUBJECT_MISMATCH,
                VisualSemanticIssue.ACTION_MISMATCH,
                VisualSemanticIssue.VISUAL_INTENT_MISMATCH,
                VisualSemanticIssue.VISUAL_CONTRADICTION,
                VisualSemanticIssue.GENERATED_TEXT_ARTIFACT,
                VisualSemanticIssue.WEAK_COMPOSITION,
                VisualSemanticIssue.LOW_INFORMATION_DENSITY,
                VisualSemanticIssue.VISUAL_CLUTTER,
            }
            matching = [i for i in issues if i in actionable_issues]
            if matching or issues:
                corrected_prompt = cls._build_corrected_generation_prompt(shot, issues)
                return VisualRetryDecision(
                    shot_id=shot.shot_id,
                    original_candidate_id=original_candidate_id,
                    original_modality=actual_modality,
                    action=VisualRetryAction.REGENERATE,
                    issues=issues,
                    reason=f"Regenerating {actual_modality.value} with stricter corrective prompt targeting {[i.value for i in issues]}",
                    attempt_index=attempt_index,
                    target_modality=actual_modality,
                    corrected_instruction=corrected_prompt,
                )

        # CASE B: DIAGRAM or STATIC_DIAGRAM
        elif actual_modality in (VisualModality.DIAGRAM, VisualModality.STATIC_DIAGRAM):
            actionable_issues = {
                VisualSemanticIssue.MECHANISM_NOT_EXPLAINED,
                VisualSemanticIssue.VISUAL_INTENT_MISMATCH,
                VisualSemanticIssue.SUBJECT_MISMATCH,
                VisualSemanticIssue.LOW_INFORMATION_DENSITY,
            }
            matching = [i for i in issues if i in actionable_issues]
            if matching or issues:
                corrected_diag = cls._build_corrected_diagram_instruction(shot, issues)
                return VisualRetryDecision(
                    shot_id=shot.shot_id,
                    original_candidate_id=original_candidate_id,
                    original_modality=actual_modality,
                    action=VisualRetryAction.RERENDER,
                    issues=issues,
                    reason=f"Rerendering diagram with explicit directional sequence targeting {[i.value for i in issues]}",
                    attempt_index=attempt_index,
                    target_modality=VisualModality.DIAGRAM,
                    corrected_instruction=corrected_diag,
                )

        # CASE E: STOCK_VIDEO (Generic or Intent Mismatch -> Switch to Diagram)
        elif actual_modality == VisualModality.STOCK_VIDEO:
            actionable_issues = {
                VisualSemanticIssue.GENERIC_STOCK,
                VisualSemanticIssue.VISUAL_INTENT_MISMATCH,
                VisualSemanticIssue.SUBJECT_MISMATCH,
                VisualSemanticIssue.ACTION_MISMATCH,
                VisualSemanticIssue.DECORATIVE_ONLY,
            }
            matching = [i for i in issues if i in actionable_issues]
            if matching or issues:
                diagram_spec = cls._build_corrected_diagram_instruction(shot, issues)
                return VisualRetryDecision(
                    shot_id=shot.shot_id,
                    original_candidate_id=original_candidate_id,
                    original_modality=actual_modality,
                    action=VisualRetryAction.SWITCH_TO_DIAGRAM,
                    issues=issues,
                    reason=f"Switching rejected stock media to technical diagram targeting {[i.value for i in issues]}",
                    attempt_index=attempt_index,
                    target_modality=VisualModality.DIAGRAM,
                    corrected_instruction=diagram_spec,
                )

        # CASE C: DOCUMENT_EVIDENCE
        elif actual_modality in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT):
            # Check if an alternative excerpt anchor is available
            binding = shot.evidence_binding
            if binding and binding.claim_text and binding.claim_text != binding.source_excerpt:
                # Targeted reacquisition using alternative claim anchor
                return VisualRetryDecision(
                    shot_id=shot.shot_id,
                    original_candidate_id=original_candidate_id,
                    original_modality=actual_modality,
                    action=VisualRetryAction.REACQUIRE,
                    issues=issues,
                    reason="Reacquiring document evidence with alternative claim anchor.",
                    attempt_index=attempt_index,
                    target_modality=actual_modality,
                    corrected_instruction=binding.claim_text,
                )
            # If no alternative targeting mechanism exists, capturing identical page is a no-op: reject!
            return VisualRetryDecision(
                shot_id=shot.shot_id,
                original_candidate_id=original_candidate_id,
                original_modality=actual_modality,
                action=VisualRetryAction.NO_RETRY,
                issues=issues,
                reason="Document evidence lacks an alternative region-targeting mechanism (no-op retry rejected).",
                attempt_index=attempt_index,
            )

        # CASE D: SCREEN_CAPTURE
        elif actual_modality == VisualModality.SCREEN_CAPTURE:
            # Check if interaction plan or alternative plan exists
            caps = provider_capabilities or {}
            if caps.get("has_alternate_interaction"):
                return VisualRetryDecision(
                    shot_id=shot.shot_id,
                    original_candidate_id=original_candidate_id,
                    original_modality=actual_modality,
                    action=VisualRetryAction.REACQUIRE,
                    issues=issues,
                    reason="Reacquiring screen capture with alternate interaction plan.",
                    attempt_index=attempt_index,
                    target_modality=actual_modality,
                )
            return VisualRetryDecision(
                shot_id=shot.shot_id,
                original_candidate_id=original_candidate_id,
                original_modality=actual_modality,
                action=VisualRetryAction.NO_RETRY,
                issues=issues,
                reason="Screen capture lacks an alternate interaction plan (no-op retry rejected).",
                attempt_index=attempt_index,
            )

        # CASE F: DATA_VISUALIZATION (Unreadable data/labels)
        elif actual_modality in (VisualModality.DATA_VISUALIZATION, VisualModality.STATIC_CHART):
            # Chart renderer has fixed font sizing and no dynamic layout controls: do not fake retry!
            return VisualRetryDecision(
                shot_id=shot.shot_id,
                original_candidate_id=original_candidate_id,
                original_modality=actual_modality,
                action=VisualRetryAction.NO_RETRY,
                issues=issues,
                reason="Chart renderer does not support dynamic label resizing or layout modification (no-op retry rejected).",
                attempt_index=attempt_index,
            )

        # Default: no eligible corrective policy
        return VisualRetryDecision(
            shot_id=shot.shot_id,
            original_candidate_id=original_candidate_id,
            original_modality=actual_modality,
            action=VisualRetryAction.NO_RETRY,
            issues=issues,
            reason="No eligible corrective retry policy for modality and issues.",
            attempt_index=attempt_index,
        )

    @staticmethod
    def _build_corrected_generation_prompt(shot: ShotSpec, issues: List[VisualSemanticIssue]) -> str:
        """Construct stricter generation prompt using only existing trusted shot context and issues."""
        base_prompt = shot.generation_prompt or shot.narration_segment or shot.subject or "Technical concept"
        directives = []

        if VisualSemanticIssue.ACTION_MISMATCH in issues:
            if shot.action:
                directives.append(
                    f"The previous candidate failed ACTION_MISMATCH. Explicitly show the active motion: '{shot.action}'. "
                    "Do not depict a stationary, passive, or generic scene."
                )
            else:
                directives.append(
                    "The previous candidate failed ACTION_MISMATCH. Emphasize visible movement, state change, and concrete action."
                )

        if VisualSemanticIssue.SUBJECT_MISMATCH in issues:
            if shot.subject:
                directives.append(
                    f"The previous candidate failed SUBJECT_MISMATCH. Prominently center and clearly feature '{shot.subject}'. "
                    "Ensure the primary subject is immediately recognizable."
                )
            else:
                directives.append(
                    "The previous candidate failed SUBJECT_MISMATCH. Clearly feature the primary technical subject without distracting elements."
                )

        if VisualSemanticIssue.VISUAL_INTENT_MISMATCH in issues:
            intent_val = shot.visual_intent.value if shot.visual_intent else "SHOW_MECHANISM"
            directives.append(
                f"The previous candidate failed VISUAL_INTENT_MISMATCH. Fulfill visual purpose: '{intent_val}' aligned with context: '{shot.narration_segment[:60]}'."
            )

        if VisualSemanticIssue.GENERATED_TEXT_ARTIFACT in issues:
            directives.append(
                "Do not generate any visible text, pseudo-letters, numbers, labels, or watermarks. Avoid all text artifacts entirely."
            )

        if VisualSemanticIssue.VISUAL_CONTRADICTION in issues:
            directives.append(
                f"Ensure visual elements strictly adhere to factual context: '{shot.narration_segment[:60]}'. Do not introduce contradictory details."
            )

        if VisualSemanticIssue.GENERIC_STOCK in issues or VisualSemanticIssue.DECORATIVE_ONLY in issues:
            directives.append(
                "Avoid generic stock tropes, decorative abstractions, or vague concepts. Focus on specific technical hardware and concrete execution."
            )

        if not directives:
            directives.append(
                f"Refined composition: Fulfill '{shot.subject or 'technical concept'}' showing '{shot.action or 'action'}' with high clarity and balanced framing."
            )

        directives.append("Avoid decorative clutter, text watermarks, and generic filler.")
        return f"{base_prompt}. [CORRECTIVE RETRY: {' '.join(directives)}]"

    @staticmethod
    def _build_corrected_diagram_instruction(shot: ShotSpec, issues: List[VisualSemanticIssue]) -> str:
        """Construct explicit directional sequence for DiagramRenderer (using -> connecting nodes)."""
        base = shot.diagram_instruction or shot.narration_segment or shot.subject or "System Flow"
        subj = shot.subject or "Client Request"
        act = shot.action or "Processing Engine"

        # If base already has clean directional arrows, keep and clarify
        if "->" in base or "→" in base:
            return f"Directional mechanism flow: {base}. Use labeled arrows and clear node sequence."

        # Derive clean technical sequence from subject and action
        return (
            f"Directional mechanism flow: Input Source: {subj} -> Processing Pipeline: {act} -> "
            f"State Verification: Log & Index -> Final Output. Use labeled directional arrows."
        )
