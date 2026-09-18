"""Opportunity Engine orchestrating real market signal collection, candidate discovery, and portfolio ranking.

Phase 1: Real Market Signals -> Candidate Discovery -> Evidence-Backed Topic Portfolio.
"""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4
from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.models import (
    Channel,
    MarketSignalSnapshot,
    MarketVideoObservation,
    OpportunityHypothesis,
    OpportunityPortfolio,
    TopicOpportunity,
)
from app.services.duplicate_detector import DuplicateDetector
from app.services.quota_manager import InsufficientQuotaError, QuotaBudgetManager
from app.services.strategy_feedback import StrategyFeedbackLoop
from app.services.topic_evaluator import TopicEvaluator
from app.services.topic_strategist import TopicStrategist
from app.services.youtube_market_signals import (
    MIN_VALID_VIDEO_SAMPLE,
    YouTubeMarketSignalError,
    YouTubeMarketSignalService,
)

MAX_SEED_QUERIES = 2
MAX_OPPORTUNITY_CANDIDATES = 5
MAX_RESULTS_PER_QUERY = 10


class HypothesesListOutput(BaseModel):
    """Structured LLM output container for proposed opportunity hypotheses."""

    hypotheses: List[OpportunityHypothesis] = Field(default_factory=list)


class OpportunityEngine:
    """Discovers, validates, scores, and ranks video topic opportunities using real market evidence."""

    def __init__(
        self,
        repository: Optional[SQLiteRepository] = None,
        market_signal_service: Optional[YouTubeMarketSignalService] = None,
        strategist: Optional[TopicStrategist] = None,
        evaluator: Optional[TopicEvaluator] = None,
        feedback_loop: Optional[StrategyFeedbackLoop] = None,
        backend: Optional[ReasoningBackend] = None,
        max_seed_queries: int = MAX_SEED_QUERIES,
        max_candidates: int = MAX_OPPORTUNITY_CANDIDATES,
        max_results_per_query: int = MAX_RESULTS_PER_QUERY,
        min_valid_sample: int = MIN_VALID_VIDEO_SAMPLE,
    ):
        self.repository = repository
        self.market_signals = market_signal_service or YouTubeMarketSignalService(repository=repository)
        self.strategist = strategist or TopicStrategist()
        self.evaluator = evaluator or TopicEvaluator(backend=backend)
        self.feedback_loop = feedback_loop or (StrategyFeedbackLoop(repository) if repository else None)
        self.backend = backend or AntigravityCLIBackend()
        self.max_seed_queries = max_seed_queries
        self.max_candidates = max_candidates
        self.max_results_per_query = max_results_per_query
        self.min_valid_sample = min_valid_sample

    def _resolve_seed_queries(
        self,
        channel: Channel,
        user_seed_queries: Optional[List[str]] = None,
    ) -> List[str]:
        """Determine initial seed queries from user input, channel niche, default tags, or real top projects."""
        seeds: List[str] = []
        if user_seed_queries:
            seeds.extend([q.strip() for q in user_seed_queries if q.strip()])

        # Check real top-performing historical projects (if available)
        if len(seeds) < self.max_seed_queries and self.feedback_loop:
            analysis = self.feedback_loop.analyze_channel_performance(channel.id)
            if analysis.get("has_data") and analysis.get("top_projects"):
                for p in analysis["top_projects"][:self.max_seed_queries]:
                    title = p.get("title", "").strip()
                    if title and title not in seeds:
                        seeds.append(title)

        # Fallback to channel niche and default tags
        if len(seeds) < self.max_seed_queries and channel.niche:
            if channel.niche not in seeds:
                seeds.append(channel.niche)
        if len(seeds) < self.max_seed_queries and channel.default_tags:
            for tag in channel.default_tags:
                if tag not in seeds:
                    seeds.append(tag)
                    if len(seeds) >= self.max_seed_queries:
                        break

        return seeds[:self.max_seed_queries]

    def _generate_topic_hypotheses(
        self,
        channel: Channel,
        seed_observations: List[MarketVideoObservation],
    ) -> List[OpportunityHypothesis]:
        """Prompt reasoning backend to generate structured topic hypotheses grounded in observed market videos."""
        if not seed_observations:
            return []

        # Provide compact catalog of observed videos
        video_catalog_lines = [
            f"- [ID: {v.video_id}] '{v.title}' by '{v.channel_title}' ({v.view_count:,} views)"
            for v in seed_observations[:20]
        ]
        catalog_str = "\n".join(video_catalog_lines)

        prompt = f"""You are an elite YouTube Strategist analyzing observable market demand for '{channel.title}'.
Channel Niche: {channel.niche}
Target Audience: {channel.target_audience}

REAL MARKET VIDEOS CURRENTLY OBSERVED IN THIS NICHE:
{catalog_str}

TASK:
Propose up to {self.max_candidates} specific, compelling video topic hypotheses that address evident viewer demand.
For each hypothesis provide:
- keyword: Concise search/topic phrase (e.g. 'Local LLM Agent Memory')
- angle: Unique technical or storytelling angle that differentiates it from competing videos
- viewer_question: The core question viewers want answered
- rationale: Why this topic has strong opportunity for this channel
- supporting_video_ids: List of video IDs from the observed list above that prove audience demand for this topic

CRITICAL SECURITY CONSTRAINT:
You MUST choose supporting_video_ids strictly from the IDs listed in the observed videos above.
Do NOT invent video IDs or search volume numbers.
"""
        raw_output = self.backend.generate_structured(prompt, HypothesesListOutput)
        if isinstance(raw_output, dict):
            raw_output = HypothesesListOutput.model_validate(raw_output)

        observed_id_set: Set[str] = {v.video_id for v in seed_observations}
        validated_hypotheses: List[OpportunityHypothesis] = []

        for h in raw_output.hypotheses:
            # Server-side provenance validation: filter out any invented video IDs
            clean_supporting = [vid for vid in h.supporting_video_ids if vid in observed_id_set]
            h.supporting_video_ids = clean_supporting
            validated_hypotheses.append(h)

        return validated_hypotheses[:self.max_candidates]

    def compute_deterministic_market_scores(
        self,
        snapshots: List[MarketSignalSnapshot],
    ) -> Dict[str, Dict[str, float]]:
        """Calculate normalized 0-10 demand, freshness, and competition opportunity scores across the batch."""
        scores: Dict[str, Dict[str, float]] = {}

        if not snapshots:
            return scores

        # Calculate raw demand metrics
        raw_demands: Dict[str, float] = {}
        for s in snapshots:
            # 70% median view velocity + 30% 75th percentile view velocity
            vpd_med = float(s.median_views_per_day)
            vpd_p75 = float(s.p75_views_per_day)
            raw_demands[s.id] = (0.70 * vpd_med) + (0.30 * vpd_p75)

        demand_vals = list(raw_demands.values())
        min_demand = min(demand_vals) if demand_vals else 0.0
        max_demand = max(demand_vals) if demand_vals else 0.0
        demand_range = max_demand - min_demand

        for s in snapshots:
            # 1. Demand Proxy (0-10)
            raw_d = raw_demands[s.id]
            if demand_range > 0 and len(snapshots) > 1:
                demand_score = 3.0 + (7.0 * (raw_d - min_demand) / demand_range)
            else:
                # Logarithmic scale fallback for single candidate or equal demand
                demand_score = math.log10(max(1.0, raw_d)) * 2.0 if raw_d > 0 else 1.0
            demand_score = round(min(10.0, max(0.0, demand_score)), 2)

            # 2. Freshness Proxy (0-10)
            # 60% recent publishing activity (30d share) + 40% recent velocity momentum
            r_share = float(s.recent_share_30d)
            c7 = float(s.recent_video_count_7d)
            c30 = float(s.recent_video_count_30d)
            velocity_momentum = (c7 / max(1.0, c30)) if c30 > 0 else 0.0
            raw_freshness = (0.60 * r_share) + (0.40 * min(1.0, velocity_momentum))
            freshness_score = round(min(10.0, max(0.0, raw_freshness * 10.0)), 2)

            # 3. Competition Opportunity Proxy (0-10)
            # 10 = low saturation / high opportunity; 0 = high saturation / creator monopoly
            creator_diversity = 1.0 - float(s.top_creator_share)
            if s.estimated_result_count is not None:
                saturation = min(1.0, float(s.estimated_result_count) / 100000.0)
            else:
                saturation = 0.5
            raw_comp = (0.70 * creator_diversity) + (0.30 * (1.0 - saturation))
            competition_score = round(min(10.0, max(0.0, raw_comp * 10.0)), 2)

            scores[s.id] = {
                "demand": demand_score,
                "freshness": freshness_score,
                "competition": competition_score,
            }

        return scores

    def discover_opportunities(
        self,
        channel: Channel,
        seed_queries: Optional[List[str]] = None,
        recent_topics: Optional[List[str]] = None,
        max_candidates: Optional[int] = None,
        force_refresh: bool = False,
    ) -> OpportunityPortfolio:
        """Run full Opportunity Engine workflow discovering, scoring, and ranking candidate portfolio."""
        batch_id = f"opp-{uuid4().hex[:8]}"
        recent = recent_topics or []
        limit_candidates = max_candidates or self.max_candidates

        # Step 1: Ensure channel is saved in repository if repository exists
        if self.repository:
            existing_chan = self.repository.get_channel(channel.id)
            if not existing_chan:
                self.repository.save_channel(channel)

        # Step 2: Resolve seed queries
        seeds = self._resolve_seed_queries(channel, seed_queries)
        if not seeds:
            return OpportunityPortfolio(
                batch_id=batch_id,
                channel_id=channel.id,
                candidates=[],
                selected_topic=None,
                selection_reason="No seed queries or channel niche could be determined for discovery.",
            )

        # Step 2: Collect initial seed market observations
        all_seed_observations: List[MarketVideoObservation] = []
        for q in seeds:
            obs, _ = self.market_signals.fetch_market_observations(
                query=q,
                max_results=self.max_results_per_query,
                region_code=getattr(channel, "region_code", None),
                relevance_language=getattr(channel, "default_language", None),
            )
            all_seed_observations.extend(obs)

        # De-duplicate seed observations by video_id
        seen_vids: Set[str] = set()
        unique_seed_obs: List[MarketVideoObservation] = []
        for obs in all_seed_observations:
            if obs.video_id not in seen_vids:
                seen_vids.add(obs.video_id)
                unique_seed_obs.append(obs)

        # Step 3: Propose structured topic hypotheses grounded in observed videos
        hypotheses = self._generate_topic_hypotheses(channel, unique_seed_obs)
        if not hypotheses:
            return OpportunityPortfolio(
                batch_id=batch_id,
                channel_id=channel.id,
                candidates=[],
                selected_topic=None,
                selection_reason="No valid topic hypotheses could be generated from market seed observations.",
            )

        # Step 4: Early Duplicate Filtering before expensive candidate-specific queries
        surviving_hypotheses: List[OpportunityHypothesis] = []
        for h in hypotheses:
            is_dup, score, matched = self.strategist.duplicate_detector.check_duplicate(h.keyword, recent)
            if is_dup:
                continue  # Drop duplicate candidate early to save API quota
            surviving_hypotheses.append(h)

        if not surviving_hypotheses:
            return OpportunityPortfolio(
                batch_id=batch_id,
                channel_id=channel.id,
                candidates=[],
                selected_topic=None,
                selection_reason="All proposed hypotheses were rejected as duplicates of recent channel topics.",
            )

        # Step 5: Collect candidate-specific market signal snapshots
        candidate_snapshots: Dict[str, MarketSignalSnapshot] = {}
        for h in surviving_hypotheses[:limit_candidates]:
            snap = self.market_signals.get_market_signal_snapshot(
                channel=channel,
                query=h.keyword,
                batch_id=batch_id,
                max_results=self.max_results_per_query,
                force_refresh=force_refresh,
            )
            candidate_snapshots[h.keyword] = snap

        # Step 6: Deterministic market scoring
        market_scores_by_snap_id = self.compute_deterministic_market_scores(list(candidate_snapshots.values()))

        # Step 7: Editorial & Historical evaluation
        candidate_opportunities: List[TopicOpportunity] = []
        observed_titles = [v.title for v in unique_seed_obs]

        for h in surviving_hypotheses[:limit_candidates]:
            snap = candidate_snapshots[h.keyword]
            m_scores = market_scores_by_snap_id.get(snap.id, {"demand": 0.0, "freshness": 0.0, "competition": 0.0})

            # Editorial dimension: channel_fit evaluated by model
            channel_fit, orig_rationale, editorial_summary, score_reasons_ed = (
                self.evaluator.evaluate_editorial_dimensions(
                    channel=channel,
                    keyword=h.keyword,
                    angle=h.angle,
                    market_titles=observed_titles[:5],
                )
            )

            # Deterministic originality score: distinctness from recent channel topics and market titles
            _, dup_score, _ = self.strategist.duplicate_detector.check_duplicate(h.keyword, recent)
            _, market_dup_score, _ = self.strategist.duplicate_detector.check_duplicate(h.keyword, observed_titles)
            max_sim = max(dup_score, market_dup_score)
            originality = round(min(10.0, max(0.0, (1.0 - max_sim) * 10.0)), 2)

            # Real historical fit: strictly None if no real analytics exist
            historical_fit: Optional[float] = None
            if self.feedback_loop:
                historical_fit = self.feedback_loop.compute_historical_fit_score(h.keyword, channel.id)

            # Composite opportunity score calculation with dynamic weight renormalization
            score_dict: Dict[str, Optional[float]] = {
                "demand": m_scores["demand"],
                "freshness": m_scores["freshness"],
                "competition": m_scores["competition"],
                "channel_fit": channel_fit,
                "originality": originality,
                "historical_fit": historical_fit,
            }
            composite = self.strategist.compute_opportunity_score(score_dict)

            # Check confidence gate
            confidence = "HIGH" if snap.sample_size >= self.min_valid_sample else "INSUFFICIENT_SIGNAL"

            # Detailed score reasons audit dictionary
            score_reasons = {
                "demand": f"Median velocity {snap.median_views_per_day:.1f} views/day, p75 {snap.p75_views_per_day:.1f} views/day",
                "freshness": f"30-day publishing share {snap.recent_share_30d*100:.1f}%, 7-day count {snap.recent_video_count_7d}",
                "competition": f"Unique creators: {snap.unique_creator_count}, top creator share {snap.top_creator_share*100:.1f}%",
                "channel_fit": score_reasons_ed.get("channel_fit", f"Fit: {channel_fit:.1f}/10 for '{channel.target_audience}'"),
                "originality": f"Originality {originality:.1f}/10 (market similarity {max_sim:.2f})",
                "historical_fit": (
                    f"Historical performance correlation {historical_fit:.1f}/10"
                    if historical_fit is not None
                    else "No real channel history available (renormalized)"
                ),
            }

            # Update snapshot with derived scores
            snap.derived_scores = {
                "demand": m_scores["demand"],
                "freshness": m_scores["freshness"],
                "competition": m_scores["competition"],
                "channel_fit": channel_fit,
                "originality": originality,
                "opportunity_score": composite,
            }
            if self.repository:
                self.repository.save_market_signal_snapshot(snap)

            candidate_opportunities.append(
                TopicOpportunity(
                    keyword=h.keyword,
                    angle=h.angle,
                    opportunity_score=composite,
                    demand=m_scores["demand"],
                    freshness=m_scores["freshness"],
                    competition=m_scores["competition"],
                    channel_fit=channel_fit,
                    originality=originality,
                    historical_fit=historical_fit,
                    market_signal_id=snap.id,
                    confidence=confidence,
                    score_reasons=score_reasons,
                    rationale=editorial_summary,
                    supporting_video_ids=h.supporting_video_ids,
                )
            )

        # Step 8: Deterministic ranking by opportunity_score descending
        candidate_opportunities.sort(key=lambda c: c.opportunity_score, reverse=True)

        # Step 9: Winner selection with confidence gate enforcement
        selected_winner: Optional[TopicOpportunity] = None
        selection_reason: Optional[str] = None

        # Filter candidates passing confidence gate
        viable_candidates = [c for c in candidate_opportunities if c.confidence == "HIGH"]
        if viable_candidates:
            selected_winner = viable_candidates[0]
            selection_reason = (
                f"Selected '{selected_winner.keyword}' (Rank #1, Score: {selected_winner.opportunity_score:.2f}) "
                f"with demand score {selected_winner.demand:.1f}/10, freshness {selected_winner.freshness:.1f}/10, "
                f"competition opportunity {selected_winner.competition:.1f}/10, and channel fit {selected_winner.channel_fit:.1f}/10. "
                f"Traceable to MarketSignalSnapshot {selected_winner.market_signal_id}."
            )
        else:
            selected_winner = None
            selection_reason = (
                f"No candidate met the minimum market evidence threshold (sample >= {self.min_valid_sample}). "
                "All candidates flagged INSUFFICIENT_SIGNAL."
            )

        portfolio = OpportunityPortfolio(
            batch_id=batch_id,
            channel_id=channel.id,
            generated_at=datetime.now(timezone.utc),
            candidates=candidate_opportunities,
            selected_topic=selected_winner,
            selection_reason=selection_reason,
        )
        return portfolio
