"""Deterministic candidate ranker and anti-slop visual selection engine."""

from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.media.acquisition.models import (
    VisualAcquisitionRequest,
    VisualAssetCandidate,
    VisualSourceType,
)
from app.media.director.models import VisualModality


class ModalityRealityPolicy:
    """Classifies visual modalities according to their strictness for real vs synthetic sourcing."""

    REAL_REQUIRED: Set[VisualModality] = {
        VisualModality.DOCUMENT_EVIDENCE,
        VisualModality.SCREENSHOT,
        VisualModality.SCREEN_CAPTURE,
    }

    REAL_PREFERRED: Set[VisualModality] = {
        VisualModality.CODE_ANIMATION,
        VisualModality.DATA_VISUALIZATION,
        VisualModality.UI_SIMULATION,
    }

    REAL_OR_SYNTHETIC: Set[VisualModality] = {
        VisualModality.DIAGRAM,
        VisualModality.COMPARISON,
        VisualModality.TIMELINE,
        VisualModality.MAP,
        VisualModality.STATIC_DIAGRAM,
        VisualModality.STATIC_CHART,
        VisualModality.STATIC_TERMINAL,
    }

    SYNTHETIC_ALLOWED: Set[VisualModality] = {
        VisualModality.GENERATED_IMAGE,
        VisualModality.GENERATED_VIDEO,
        VisualModality.IMAGE_TO_VIDEO,
        VisualModality.MOTION_GRAPHICS,
        VisualModality.KINETIC_TYPOGRAPHY,
        VisualModality.STATIC_CARD,
    }


class VisualCandidateScore(BaseModel):
    """Deterministic score breakdown for a visual asset candidate."""

    candidate_id: str = Field(description="Candidate identifier")
    evidence_affinity: float = Field(ge=0.0, le=1.0, description="Correlation with grounded factual evidence")
    technical_quality: float = Field(ge=0.0, le=1.0, description="Resolution and dimensions score")
    format_fit: float = Field(ge=0.0, le=1.0, description="Aspect ratio and duration compatibility")
    source_preference: float = Field(ge=0.0, le=1.0, description="Source origin priority weight")
    penalties: List[str] = Field(default_factory=list, description="Deductions applied (e.g. duplicates, synthetic slop)")
    penalty_sum: float = Field(default=0.0, ge=0.0, description="Total deductions applied")
    total_score: float = Field(description="Final computed ranking score (higher is better)")


class CandidateRanker:
    """Ranks acquired visual candidates deterministically based on evidence, technical fit, and reality policy."""

    def __init__(self):
        self.recently_used_hashes: List[str] = []
        self.recently_used_urls: List[str] = []
        self.recent_modalities: List[VisualModality] = []

    def record_selection(
        self,
        candidate: VisualAssetCandidate,
        modality: VisualModality,
    ) -> None:
        """Update recent history window to track repetition and prevent duplicate visual overuse."""
        if candidate.content_sha256:
            self.recently_used_hashes.append(candidate.content_sha256)
            if len(self.recently_used_hashes) > 10:
                self.recently_used_hashes.pop(0)

        if candidate.source_url:
            self.recently_used_urls.append(candidate.source_url)
            if len(self.recently_used_urls) > 10:
                self.recently_used_urls.pop(0)

        self.recent_modalities.append(modality)
        if len(self.recent_modalities) > 10:
            self.recent_modalities.pop(0)

    def score_candidate(
        self,
        candidate: VisualAssetCandidate,
        request: VisualAcquisitionRequest,
    ) -> VisualCandidateScore:
        """Compute transparent deterministic score for an individual candidate."""
        penalties: List[str] = []
        penalty_deduction = 0.0

        # 1. Source Preference Weight
        source_weights: Dict[VisualSourceType, float] = {
            VisualSourceType.RESEARCH_SOURCE: 1.0,
            VisualSourceType.DOCUMENT: 1.0,
            VisualSourceType.WEB_PAGE: 0.95,
            VisualSourceType.LOCAL_WEB_APP: 0.90,
            VisualSourceType.LOCAL_FILE: 0.85,
            VisualSourceType.CODE_OUTPUT: 0.85,
            VisualSourceType.RENDERED: 0.75,
            VisualSourceType.STOCK_MEDIA: 0.55,
            VisualSourceType.GENERATED: 0.40,
            VisualSourceType.FALLBACK_CARD: 0.10,
        }
        source_pref = source_weights.get(candidate.source_type, 0.5)

        # 2. Evidence Affinity
        evidence_affinity = 0.0
        if request.evidence_binding:
            # Check if candidate links to verified evidence binding
            if candidate.source_url and request.evidence_binding.source_url:
                if candidate.source_url.strip().lower() == request.evidence_binding.source_url.strip().lower():
                    evidence_affinity = 1.0
                elif request.evidence_binding.source_url.strip().lower() in candidate.source_url.strip().lower():
                    evidence_affinity = 0.9
            elif candidate.source_ref and request.evidence_binding.source_ref:
                if candidate.source_ref == request.evidence_binding.source_ref:
                    evidence_affinity = 1.0

            if candidate.evidence_claim_ids and request.evidence_binding.claim_id:
                if request.evidence_binding.claim_id in candidate.evidence_claim_ids:
                    evidence_affinity = max(evidence_affinity, 1.0)
        else:
            # If shot does not require evidence, neutrality
            evidence_affinity = 0.5 if candidate.source_type != VisualSourceType.FALLBACK_CARD else 0.1

        # 3. Technical Quality & Resolution Fit
        tech_quality = 0.8
        target_w = request.target_width or 1080
        target_h = request.target_height or 1920
        if candidate.width and candidate.height:
            if candidate.width >= target_w and candidate.height >= target_h:
                tech_quality = 1.0
            elif candidate.width >= 640 and candidate.height >= 360:
                tech_quality = 0.8
            else:
                tech_quality = 0.5
                penalties.append("LOW_RESOLUTION: Asset resolution below standard HD")
                penalty_deduction += 0.15

        # 4. Format Fit (Aspect ratio & Duration)
        format_fit = 0.8
        if candidate.width and candidate.height:
            cand_ratio = candidate.width / candidate.height
            target_ratio = target_w / target_h
            drift = abs(cand_ratio - target_ratio)
            if drift < 0.05:
                format_fit = 1.0
            elif drift < 0.25:
                format_fit = 0.85
            else:
                format_fit = 0.6
                penalties.append("ASPECT_RATIO_POOR_FIT: Aspect ratio deviates significantly from 9:16 portrait")
                penalty_deduction += 0.10

        # Duration fit if video
        if candidate.duration_seconds is not None and request.duration_seconds is not None:
            if candidate.duration_seconds >= request.duration_seconds * 0.8:
                format_fit = min(1.0, format_fit + 0.1)
            else:
                penalties.append("DURATION_SHORT: Video duration shorter than shot requirements")
                penalty_deduction += 0.15

        # 5. Policy-based Penalties
        # Reality policy penalty
        modality = request.modality
        if modality in ModalityRealityPolicy.REAL_REQUIRED:
            if candidate.is_synthetic or candidate.source_type == VisualSourceType.GENERATED:
                penalties.append("SYNTHETIC_ON_REAL_REQUIRED: Synthetic candidate offered for REAL_REQUIRED modality")
                penalty_deduction += 0.80
        elif modality in ModalityRealityPolicy.REAL_PREFERRED:
            if candidate.is_synthetic:
                penalties.append("SYNTHETIC_ON_REAL_PREFERRED: Synthetic candidate offered for REAL_PREFERRED modality")
                penalty_deduction += 0.35

        # Duplicate asset penalty (unless intentional callback)
        if not request.intentional_callback:
            if candidate.content_sha256 in self.recently_used_hashes:
                penalties.append(f"DUPLICATE_ASSET: Asset hash '{candidate.content_sha256[:8]}' recently used")
                penalty_deduction += 0.50

            if candidate.source_url and candidate.source_url in self.recently_used_urls[-2:]:
                penalties.append(f"DUPLICATE_SOURCE_URL: Source URL '{candidate.source_url[:30]}' used in consecutive shots")
                penalty_deduction += 0.25

            # Modality overuse penalty: if same modality was used in the last 2 consecutive shots
            if len(self.recent_modalities) >= 2 and all(m == modality for m in self.recent_modalities[-2:]):
                penalties.append(f"MODALITY_OVERUSE: Modality '{modality.value}' repeated > 2 consecutive shots")
                penalty_deduction += 0.20

        # Composite total score
        raw_score = (
            evidence_affinity * 0.35
            + source_pref * 0.30
            + tech_quality * 0.20
            + format_fit * 0.15
        )
        final_score = max(0.0, round(raw_score - penalty_deduction, 4))

        return VisualCandidateScore(
            candidate_id=candidate.candidate_id,
            evidence_affinity=round(evidence_affinity, 4),
            technical_quality=round(tech_quality, 4),
            format_fit=round(format_fit, 4),
            source_preference=round(source_pref, 4),
            penalties=penalties,
            penalty_sum=round(penalty_deduction, 4),
            total_score=final_score,
        )

    def rank_candidates(
        self,
        candidates: List[VisualAssetCandidate],
        request: VisualAcquisitionRequest,
    ) -> List[Tuple[VisualAssetCandidate, VisualCandidateScore]]:
        """Score and sort candidates from highest to lowest deterministic score."""
        scored = []
        for c in candidates:
            score = self.score_candidate(c, request)
            scored.append((c, score))

        # Sort descending by total_score
        scored.sort(key=lambda item: item[1].total_score, reverse=True)
        return scored
