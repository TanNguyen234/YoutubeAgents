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
    id: Optional[str] = Field(default=None, description="Optional candidate identifier from model (ignored by server: canonical server IDs are cand-1, cand-2, cand-3)")
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


class TitleProposition(BaseModel):
    """Individual proposition extracted from a candidate title."""
    model_config = {"extra": "forbid"}
    text: str = Field(description="Concrete factual proposition asserted or presupposed by title, if any.")
    factual: bool = Field(description="True if asserting a concrete factual claim; False if pure editorial framing.")
    supporting_claim_ids: List[str] = Field(default_factory=list, description="IDs of verified claims supporting this proposition.")


class TitleGroundingEvaluation(BaseModel):
    """Structured semantic evaluation of title factual grounding."""
    model_config = {"extra": "forbid"}
    propositions: List[TitleProposition] = Field(default_factory=list, description="Propositions asserted or presupposed in title")
    topic_framing_only: bool = Field(default=False, description="True if title is pure educational/topic framing without factual claims.")
    rationale: str = Field(default="", description="Fact-checking reasoning for title grounding evaluation")


class PackagingEngineService:
    """Orchestrates grounded title + thumbnail tournament, gate validation, rendering, and persistence."""

    SCORING_VERSION = "v1.0"
    MAX_CANDIDATES = 3

    # Weights for observable offline quality dimensions (sum = 1.0, scale 0.0 to 1.0)
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
            if getattr(c, "verified", False) or getattr(c, "verdict", "").lower() == "verified":
                verified_claims.append(c.statement)
                verified_claim_ids.append(c.id)

        # Visual assets from project
        visual_asset_ids: List[str] = []
        for a in (project.assets or []):
            if a.asset_type in (AssetType.IMAGE, AssetType.SCENE_CARD, AssetType.THUMBNAIL):
                visual_asset_ids.append(a.id)

        core_question = None
        promised_payoff = None
        hook = None
        if project.script:
            hook = project.script.hook
            if project.script.scenes:
                core_question = project.script.hook
                promised_payoff = project.script.scenes[-1].narration

        summary = dossier.summary if dossier else (project.title or "")
        clean_keyword = primary_keyword or project.title or "Tech Overview"

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

        # 2. Gate 4: Diversity Check (Bounded to at most 1 correction attempt; fail closed if unresolved)
        is_diverse, div_reason = self._check_diversity(candidates)
        tournament_status = PackagingTournamentStatus.COMPLETED
        if not is_diverse:
            logger.warning(f"Packaging diversity check failed: {div_reason}. Attempting 1 correction pass.")
            candidates = self._correct_diversity(candidates, context)
            rechecked_diverse, rechecked_reason = self._check_diversity(candidates)
            if rechecked_diverse:
                tournament_status = PackagingTournamentStatus.CORRECTED
            else:
                raise PackagingError(f"PACKAGING_DIVERSITY_UNRESOLVED: {rechecked_reason}")

        # Defense-in-depth: Candidate IDs must strictly match canonical positional IDs
        expected_ids = ["cand-1", "cand-2", "cand-3"]
        actual_ids = [c.id for c in candidates]
        if actual_ids != expected_ids:
            raise PackagingError(f"Candidate IDs must strictly match canonical positional IDs {expected_ids}, got {actual_ids}")

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

            if not bg_image_path:
                for a in project_assets:
                    if (
                        a.asset_type in (AssetType.IMAGE, AssetType.SCENE_CARD)
                        and Path(a.file_path).exists()
                        and Path(a.file_path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                    ):
                        bg_image_path = a.file_path
                        break

            supporting_paths = [
                str(a.file_path)
                for a in project_assets
                if a.id in cand.supporting_asset_ids and Path(a.file_path).exists()
            ]

            try:
                render_res = designer.render_candidate_thumbnail(
                    project_id=project_id,
                    candidate_id=cand.id,
                    headline_text=cand.thumbnail_headline,
                    background_image_path=bg_image_path,
                    series_badge=series_badge,
                    visual_strategy=cand.thumbnail_visual_strategy,
                    supporting_image_paths=supporting_paths,
                )
                cand.file_path_16_9 = str(render_res.file_path_16_9)
                cand.file_path_9_16 = str(render_res.file_path_9_16)
                cand.content_sha256 = render_res.content_sha256
                cand.requested_visual_strategy = cand.thumbnail_visual_strategy
                cand.actual_visual_strategy = render_res.actual_visual_strategy
                cand.visual_fallback_reason = render_res.visual_fallback_reason

                p16 = render_res.file_path_16_9
                p9 = render_res.file_path_9_16
                if not p16.exists() or p16.stat().st_size == 0 or not p9.exists() or p9.stat().st_size == 0:
                    cand.passed_gates = False
                    cand.rejection_reason = "Rendered thumbnail file missing or zero bytes."
                elif not cand.content_sha256:
                    cand.passed_gates = False
                    cand.rejection_reason = "Missing SHA-256 for rendered thumbnail."
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
        channel = self.repo.get_channel(project.channel_id) if project.channel_id else None
        is_made_for_kids = bool(channel and getattr(channel, "made_for_kids", False))

        is_long_form = project.format == PlatformFormat.LONG_FORM_16_9 or context.platform_format == PlatformFormat.LONG_FORM_16_9.value
        passed_count = sum(1 for c in candidates if c.passed_gates)
        native_ab_eligible = bool(is_long_form and passed_count == self.MAX_CANDIDATES and not is_made_for_kids)

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
                        id=f"cand-{i+1}",
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
        """Conservative deterministic fallback candidates based on known inputs."""
        kw = context.primary_keyword.strip()
        asset_id = context.visual_asset_ids[0] if context.visual_asset_ids else None

        c1 = PackagingCandidate(
            id="cand-1",
            title=f"Understanding {kw}"[:95],
            title_strategy="DIRECT_VALUE",
            thumbnail_headline="UNDERSTANDING",
            thumbnail_visual_strategy="FOCUS",
            subject_asset_id=asset_id,
            rationale="Clear direct engineering explanation of mechanics.",
        )

        c2 = PackagingCandidate(
            id="cand-2",
            title=f"How {kw} Works"[:95],
            title_strategy="CONTRAST_MECHANISM",
            thumbnail_headline="HOW IT WORKS",
            thumbnail_visual_strategy="SPLIT_CONTRAST",
            subject_asset_id=asset_id,
            rationale="Detailed breakdown of fundamental operational mechanics.",
        )

        c3 = PackagingCandidate(
            id="cand-3",
            title=f"Key Concepts in {kw}"[:95],
            title_strategy="CURIOSITY_QUESTION",
            thumbnail_headline="KEY CONCEPTS",
            thumbnail_visual_strategy="DETAIL_CROP",
            subject_asset_id=asset_id,
            rationale="Inquiry into architectural structure and trade-offs.",
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
        truth_status, passed_truth, truth_diag, claim_id = self._validate_title_truth(candidate.title, context)
        candidate.truth_status = truth_status
        candidate.grounding_claim_id = claim_id
        if not passed_truth:
            candidate.passed_gates = False
            candidate.rejection_reason = truth_diag

    def _validate_title_truth(
        self,
        title: str,
        context: PackagingContext,
    ) -> Tuple[TitleTruthStatus, bool, Optional[str], Optional[str]]:
        """Validate candidate title factual propositions against verified claims and context.

        Returns (status, passed, reason, valid_supporting_claim_id).
        """
        lower_title = title.lower()

        # 1. Deterministic Fast-Fail Checks
        # Unverified numerical multiplier / benchmark claims (e.g., "10x", "5x", "100%", "300%")
        multiplier_matches = re.findall(r"\b(\d+x|\d+%\s*faster|\d+x\s*faster)\b", lower_title)
        if multiplier_matches:
            grounded_text = " ".join(context.verified_claims + [context.summary]).lower()
            unsupported = [m for m in multiplier_matches if m not in grounded_text]
            if unsupported:
                return (
                    TitleTruthStatus.UNSUPPORTED,
                    False,
                    f"Title asserts unverified quantitative multiplier(s): {', '.join(unsupported)}.",
                    None,
                )

        # Fabricated controversy / scam / failure claims
        fabricated_tokens = ["scam", "is dead", "disaster", "fails at scale", "replaced postgresql", "i replaced"]
        for tok in fabricated_tokens:
            if tok in lower_title:
                grounded_text = " ".join(context.verified_claims + [context.summary]).lower()
                if tok not in grounded_text:
                    return (
                        TitleTruthStatus.UNSUPPORTED,
                        False,
                        f"Title asserts unsupported failure or controversy framing: '{tok}'.",
                        None,
                    )

        # 2. Semantic Claim Grounding via Reasoning Backend
        claims_data = []
        for cid, ctxt in zip(context.verified_claim_ids, context.verified_claims):
            claims_data.append({"id": cid, "statement": ctxt})

        grounding_prompt = f"""You are the Title Fact-Verification Judge.
Evaluate the factual propositions asserted or presupposed by this candidate title against the verified claims.

CANDIDATE TITLE: "{title}"
PRIMARY TOPIC: "{context.primary_keyword}"
VIDEO SUMMARY: "{context.summary}"

VERIFIED CLAIMS (ONLY VALID FACTUAL AUTHORITY):
{json.dumps(claims_data, indent=2)}

INSTRUCTIONS:
1. Determine if the title is purely educational or topic framing without concrete factual propositions (e.g. "Understanding {context.primary_keyword}", "How {context.primary_keyword} Works").
   If so, set topic_framing_only=True and propositions=[].
2. If the title asserts or presupposes a factual proposition (e.g. "{context.primary_keyword} Prevents Database Corruption", "Why {context.primary_keyword} Eliminates All Write Locks", "How {context.primary_keyword} Lets Reads Continue During Writes"):
   - Extract the proposition into TitleProposition(text=..., factual=True).
   - If a verified claim supports or semantically entails this proposition, include its claim ID in supporting_claim_ids.
   - If NO verified claim supports it, leave supporting_claim_ids empty.
3. Questions with factual presuppositions (e.g. "Why X does Y") MUST be treated as asserting that X does Y.
4. DO NOT invent supporting claim IDs. Only reference valid IDs provided above.
"""

        try:
            eval_output = self.backend.generate_structured(grounding_prompt, TitleGroundingEvaluation)
            if eval_output:
                # Case A: Topic framing only
                if eval_output.topic_framing_only and not any(p.factual for p in eval_output.propositions):
                    # Sanity check: Ensure no ungrounded factual claims crept into framing
                    unverified_keywords = ["corruption", "eliminates all", "zero lock", "eliminates lock"]
                    all_claims_text = " ".join(context.verified_claims).lower()
                    if any(uk in lower_title for uk in unverified_keywords) and not any(uk in all_claims_text for uk in unverified_keywords):
                        return (
                            TitleTruthStatus.UNSUPPORTED,
                            False,
                            f"Title asserts unverified factual proposition: '{title}'.",
                            None,
                        )
                    return TitleTruthStatus.NON_FACTUAL_FRAMING, True, None, None

                # Case B & C: Evaluate propositions
                has_factual = False
                first_valid_claim_id = None
                for prop in eval_output.propositions:
                    if prop.factual:
                        has_factual = True
                        # Server-side authority validation: Must be in context.verified_claim_ids
                        valid_ids = [cid for cid in prop.supporting_claim_ids if cid in context.verified_claim_ids]
                        if not valid_ids:
                            return (
                                TitleTruthStatus.UNSUPPORTED,
                                False,
                                f"Title asserts unverified factual proposition '{prop.text}' without verified claim support.",
                                None,
                            )
                        if first_valid_claim_id is None:
                            first_valid_claim_id = valid_ids[0]

                if has_factual:
                    return TitleTruthStatus.SUPPORTED, True, None, first_valid_claim_id
                else:
                    return TitleTruthStatus.NON_FACTUAL_FRAMING, True, None, None

        except Exception as e:
            logger.warning(f"Backend semantic grounding evaluation failed for '{title}': {e}")
            # Conservative fallback: only safe topic framing passes without backend grounding
            clean_title_lower = title.strip().lower()
            kw_lower = context.primary_keyword.strip().lower()
            safe_framings = {
                f"understanding {kw_lower}",
                f"how {kw_lower} works",
                f"{kw_lower} explained",
                f"what is {kw_lower}",
                f"a guide to {kw_lower}",
                f"guide to {kw_lower}",
            }
            if clean_title_lower in safe_framings:
                return TitleTruthStatus.NON_FACTUAL_FRAMING, True, None, None

            # Fail closed for factual assertions without verification
            return (
                TitleTruthStatus.UNSUPPORTED,
                False,
                f"Semantic grounding evaluation unavailable for candidate title '{title}'.",
                None,
            )

        return TitleTruthStatus.SUPPORTED, True, None, None

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
                if jaccard >= 0.70 or candidates[i].title.strip().lower() == candidates[j].title.strip().lower():
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
        # 1. Ask reasoning backend for a diverse re-proposal
        prompt = f"""You previously proposed packaging candidates that are too similar and failed the diversity check.
Please provide 3 distinctly diverse title + thumbnail candidate proposals for:
TOPIC: {context.primary_keyword}
TARGET AUDIENCE: {context.target_audience}
SUMMARY: {context.summary}

PREVIOUS CANDIDATES (FAILED DIVERSITY CHECK):
{[c.title for c in candidates]}

REQUIREMENTS FOR DIVERSITY CORRECTION:
1. Completely distinct title angles (DIRECT_VALUE, CONTRAST_MECHANISM, CURIOSITY_QUESTION)
2. Different visual strategies (FOCUS, SPLIT_CONTRAST, DETAIL_CROP)
3. Different headlines (at most 4 words)
"""
        try:
            output = self.backend.generate_structured(prompt, PackagingGenerationOutput)
            if output and output.candidates and len(output.candidates) >= self.MAX_CANDIDATES:
                corrected: List[PackagingCandidate] = []
                for i, p in enumerate(output.candidates[:self.MAX_CANDIDATES]):
                    c = PackagingCandidate(
                        id=f"cand-{i+1}",
                        title=p.title.strip(),
                        title_strategy=p.title_strategy,
                        thumbnail_headline=p.thumbnail_headline.strip() if p.thumbnail_headline else None,
                        thumbnail_visual_strategy=p.thumbnail_visual_strategy,
                        subject_asset_id=p.subject_asset_id if (p.subject_asset_id and p.subject_asset_id in context.visual_asset_ids) else None,
                        supporting_asset_ids=[a for a in p.supporting_asset_ids if a in context.visual_asset_ids],
                        rationale=p.click_motivation_rationale,
                    )
                    corrected.append(c)
                return corrected
        except Exception as e:
            logger.warning(f"Backend diversity correction failed: {e}. Applying deterministic fallback.")

        # 2. Deterministic diversity fallback if backend unavailable
        corrected = list(candidates)
        kw = context.primary_keyword.strip()
        fallback_strategies = ["FOCUS", "SPLIT_CONTRAST", "DETAIL_CROP"]
        fallback_headlines = ["UNDERSTANDING", "HOW IT WORKS", "AT SCALE?"]
        fallback_titles = [
            f"Understanding {kw}"[:95],
            f"How {kw} Works"[:95],
            f"Can {kw} Solve Concurrency?"[:95],
        ]

        for i, cand in enumerate(corrected[:self.MAX_CANDIDATES]):
            cand.id = f"cand-{i+1}"
            cand.thumbnail_visual_strategy = fallback_strategies[i % len(fallback_strategies)]
            cand.thumbnail_headline = fallback_headlines[i % len(fallback_headlines)]
            cand.title = fallback_titles[i % len(fallback_titles)]

        return corrected

    def _evaluate_complementarity(self, title: str, headline: Optional[str]) -> float:
        """Score title <-> thumbnail text complementarity (1.0 = highly complementary, 0.1 = verbatim repetition)."""
        if not headline or not headline.strip():
            return 0.90

        title_words = set(re.findall(r"\w+", title.lower()))
        headline_words = set(re.findall(r"\w+", headline.lower()))
        if not headline_words:
            return 0.90

        overlap = title_words.intersection(headline_words)
        overlap_ratio = len(overlap) / len(headline_words)

        if overlap_ratio >= 0.8:
            return 0.20
        elif overlap_ratio >= 0.5:
            return 0.50
        else:
            return 0.95

    def _calculate_offline_scores(
        self,
        candidate: PackagingCandidate,
        context: PackagingContext,
    ) -> Tuple[float, Dict[str, float]]:
        """Compute observable offline multi-factor packaging quality score (0.0 to 1.0)."""
        title = candidate.title.strip()
        headline = candidate.thumbnail_headline

        # 1. Promise Alignment (core question / promised payoff overlap)
        promise_score = 0.70
        title_lower = title.lower()
        if context.core_question and any(w in title_lower for w in re.findall(r"\w+", context.core_question.lower())):
            promise_score += 0.15
        if context.promised_payoff and any(w in title_lower for w in re.findall(r"\w+", context.promised_payoff.lower())):
            promise_score += 0.15
        promise_score = min(1.0, promise_score)

        # 2. Title Clarity (length penalty if > 70 characters due to mobile truncation)
        clarity_score = 0.95
        if len(title) > 70:
            clarity_score -= 0.20
        if len(title) < 20:
            clarity_score -= 0.15

        # 3. Topic Specificity
        topic_words = set(re.findall(r"\w+", context.primary_keyword.lower()))
        title_words = set(re.findall(r"\w+", title_lower))
        common = topic_words.intersection(title_words)
        spec_score = min(1.0, 0.50 + 0.25 * len(common))

        # 4. Complementarity
        comp_score = self._evaluate_complementarity(title, headline)

        # 5. Audience Fit
        aud_score = 0.85
        if candidate.title_strategy in ("DIRECT_VALUE", "CONTRAST_MECHANISM"):
            aud_score = 0.95

        # 6. Visual Relevance
        vis_score = 0.90 if candidate.subject_asset_id else 0.70

        # 7. Text Legibility
        leg_score = 1.0
        if headline:
            w_count = len(headline.split())
            if w_count > 3:
                leg_score = 0.80

        weighted_total = (
            self.WEIGHT_PROMISE_ALIGNMENT * promise_score
            + self.WEIGHT_CLARITY * clarity_score
            + self.WEIGHT_SPECIFICITY * spec_score
            + self.WEIGHT_COMPLEMENTARITY * comp_score
            + self.WEIGHT_AUDIENCE_FIT * aud_score
            + self.WEIGHT_VISUAL_RELEVANCE * vis_score
            + self.WEIGHT_LEGIBILITY * leg_score
        )

        breakdown = {
            "promise_alignment": round(promise_score, 3),
            "clarity": round(clarity_score, 3),
            "specificity": round(spec_score, 3),
            "complementarity": round(comp_score, 3),
            "audience_fit": round(aud_score, 3),
            "visual_relevance": round(vis_score, 3),
            "legibility": round(leg_score, 3),
        }
        return round(weighted_total, 3), breakdown

    def _select_winner(
        self,
        candidates: List[PackagingCandidate],
        context: PackagingContext,
    ) -> Tuple[Optional[PackagingCandidate], str]:
        """Deterministic tie-breaking selection of winning packaging candidate."""
        passed = [c for c in candidates if c.passed_gates]
        if not passed:
            return None, "No candidates passed truth, constraint, and rendering gates."

        # Sort key: (-quality_score, -complementarity, len(title), candidate_id)
        def tie_breaker_key(c: PackagingCandidate):
            comp = c.score_breakdown.get("complementarity", 0.0)
            return (-c.quality_score, -comp, len(c.title), c.id)

        ranked = sorted(passed, key=tie_breaker_key)
        winner = ranked[0]
        reason = (
            f"Selected candidate '{winner.id}' ({winner.title_strategy}) with highest offline "
            f"quality score {winner.quality_score:.3f} and complementarity {winner.score_breakdown.get('complementarity', 0.0):.2f}."
        )
        return winner, reason

    def _update_downstream_contracts(
        self,
        project: VideoProject,
        winner: PackagingCandidate,
        tournament: PackagingTournament,
        series_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update SEOPackage and active ThumbnailPackage with winner metadata and gate-passed variants."""
        if not winner.content_sha256 or not winner.file_path_16_9 or not Path(winner.file_path_16_9).exists():
            raise PackagingError("Cannot activate winner without real rendered thumbnail file and SHA-256.")

        # 1. Update SEOPackage: ONLY include candidates that passed all gates!
        seo_pkg = self.repo.get_seo_package(project.id)
        if seo_pkg:
            valid_candidates = [c for c in tournament.candidates if c.passed_gates]
            variants: List[TitleVariant] = []
            for c in valid_candidates:
                angle = TitleVariantType.DIRECT_VALUE
                if c.title_strategy == "CONTRAST_MECHANISM":
                    angle = TitleVariantType.CURIOSITY_GAP
                elif c.title_strategy == "CURIOSITY_QUESTION":
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
            content_sha256=winner.content_sha256,
            provenance={
                "generator": "PackagingEngineService",
                "tournament_id": tournament.id,
                "candidate_id": winner.id,
                "strategy": winner.actual_visual_strategy or winner.thumbnail_visual_strategy,
                "subject_asset_id": winner.subject_asset_id,
            },
            created_at=datetime.now(timezone.utc),
        )
        self.repo.save_thumbnail_package(thumb_pkg)

        # 3. Register as project asset if file exists
        thumb_asset = Asset(
            id=f"ast-thumb-{winner.id}",
            project_id=project.id,
            asset_type=AssetType.THUMBNAIL,
            file_path=winner.file_path_16_9,
            source_url=f"file://{winner.file_path_16_9}",
            license_type="PROPRIETARY",
            content_sha256=winner.content_sha256,
            created_at=datetime.now(timezone.utc),
        )
        existing_assets = [a for a in project.assets if a.asset_type != AssetType.THUMBNAIL]
        existing_assets.append(thumb_asset)
        project.assets = existing_assets
        self.repo.save_video_project(project)
