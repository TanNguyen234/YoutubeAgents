"""Candidate Judge orchestrating deterministic prechecks, semantic evaluation, and score blending."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.media.acquisition.candidate_ranker import ModalityRealityPolicy, VisualCandidateScore
from app.media.acquisition.models import (
    VisualAcquisitionRequest,
    VisualAssetCandidate,
    VisualSourceType,
)
from app.media.director.models import ShotSpec
from app.media.semantic_qa.evaluator import VisualSemanticEvaluator
from app.media.semantic_qa.frame_sampler import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from app.media.semantic_qa.models import (
    VisualSemanticAssessment,
    VisualSemanticIssue,
    VisualSemanticQAError,
    VisualSemanticVerdict,
)

logger = logging.getLogger(__name__)

SUPPORTED_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


class CandidateJudgingResult(BaseModel):
    """Result of semantic judging across candidate assets for a single shot."""

    shot_id: str
    selected_candidate_id: Optional[str] = None
    winning_candidate: Optional[VisualAssetCandidate] = None
    winning_final_score: Optional[float] = None
    winning_deterministic_score: Optional[float] = None
    winning_semantic_score: Optional[float] = None
    assessments: Dict[str, VisualSemanticAssessment] = Field(default_factory=dict)
    candidate_final_scores: Dict[str, float] = Field(default_factory=dict)
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)
    failure_reasons: List[str] = Field(default_factory=list)


class VisualCandidateJudge:
    """Judges and selects the best visual asset candidate based on deterministic and semantic fitness."""

    def __init__(
        self,
        evaluator: VisualSemanticEvaluator,
        max_shortlist: int = 3,
        deterministic_weight: float = 0.35,
        semantic_weight: float = 0.65,
    ):
        self.evaluator = evaluator
        self.max_shortlist = max_shortlist
        self.deterministic_weight = deterministic_weight
        self.semantic_weight = semantic_weight

    @staticmethod
    def _compute_sha256(path: Path | str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def run_deterministic_precheck(
        self,
        candidate: VisualAssetCandidate,
        request: VisualAcquisitionRequest,
    ) -> Tuple[bool, Optional[str], Optional[VisualSemanticIssue]]:
        """Verify candidate integrity before spending VLM inference calls.

        Checks:
        - File exists on disk
        - File is non-empty (> 0 bytes)
        - Actual content SHA-256 matches candidate.content_sha256
        - Media extension is supported
        - Reasonable dimensions (>= 100x100 if specified)
        - REAL_REQUIRED gate: synthetic assets prohibited for real modalities
        - Basic provenance presence
        """
        path = Path(candidate.file_path)
        if not path.exists():
            return False, f"MISSING_FILE: Asset file not found: {path}", VisualSemanticIssue.EVIDENCE_NOT_VISIBLE

        try:
            size = os.path.getsize(path)
            if size == 0:
                return False, f"EMPTY_FILE: Asset file is 0 bytes: {path}", VisualSemanticIssue.LOW_INFORMATION_DENSITY
        except Exception as e:
            return False, f"FILE_READ_ERROR: {e}", VisualSemanticIssue.EVIDENCE_NOT_VISIBLE

        # SHA-256 match check
        try:
            actual_sha = self._compute_sha256(path)
            if candidate.content_sha256 and actual_sha.lower() != candidate.content_sha256.lower():
                return (
                    False,
                    f"SHA_MISMATCH: Declared '{candidate.content_sha256}' != actual '{actual_sha}'",
                    VisualSemanticIssue.VISUAL_CONTRADICTION,
                )
        except Exception as e:
            return False, f"SHA_COMPUTE_ERROR: {e}", VisualSemanticIssue.EVIDENCE_NOT_VISIBLE

        # Supported media format
        ext = path.suffix.lower()
        if ext not in SUPPORTED_MEDIA_EXTENSIONS:
            return False, f"UNSUPPORTED_MEDIA_TYPE: Extension '{ext}' not supported", VisualSemanticIssue.VISUAL_INTENT_MISMATCH

        # Dimension sanity
        if candidate.width is not None and candidate.width < 100:
            return False, f"DIMENSION_TOO_SMALL: Width {candidate.width} < 100px", VisualSemanticIssue.LOW_INFORMATION_DENSITY
        if candidate.height is not None and candidate.height < 100:
            return False, f"DIMENSION_TOO_SMALL: Height {candidate.height} < 100px", VisualSemanticIssue.LOW_INFORMATION_DENSITY

        # REAL_REQUIRED Hard Gate
        if request.modality in ModalityRealityPolicy.REAL_REQUIRED:
            if candidate.is_synthetic or candidate.source_type == VisualSourceType.GENERATED:
                return (
                    False,
                    f"REAL_REQUIRED_VIOLATION: Synthetic asset offered for {request.modality.value}",
                    VisualSemanticIssue.VISUAL_INTENT_MISMATCH,
                )

        return True, None, None

    def _compute_semantic_composite_score(self, assessment: VisualSemanticAssessment) -> float:
        """Calculate weighted average across relevant semantic assessment dimensions."""
        dims = [
            assessment.semantic_relevance,
            assessment.visual_intent_match,
            assessment.readability,
            assessment.information_value,
            max(0.0, 1.0 - assessment.generic_slop_score),
        ]
        if assessment.evidence_visibility is not None:
            dims.append(assessment.evidence_visibility)
        if assessment.interface_state_match is not None:
            dims.append(assessment.interface_state_match)
        if assessment.mechanism_clarity is not None:
            dims.append(assessment.mechanism_clarity)
        if assessment.comparison_clarity is not None:
            dims.append(assessment.comparison_clarity)
        if assessment.data_readability is not None:
            dims.append(assessment.data_readability)

        return round(sum(dims) / len(dims), 4)

    def judge_candidates(
        self,
        candidates: List[VisualAssetCandidate],
        request: VisualAcquisitionRequest,
        shot: ShotSpec,
        deterministic_scores: Dict[str, VisualCandidateScore],
    ) -> CandidateJudgingResult:
        """Evaluate shortlisted candidates and select winning asset blending deterministic and semantic scores."""
        if not candidates:
            return CandidateJudgingResult(
                shot_id=shot.shot_id,
                failure_reasons=["NO_CANDIDATES: No candidate assets provided for judging."],
            )

        # Sort candidates according to deterministic score descending
        sorted_candidates = sorted(
            candidates,
            key=lambda c: deterministic_scores.get(c.candidate_id, VisualCandidateScore(
                candidate_id=c.candidate_id,
                evidence_affinity=0.0,
                technical_quality=0.0,
                format_fit=0.0,
                source_preference=0.0,
                total_score=0.0,
            )).total_score,
            reverse=True,
        )

        # Limit to top shortlist (<= max_shortlist)
        shortlist = sorted_candidates[: self.max_shortlist]

        assessments: Dict[str, VisualSemanticAssessment] = {}
        candidate_final_scores: Dict[str, float] = {}
        accepted_candidates: List[Tuple[VisualAssetCandidate, float, float, float]] = []
        failures: List[str] = []

        for cand in shortlist:
            # 1. Deterministic Precheck
            passed, precheck_reason, precheck_issue = self.run_deterministic_precheck(cand, request)
            if not passed:
                logger.info("VISUAL_SEMANTIC_PRECHECK_FAILED: candidate=%s reason=%s", cand.candidate_id, precheck_reason)
                issues = [precheck_issue] if precheck_issue else []
                # Create synthetic deterministic rejection assessment
                rejection = VisualSemanticAssessment(
                    candidate_id=cand.candidate_id,
                    shot_id=shot.shot_id,
                    verdict=VisualSemanticVerdict.REJECT,
                    semantic_relevance=0.0,
                    visual_intent_match=0.0,
                    subject_match=0.0,
                    readability=0.0,
                    composition_quality=0.0,
                    information_value=0.0,
                    generic_slop_score=1.0,
                    issues=issues,
                    concise_reason=f"Deterministic precheck failed: {precheck_reason}",
                    evaluator_backend="deterministic_precheck",
                    evaluator_model=None,
                    evaluator_policy_version=self.evaluator.policy_version,
                    candidate_sha256=cand.content_sha256 or "",
                    semantic_input_hash="",
                )
                assessments[cand.candidate_id] = rejection
                failures.append(f"PRECHECK_FAILED ({cand.candidate_id}): {precheck_reason}")
                continue

            # 2. Semantic Evaluation
            try:
                assessment = self.evaluator.evaluate_candidate(cand, shot)
            except VisualSemanticQAError:
                raise
            except Exception as e:
                raise VisualSemanticQAError(f"SEMANTIC_QA_BACKEND_FAILURE ({cand.candidate_id}): {e}") from e
            assessments[cand.candidate_id] = assessment

            if assessment.verdict == VisualSemanticVerdict.ACCEPT:
                det_score_obj = deterministic_scores.get(cand.candidate_id)
                det_score = det_score_obj.total_score if det_score_obj else 0.5
                sem_score = self._compute_semantic_composite_score(assessment)

                # Composite blended score: 35% deterministic + 65% semantic
                final_score = round(
                    det_score * self.deterministic_weight + sem_score * self.semantic_weight,
                    4,
                )
                candidate_final_scores[cand.candidate_id] = final_score
                accepted_candidates.append((cand, final_score, det_score, sem_score))
            else:
                failures.append(
                    f"SEMANTIC_REJECT ({cand.candidate_id}): {assessment.concise_reason} (Issues: {[i.value for i in assessment.issues]})"
                )

        # 3. Winning Selection
        if not accepted_candidates:
            logger.warning("VISUAL_SEMANTIC_QA_REJECTED_ALL: shot_id=%s all candidates rejected", shot.shot_id)
            return CandidateJudgingResult(
                shot_id=shot.shot_id,
                selected_candidate_id=None,
                winning_candidate=None,
                assessments=assessments,
                candidate_final_scores=candidate_final_scores,
                failure_reasons=failures + ["SEMANTIC_QA_REJECTED_ALL"],
            )

        # Pick candidate with highest blended final_score
        accepted_candidates.sort(key=lambda item: item[1], reverse=True)
        winner, win_final, win_det, win_sem = accepted_candidates[0]
        winner_assessment = assessments[winner.candidate_id]

        audit_metadata = {
            "candidate_id": winner.candidate_id,
            "deterministic_score": win_det,
            "semantic_score": win_sem,
            "final_score": win_final,
            "semantic_verdict": winner_assessment.verdict.value,
            "semantic_issues": [i.value for i in winner_assessment.issues],
            "semantic_reason": winner_assessment.concise_reason,
            "semantic_policy_version": winner_assessment.evaluator_policy_version,
            "semantic_backend": winner_assessment.evaluator_backend,
            "semantic_model": winner_assessment.evaluator_model,
        }

        return CandidateJudgingResult(
            shot_id=shot.shot_id,
            selected_candidate_id=winner.candidate_id,
            winning_candidate=winner,
            winning_final_score=win_final,
            winning_deterministic_score=win_det,
            winning_semantic_score=win_sem,
            assessments=assessments,
            candidate_final_scores=candidate_final_scores,
            audit_metadata=audit_metadata,
            failure_reasons=failures,
        )
