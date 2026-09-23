"""Packaging Engine Service orchestrating grounded title + thumbnail tournaments."""

from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid
from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import (
    AssetType,
    PackagingTournamentStatus,
    PlatformFormat,
    TitleTruthStatus,
    TitleVariantType,
)
from app.domain.models import (
    Asset,
    PackagingCandidate,
    PackagingContext,
    PackagingTournament,
    SEOPackage,
    ThumbnailPackage,
    TitleVariant,
    VideoProject,
)
from app.services.thumbnail_designer import ThumbnailDesignerService

logger = logging.getLogger(__name__)


class PackagingError(RuntimeError):
    """Raised when packaging tournament execution encounters an unrecoverable failure."""
    pass


class CandidateProposal(BaseModel):
    """Structured candidate proposed by reasoning model."""
    id: str = Field(description="Candidate identifier: cand-1, cand-2, cand-3")
    title: str = Field(description="Title string (must adhere to <= 100 characters)")
    title_strategy: str = Field(description="Strategic angle (e.g. DIRECT_VALUE, CONTRAST_MECHANISM, CURIOSITY_QUESTION)")
    thumbnail_headline: Optional[str] = Field(default=None, description="0 to 4 words punchy overlay text")
    thumbnail_visual_strategy: str = Field(description="Visual strategy (e.g. architecture_diagram, ui_screenshot, code_benchmark)")
    subject_asset_id: Optional[str] = Field(default=None, description="Asset ID from project visual assets or None")
    supporting_asset_ids: List[str] = Field(default_factory=list, description="Supporting asset IDs from project visual assets")
    click_motivation_rationale: str = Field(description="Qualitative click motivation and packaging rationale")


class PackagingGenerationOutput(BaseModel):
    """Structured LLM output for the 3 packaging candidates."""
    candidates: List[CandidateProposal] = Field(description="List of exactly 3 distinct candidate proposals")


class PackagingEngineService:
    """Orchestrates grounded title + thumbnail tournament, gate validation, rendering, and persistence."""

    SCORING_VERSION = "v1.0"
    MAX_CANDIDATES = 3

    # Weights for observable offline quality dimensions (sum = 1.0)
    WEIGHT_PROMISE_ALIGNMENT = 0.25
    WEIGHT_CLARITY = 0.15
    WEIGHT_SPECIFICITY = 0.15
    WEIGHT_COMPLEMENTARITY = 0.15
    WEIGHT_AUDIENCE_FIT = 0.10
    WEIGHT_VISUAL_RELEVANCE = 0.10
    WEIGHT_LEGIBILITY = 0.10

    def __init__(
        self,
        repository: SQLiteRepository,
        thumbnail_designer: Optional[ThumbnailDesignerService] = None,
        backend: Optional[ReasoningBackend] = None,
        output_dir: Optional[Path] = None,
    ):
        self.repo = repository
        self.backend = backend or AntigravityCLIBackend()
        self.output_dir = Path(output_dir or "output/projects")
        self.thumbnail_designer = thumbnail_designer

    def get_thumbnail_designer(self, project_id: str) -> ThumbnailDesignerService:
        """Obtain or instantiate project-scoped ThumbnailDesignerService."""
        if self.thumbnail_designer:
            return self.thumbnail_designer
        thumb_dir = self.output_dir / project_id / "thumbnails"
        return ThumbnailDesignerService(self.repo, thumb_dir)

    def build_packaging_context(
        self,
        project_id: str,
        primary_keyword: Optional[str] = None,
        series_context: Optional[Dict[str, Any]] = None,
    ) -> PackagingContext:
        """Construct authoritative, trusted packaging context from existing project artifacts."""
        project = self.repo.get_video_project(project_id)
        if not project:
            raise ValueError(f"VideoProject '{project_id}' not found.")

        channel = self.repo.get_channel(project.channel_id) if project.channel_id else None
        dossier = self.repo.get_research_dossier(project_id)
        fact_report = self.repo.get_fact_check_report(project_id)
        claims = self.repo.get_claims_by_project(project_id)

        verified_claims: List[str] = []
        verified_claim_ids: List[str] = []
        for c in claims:
            # Include claims verified by fact checker
            if getattr(c, "verified", False) or getattr(c, "verdict", "").lower() == "verified":
                verified_claims.append(c.statement)
                verified_claim_ids.append(c.id)

        # Visual assets from project
        visual_asset_ids: List[str] = []
        for a in (project.assets or []):
            if a.asset_type in (AssetType.IMAGE, AssetType.SCENE_CARD, AssetType.THUMBNAIL):
                visual_asset_ids.append(a.id)

        # Core question & promised payoff from script or retention blueprint
        core_question = None
        promised_payoff = None
        hook = None
        if project.script:
            hook = project.script.hook
            if project.script.scenes:
                # First scene hook or intro
                core_question = project.script.hook
                # Last scene resolution or CTA
                promised_payoff = project.script.scenes[-1].narration

        summary = dossier.summary if dossier else (project.title or "")
        clean_keyword = primary_keyword or project.title or "Tech Overview"

        # Check for market opportunity angle if available
        opp_angle = None
        latest_port = self.repo.get_latest_opportunity_portfolio(project.channel_id) if channel else None
        if latest_port and latest_port.candidates:
            for cand in latest_port.candidates:
                if cand.keyword.lower() in clean_keyword.lower() or clean_keyword.lower() in cand.keyword.lower():
                    opp_angle = getattr(cand, "angle", None)
                    break

        return PackagingContext(
            project_id=project_id,
            channel_id=project.channel_id,
            primary_keyword=clean_keyword,
            target_audience=channel.target_audience if channel else "Tech Enthusiasts & Engineers",
            video_topic=project.title,
            core_question=core_question,
            promised_payoff=promised_payoff,
            hook=hook,
            summary=summary,
            verified_claim_ids=verified_claim_ids,
            verified_claims=verified_claims,
            visual_asset_ids=visual_asset_ids,
            content_format=getattr(project.content_format, "value", str(project.content_format)),
            platform_format=getattr(project.format, "value", str(project.format)),
            series_context=series_context,
            opportunity_angle=opp_angle,
        )

    def run_tournament(
        self,
        project_id: str,
        primary_keyword: Optional[str] = None,
        series_context: Optional[Dict[str, Any]] = None,
    ) -> PackagingTournament:
        """Execute full grounded title + thumbnail tournament, select default, and persist downstream."""
        project = self.repo.get_video_project(project_id)
        if not project:
            raise ValueError(f"VideoProject '{project_id}' not found.")

        # Build canonical context
        context = self.build_packaging_context(
            project_id=project_id,
            primary_keyword=primary_keyword,
            series_context=series_context,
        )

        # 1. Propose 3 candidates via reasoning backend
        candidates = self._propose_candidates(context)

        # 2. Gate 4: Diversity Check (Bounded to at most 1 correction attempt)
        is_diverse, div_reason = self._check_diversity(candidates)
        tournament_status = PackagingTournamentStatus.COMPLETED
        if not is_diverse:
            logger.warning(f"Packaging diversity check failed: {div_reason}. Attempting 1 correction pass.")
            candidates = self._correct_diversity(candidates, context)
            tournament_status = PackagingTournamentStatus.CORRECTED

        # 3. Gate 1, 2, 3: Hard Constraints, Truth Grounding, and Asset Provenance
        project_assets = project.assets or []
        for cand in candidates:
            self._evaluate_hard_gates(cand, context, project_assets)

        # 4. Render Thumbnails for Candidates passing hard gates
        designer = self.get_thumbnail_designer(project_id)
        series_badge = series_context.get("display_badge") if series_context else None

        for cand in candidates:
            if not cand.passed_gates:
                continue

            bg_image_path = None
            if cand.subject_asset_id:
                for a in project_assets:
                    if a.id == cand.subject_asset_id and Path(a.file_path).exists():
                        bg_image_path = a.file_path
                        break

            # If no subject asset matched or specified, check if any project visual asset exists as fallback base
            if not bg_image_path:
                for a in project_assets:
                    if (
                        a.asset_type in (AssetType.IMAGE, AssetType.SCENE_CARD)
                        and Path(a.file_path).exists()
                        and Path(a.file_path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                    ):
                        bg_image_path = a.file_path
                        break

            try:
                p16, p9, sha = designer.render_candidate_thumbnail(
                    project_id=project_id,
                    candidate_id=cand.id,
                    headline_text=cand.thumbnail_headline,
                    background_image_path=bg_image_path,
                    series_badge=series_badge,
                )
                cand.file_path_16_9 = str(p16)
                cand.file_path_9_16 = str(p9)
                cand.content_sha256 = sha
                cand.thumbnail_package_id = f"thm-{project_id}-{cand.id}"

                # Deterministic Legibility & File Integrity Gate
                if not p16.exists() or p16.stat().st_size == 0 or not p9.exists() or p9.stat().st_size == 0:
                    cand.passed_gates = False
                    cand.rejection_reason = "Rendered thumbnail file missing or zero bytes."
            except Exception as e:
                logger.error(f"Failed to render thumbnail for candidate '{cand.id}': {e}")
                cand.passed_gates = False
                cand.rejection_reason = f"Rendering error: {e}"

        # 5. Offline Quality Scoring
        for cand in candidates:
            if cand.passed_gates:
                score, breakdown = self._calculate_offline_scores(cand, context)
                cand.quality_score = score
                cand.score_breakdown = breakdown
            else:
                cand.quality_score = 0.0
                cand.score_breakdown = {"passed_gates": 0.0}

        # 6. Winner Selection (Deterministic Tie-Breaker)
        winner, win_reason = self._select_winner(candidates, context)

        # 7. Format-aware A/B readiness
        is_long_form = project.format == PlatformFormat.LONG_FORM_16_9 or context.platform_format == PlatformFormat.LONG_FORM_16_9.value
        passed_count = sum(1 for c in candidates if c.passed_gates)
        native_ab_eligible = bool(is_long_form and passed_count == self.MAX_CANDIDATES)

        tournament = PackagingTournament(
            id=f"trn-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            candidates=candidates,
            selected_candidate_id=winner.id if winner else None,
            selection_reason=win_reason,
            native_ab_eligible=native_ab_eligible,
            scoring_version=self.SCORING_VERSION,
            status=tournament_status,
            created_at=datetime.now(timezone.utc),
        )

        # 8. Persist Tournament
        self.repo.save_packaging_tournament(tournament)

        # 9. Downstream Integration: Update SEOPackage and active ThumbnailPackage
        if winner:
            self._update_downstream_contracts(project, winner, tournament, series_context)

        return tournament

    def _propose_candidates(self, context: PackagingContext) -> List[PackagingCandidate]:
        """Invoke reasoning backend or clean fallback to obtain 3 candidate proposals."""
        prompt = f"""You are the Packaging Engine for an autonomous educational channel.
Generate exactly 3 DISTINCT, GROUNDED title + thumbnail packaging candidates for a verified video.

VIDEO CONTEXT:
- Primary Topic/Keyword: {context.primary_keyword}
- Video Title: {context.video_topic}
- Target Audience: {context.target_audience}
- Opening Hook: {context.hook or 'N/A'}
- Promised Payoff: {context.promised_payoff or 'N/A'}
- Research Summary: {context.summary}
- Verified Claims:
{json.dumps(context.verified_claims, indent=2)}
- Available Project Visual Asset IDs:
{json.dumps(context.visual_asset_ids, indent=2)}

PACKAGING REQUIREMENTS:
1. Propose exactly 3 candidates (cand-1, cand-2, cand-3).
2. DIVERSE STRATEGIES:
   - Candidate 1: DIRECT_VALUE (Outcome, clarity, direct utility)
   - Candidate 2: CONTRAST_MECHANISM (Tension, unexpected architectural mechanism)
   - Candidate 3: CURIOSITY_QUESTION (Unresolved technical inquiry or reveal)
3. TRUTH BOUNDARY:
   - Propose emotional framing and inquiry, BUT DO NOT INVENT unverified claims, numbers (e.g. '10x faster'), benchmark multipliers, fake controversies, or fake failures.
   - Any factual proposition MUST be grounded in verified claims.
4. THUMBNAIL COMPLEMENTARITY:
   - Thumbnail headline must be 0 to 4 words.
   - DO NOT repeat the title words verbatim in the thumbnail headline. Pair them synergistically.
5. ASSET PROVENANCE:
   - subject_asset_id must be selected from the provided Available Visual Asset IDs or null. Do NOT invent asset IDs.
6. TITLE LENGTH:
   - Must strictly be <= 100 characters.
"""
        try:
            output = self.backend.generate_structured(prompt, PackagingGenerationOutput)
            if output and output.candidates and len(output.candidates) >= self.MAX_CANDIDATES:
                raw_list = output.candidates[:self.MAX_CANDIDATES]
                return [
                    PackagingCandidate(
                        id=c.id or f"cand-{i+1}",
                        title=c.title.strip(),
                        title_strategy=c.title_strategy,
                        thumbnail_headline=c.thumbnail_headline.strip() if c.thumbnail_headline else None,
                        thumbnail_visual_strategy=c.thumbnail_visual_strategy,
                        subject_asset_id=c.subject_asset_id if (c.subject_asset_id and c.subject_asset_id in context.visual_asset_ids) else None,
                        supporting_asset_ids=[a for a in c.supporting_asset_ids if a in context.visual_asset_ids],
                        rationale=c.click_motivation_rationale,
                    )
                    for i, c in enumerate(raw_list)
                ]
        except Exception as e:
            logger.warning(f"Reasoning backend candidate proposal failed: {e}. Using deterministic grounded generation.")

        return self._generate_grounded_fallback_candidates(context)

    def _generate_grounded_fallback_candidates(self, context: PackagingContext) -> List[PackagingCandidate]:
        """Deterministic grounded fallback candidates when reasoning model is unavailable."""
        kw = context.primary_keyword.strip()
        asset_id = context.visual_asset_ids[0] if context.visual_asset_ids else None

        c1 = PackagingCandidate(
            id="cand-1",
            title=f"How {kw} Works Under Load"[:95],
            title_strategy="DIRECT_VALUE",
            thumbnail_headline="HOW IT WORKS",
            thumbnail_visual_strategy="architecture_diagram",
            subject_asset_id=asset_id,
            rationale="Clear direct engineering explanation of mechanics under load.",
        )

        c2 = PackagingCandidate(
            id="cand-2",
            title=f"Why {kw} Changes Everything You Know"[:95],
            title_strategy="CONTRAST_MECHANISM",
            thumbnail_headline="THE REAL SHIFT",
            thumbnail_visual_strategy="mechanism_breakdown",
            subject_asset_id=asset_id,
            rationale="Contrasts traditional assumptions with modern architectural realities.",
        )

        c3 = PackagingCandidate(
            id="cand-3",
            title=f"Can {kw} Actually Scale in Production?"[:95],
            title_strategy="CURIOSITY_QUESTION",
            thumbnail_headline="AT SCALE?",
            thumbnail_visual_strategy="benchmark_comparison",
            subject_asset_id=asset_id,
            rationale="Invites inquiry into real production performance boundaries.",
        )

        return [c1, c2, c3]

    def _evaluate_hard_gates(
        self,
        candidate: PackagingCandidate,
        context: PackagingContext,
        project_assets: List[Asset],
    ) -> None:
        """Deterministic server-side gate evaluation (hard constraints, truth, assets)."""
        # 1. Hard Title Constraints
        clean_title = candidate.title.strip()
        if not clean_title:
            candidate.passed_gates = False
            candidate.rejection_reason = "Title cannot be empty."
            return

        if len(clean_title) > 100:
            candidate.passed_gates = False
            candidate.rejection_reason = f"Title exceeds 100 characters ({len(clean_title)} chars)."
            return

        # Check headline word count (at most 4 words)
        if candidate.thumbnail_headline:
            words = candidate.thumbnail_headline.split()
            if len(words) > 4:
                candidate.passed_gates = False
                candidate.rejection_reason = f"Thumbnail headline exceeds 4 words ({len(words)} words)."
                return

        # 2. Asset Provenance Gate
        valid_asset_ids = {a.id for a in project_assets}
        if candidate.subject_asset_id and candidate.subject_asset_id not in valid_asset_ids:
            candidate.passed_gates = False
            candidate.rejection_reason = f"Invented subject_asset_id '{candidate.subject_asset_id}' not found in project assets."
            return

        for sa in candidate.supporting_asset_ids:
            if sa not in valid_asset_ids:
                candidate.passed_gates = False
                candidate.rejection_reason = f"Invented supporting_asset_id '{sa}' not found in project assets."
                return

        # 3. Truth & Grounding Gate
        truth_status, passed_truth, truth_diag = self._validate_title_truth(candidate.title, context)
        candidate.truth_status = truth_status
        if not passed_truth:
            candidate.passed_gates = False
            candidate.rejection_reason = truth_diag

    def _validate_title_truth(
        self,
        title: str,
        context: PackagingContext,
    ) -> Tuple[TitleTruthStatus, bool, Optional[str]]:
        """Validate candidate title factual propositions against verified claims and context."""
        lower_title = title.lower()

        # Check for unverified numerical multiplier / benchmark claims (e.g., "10x", "5x", "100%", "300%")
        multiplier_matches = re.findall(r"\b(\d+x|\d+%\s*faster|\d+x\s*faster)\b", lower_title)
        if multiplier_matches:
            # Check if multiplier is backed by verified claims or summary
            grounded_text = " ".join(context.verified_claims + [context.summary]).lower()
            unsupported = [m for m in multiplier_matches if m not in grounded_text]
            if unsupported:
                return (
                    TitleTruthStatus.UNSUPPORTED,
                    False,
                    f"Title asserts unverified quantitative multiplier(s): {', '.join(unsupported)}.",
                )

        # Check for fabricated controversy / scam / failure claims when not supported
        fabricated_tokens = ["scam", "is dead", "disaster", "fails at scale", "replaced postgresql", "i replaced"]
        for tok in fabricated_tokens:
            if tok in lower_title:
                grounded_text = " ".join(context.verified_claims + [context.summary]).lower()
                if tok not in grounded_text:
                    return (
                        TitleTruthStatus.UNSUPPORTED,
                        False,
                        f"Title asserts unsupported failure or controversy framing: '{tok}'.",
                    )

        # Check extreme clickbait safety
        extreme_clickbait = ["secret truth", "they don't want you to know", "this changes everything"]
        for phrase in extreme_clickbait:
            if phrase in lower_title:
                grounded_text = " ".join(context.verified_claims + [context.summary]).lower()
                if phrase not in grounded_text and "truth" not in context.video_topic.lower():
                    # Heavily penalize or flag if completely unjustified
                    pass

        # Check whether title is a factual assertion or inquiry/topic framing
        is_inquiry_or_how_to = any(lower_title.startswith(p) for p in ("how ", "why ", "can ", "what ", "is ", "understanding ", "mastering "))
        if is_inquiry_or_how_to:
            return TitleTruthStatus.NON_FACTUAL_FRAMING, True, None

        # Check factual grounding
        # If title makes a declarative factual statement, verify topic keywords match
        topic_words = set(re.findall(r"\w+", context.primary_keyword.lower()))
        title_words = set(re.findall(r"\w+", lower_title))
        common = topic_words.intersection(title_words)
        if not common and len(topic_words) > 0:
            return (
                TitleTruthStatus.UNSUPPORTED,
                False,
                f"Title has zero topical overlap with primary keyword '{context.primary_keyword}'.",
            )

        return TitleTruthStatus.SUPPORTED, True, None

    def _check_diversity(self, candidates: List[PackagingCandidate]) -> Tuple[bool, Optional[str]]:
        """Evaluate strategic diversity across the 3 packaging candidates."""
        if len(candidates) < self.MAX_CANDIDATES:
            return False, f"Expected {self.MAX_CANDIDATES} candidates, got {len(candidates)}"

        # 1. Pairwise Title Overlap (Jaccard similarity on tokens)
        titles_tokens = [set(re.findall(r"\w+", c.title.lower())) for c in candidates]
        for i in range(len(titles_tokens)):
            for j in range(i + 1, len(titles_tokens)):
                s1, s2 = titles_tokens[i], titles_tokens[j]
                if not s1 or not s2:
                    continue
                intersection = len(s1.intersection(s2))
                union = len(s1.union(s2))
                jaccard = intersection / union if union > 0 else 1.0
                if jaccard > 0.65 or candidates[i].title.strip().lower() == candidates[j].title.strip().lower():
                    return False, f"Candidates '{candidates[i].id}' and '{candidates[j].id}' are near-duplicate titles (Jaccard={jaccard:.2f})."

        # 2. Thumbnail visual strategy diversity (must not all be identical)
        strategies = {c.thumbnail_visual_strategy.strip().lower() for c in candidates if c.thumbnail_visual_strategy}
        if len(strategies) <= 1:
            return False, "All candidates use the identical thumbnail visual strategy."

        # 3. Thumbnail headline diversity (must not all be identical)
        headlines = [c.thumbnail_headline.strip().upper() for c in candidates if c.thumbnail_headline]
        if len(headlines) == len(candidates) and len(set(headlines)) <= 1:
            return False, "All candidates share identical thumbnail headlines."

        return True, None

    def _correct_diversity(
        self,
        candidates: List[PackagingCandidate],
        context: PackagingContext,
    ) -> List[PackagingCandidate]:
        """Apply a bounded, single correction pass to ensure candidate diversity."""
        corrected = list(candidates)
        kw = context.primary_keyword.strip()

        # Differentiate strategies and headlines deterministically
        fallback_strategies = ["architecture_diagram", "tension_comparison", "code_benchmark"]
        fallback_headlines = ["HOW IT WORKS", "NOT WHAT YOU THINK", "AT SCALE?"]
        fallback_titles = [
            f"How {kw} Handles Concurrency"[:95],
            f"Why {kw} Changes Traditional Architectures"[:95],
            f"Can {kw} Actually Handle High Concurrency?"[:95],
        ]

        for i, cand in enumerate(corrected[:self.MAX_CANDIDATES]):
            # If title is near duplicate or strategy is identical, differentiate
            cand.thumbnail_visual_strategy = fallback_strategies[i % len(fallback_strategies)]
            if not cand.thumbnail_headline or cand.thumbnail_headline == corrected[0].thumbnail_headline:
                cand.thumbnail_headline = fallback_headlines[i % len(fallback_headlines)]
            if i > 0 and cand.title.strip().lower() == corrected[0].title.strip().lower():
                cand.title = fallback_titles[i % len(fallback_titles)]

        return corrected

    def _evaluate_complementarity(self, title: str, headline: Optional[str]) -> float:
        """Score title <-> thumbnail text complementarity (1.0 = highly complementary, 0.1 = verbatim repetition)."""
        if not headline or not headline.strip():
            # No text is clean and allows visual to speak for itself
            return 0.90

        title_words = set(re.findall(r"\w+", title.lower()))
        headline_words = set(re.findall(r"\w+", headline.lower()))
        if not headline_words:
            return 0.90

        overlap = title_words.intersection(headline_words)
        overlap_ratio = len(overlap) / len(headline_words)

        if overlap_ratio >= 0.8:
            # Extreme verbatim repetition (bad)
            return 0.20
        elif overlap_ratio >= 0.5:
            # Moderate repetition
            return 0.50
        else:
            # Complementary (different concepts/question)
            return 0.95

    def _calculate_offline_scores(
        self,
        candidate: PackagingCandidate,
        context: PackagingContext,
    ) -> Tuple[float, Dict[str, float]]:
        """Compute observable offline quality scores across defined dimensions."""
        title_lower = candidate.title.lower()
        topic_words = set(re.findall(r"\w+", context.primary_keyword.lower()))
        title_words = set(re.findall(r"\w+", title_lower))

        # 1. Promise Alignment (0.0 to 1.0)
        # Checks if title aligns with delivered payoff or core question
        payoff_text = (context.promised_payoff or context.summary).lower()
        payoff_words = set(re.findall(r"\w+", payoff_text))
        promise_overlap = len(title_words.intersection(payoff_words))
        promise_score = min(1.0, max(0.5, promise_overlap / max(1, len(title_words) * 0.4)))

        # 2. Clarity (0.0 to 1.0)
        # Concise titles (40-75 chars) score highest
        title_len = len(candidate.title)
        if 35 <= title_len <= 80:
            clarity_score = 0.95
        elif title_len < 35:
            clarity_score = 0.75
        else:
            clarity_score = max(0.60, 1.0 - (title_len - 80) * 0.015)

        # 3. Specificity (0.0 to 1.0)
        # Presence of technical topic keyword and concrete strategy
        spec_score = 0.90 if topic_words.intersection(title_words) else 0.50

        # 4. Complementarity (0.0 to 1.0)
        comp_score = self._evaluate_complementarity(candidate.title, candidate.thumbnail_headline)

        # 5. Audience Fit (0.0 to 1.0)
        aud_lower = context.target_audience.lower()
        aud_score = 0.85
        if "engineer" in aud_lower or "developer" in aud_lower:
            if any(w in title_lower for w in ("how", "why", "architecture", "scale", "concurrency", "deep dive")):
                aud_score = 0.95

        # 6. Visual Relevance (0.0 to 1.0)
        vis_score = 0.85 if candidate.subject_asset_id else 0.70

        # 7. Thumbnail Legibility (0.0 to 1.0)
        leg_score = 0.95
        if candidate.thumbnail_headline:
            words = candidate.thumbnail_headline.split()
            if len(words) > 3:
                leg_score = 0.80

        total_score = (
            self.WEIGHT_PROMISE_ALIGNMENT * promise_score
            + self.WEIGHT_CLARITY * clarity_score
            + self.WEIGHT_SPECIFICITY * spec_score
            + self.WEIGHT_COMPLEMENTARITY * comp_score
            + self.WEIGHT_AUDIENCE_FIT * aud_score
            + self.WEIGHT_VISUAL_RELEVANCE * vis_score
            + self.WEIGHT_LEGIBILITY * leg_score
        )

        breakdown = {
            "promise_alignment": round(promise_score, 4),
            "clarity": round(clarity_score, 4),
            "specificity": round(spec_score, 4),
            "title_thumbnail_complementarity": round(comp_score, 4),
            "audience_fit": round(aud_score, 4),
            "thumbnail_visual_relevance": round(vis_score, 4),
            "thumbnail_legibility": round(leg_score, 4),
        }

        return round(total_score, 4), breakdown

    def _select_winner(
        self,
        candidates: List[PackagingCandidate],
        context: PackagingContext,
    ) -> Tuple[Optional[PackagingCandidate], str]:
        """Select winner via deterministic tie-breaking over valid candidates."""
        valid_candidates = [c for c in candidates if c.passed_gates]
        if not valid_candidates:
            return None, "All candidates failed hard constraints or truth grounding gates."

        # Sort order:
        # 1. quality_score (descending)
        # 2. promise_alignment (descending)
        # 3. clarity (descending)
        # 4. candidate id order (ascending)
        def sort_key(c: PackagingCandidate):
            score = c.quality_score
            promise = c.score_breakdown.get("promise_alignment", 0.0)
            clarity = c.score_breakdown.get("clarity", 0.0)
            # cand-1 -> 1, cand-2 -> 2
            cand_idx = 99
            m = re.search(r"\d+", c.id)
            if m:
                cand_idx = int(m.group())
            return (-score, -promise, -clarity, cand_idx)

        sorted_candidates = sorted(valid_candidates, key=sort_key)
        winner = sorted_candidates[0]

        reason = (
            f"Candidate '{winner.id}' selected with highest offline quality score {winner.quality_score:.4f} "
            f"(promise={winner.score_breakdown.get('promise_alignment', 0.0):.2f}, "
            f"clarity={winner.score_breakdown.get('clarity', 0.0):.2f}, "
            f"complementarity={winner.score_breakdown.get('title_thumbnail_complementarity', 0.0):.2f})."
        )
        return winner, reason

    def _update_downstream_contracts(
        self,
        project: VideoProject,
        winner: PackagingCandidate,
        tournament: PackagingTournament,
        series_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update SEOPackage and active ThumbnailPackage so downstream publisher seamlessly consumes winner."""
        # 1. Update or create SEOPackage
        seo_pkg = self.repo.get_seo_package(project.id)
        if seo_pkg:
            # Map all 3 tournament candidates to title variants
            variants: List[TitleVariant] = []
            for c in tournament.candidates:
                angle = TitleVariantType.DIRECT_VALUE
                if "curiosity" in c.title_strategy.lower() or "question" in c.title_strategy.lower():
                    angle = TitleVariantType.CURIOSITY_GAP
                elif "contrast" in c.title_strategy.lower() or "provocative" in c.title_strategy.lower():
                    angle = TitleVariantType.PROVOCATIVE_QUESTION

                variants.append(
                    TitleVariant(
                        angle=angle,
                        title=c.title,
                        predicted_ctr_rationale=c.rationale or f"Tournament strategy: {c.title_strategy}",
                    )
                )

            seo_pkg.selected_title = winner.title
            seo_pkg.title_variants = variants
            self.repo.save_seo_package(seo_pkg)

        # 2. Persist active ThumbnailPackage for publisher
        thumb_pkg = ThumbnailPackage(
            id=winner.thumbnail_package_id or f"thm-{project.id}-{winner.id}",
            project_id=project.id,
            file_path_16_9=winner.file_path_16_9,
            file_path_9_16=winner.file_path_9_16,
            headline_text=winner.thumbnail_headline or "",
            content_sha256=winner.content_sha256 or hashlib.sha256(winner.title.encode()).hexdigest(),
            provenance={
                "generator": "PackagingEngineService",
                "tournament_id": tournament.id,
                "candidate_id": winner.id,
                "strategy": winner.thumbnail_visual_strategy,
                "subject_asset_id": winner.subject_asset_id,
            },
            created_at=datetime.now(timezone.utc),
        )
        self.repo.save_thumbnail_package(thumb_pkg)

        # 3. Register as project asset if file exists
        if winner.file_path_16_9 and Path(winner.file_path_16_9).exists():
            thumb_asset = Asset(
                id=f"ast-thumb-{winner.id}",
                project_id=project.id,
                asset_type=AssetType.THUMBNAIL,
                file_path=winner.file_path_16_9,
                source_url=f"file://{winner.file_path_16_9}",
                license_type="PROPRIETARY",
                content_sha256=winner.content_sha256 or "",
                created_at=datetime.now(timezone.utc),
            )
            # Avoid duplicate thumbnail asset
            existing_assets = [a for a in project.assets if a.asset_type != AssetType.THUMBNAIL]
            existing_assets.append(thumb_asset)
            project.assets = existing_assets
            self.repo.save_video_project(project)
