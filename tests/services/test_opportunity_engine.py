"""Comprehensive test suite for Opportunity Engine Phase 1.

Verifies deterministic market signal collection, derived metrics, outlier damping,
LLM trust boundaries, provenance validation, duplicate pre-filtering, quota enforcement,
persistence, and end-to-end portfolio ranking.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock
import pytest

from app.core.backend import ReasoningBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import VideoLifecycleState
from app.domain.models import (
    AnalyticsSnapshot,
    Channel,
    MarketSignalSnapshot,
    MarketVideoObservation,
    OpportunityHypothesis,
    OpportunityPortfolio,
    TopicOpportunity,
    VideoProject,
)
from app.services.duplicate_detector import DuplicateDetector
from app.services.opportunity_engine import (
    HypothesesListOutput,
    OpportunityEngine,
)
from app.services.quota_manager import InsufficientQuotaError, QuotaBudgetManager
from app.services.strategy_feedback import StrategyFeedbackLoop
from app.services.topic_evaluator import EditorialEvaluationOutput, TopicEvaluator
from app.services.topic_strategist import TopicStrategist
from app.services.youtube_market_signals import (
    MIN_VALID_VIDEO_SAMPLE,
    YouTubeMarketSignalError,
    YouTubeMarketSignalService,
    compute_percentile,
)


@pytest.fixture
def test_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_opportunity.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)
        yield repo


@pytest.fixture
def channel():
    return Channel(
        id="chan-tech-ai",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Local AI Agents and Python Architecture",
        target_audience="Senior Python Engineers and AI System Architects",
        default_language="en",
        default_tags=["ai agents", "python", "local llm"],
    )


class MockReasoningBackend(ReasoningBackend):
    """Predictable mock backend for testing editorial outputs and hypotheses."""

    def __init__(self, hypotheses=None, channel_fit=8.5):
        self.hypotheses = hypotheses or []
        self.channel_fit = channel_fit
        self.call_count = 0

    def generate_text(self, prompt: str) -> str:
        return "Editorial rationale summary"

    def generate_structured(self, prompt: str, response_model: Any) -> Any:
        self.call_count += 1
        if response_model == HypothesesListOutput:
            return HypothesesListOutput(hypotheses=self.hypotheses)
        if response_model == EditorialEvaluationOutput:
            return EditorialEvaluationOutput(
                channel_fit=self.channel_fit,
                originality_rationale="Highly differentiated angle",
                rationale="Compelling topic for senior software architects",
                score_reasons={"channel_fit": f"Fits persona at {self.channel_fit}/10"},
            )
        return response_model()


# ------------------------------------------------------------------------------
# Test A — Market Metrics Calculation
# ------------------------------------------------------------------------------
def test_a_market_metrics_calculation():
    """Assert deterministic calculation of views_per_day, median, p75, recent_share, and creator stats."""
    collected_at = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    service = YouTubeMarketSignalService()

    # 8 videos with known publish dates, views, and channels
    observations = [
        MarketVideoObservation(
            video_id="v1",
            title="Video 1",
            channel_id="c1",
            channel_title="Creator 1",
            published_at=collected_at - timedelta(days=2),  # 2 days old -> 1000/2 = 500 vpd
            view_count=1000,
        ),
        MarketVideoObservation(
            video_id="v2",
            title="Video 2",
            channel_id="c1",
            channel_title="Creator 1",
            published_at=collected_at - timedelta(days=5),  # 5 days old -> 2000/5 = 400 vpd
            view_count=2000,
        ),
        MarketVideoObservation(
            video_id="v3",
            title="Video 3",
            channel_id="c2",
            channel_title="Creator 2",
            published_at=collected_at - timedelta(days=10),  # 10 days old -> 3000/10 = 300 vpd
            view_count=3000,
        ),
        MarketVideoObservation(
            video_id="v4",
            title="Video 4",
            channel_id="c3",
            channel_title="Creator 3",
            published_at=collected_at - timedelta(days=20),  # 20 days old -> 4000/20 = 200 vpd
            view_count=4000,
        ),
        MarketVideoObservation(
            video_id="v5",
            title="Video 5",
            channel_id="c4",
            channel_title="Creator 4",
            published_at=collected_at - timedelta(days=35),  # 35 days old -> 5000/35 = 142.86 vpd
            view_count=5000,
        ),
        MarketVideoObservation(
            video_id="v6",
            title="Video 6",
            channel_id="c5",
            channel_title="Creator 5",
            published_at=collected_at - timedelta(days=50),  # 50 days old -> 6000/50 = 120 vpd
            view_count=6000,
        ),
        MarketVideoObservation(
            video_id="v7",
            title="Video 7",
            channel_id="c6",
            channel_title="Creator 6",
            published_at=collected_at - timedelta(days=100),  # 100 days old -> 7000/100 = 70 vpd
            view_count=7000,
        ),
        MarketVideoObservation(
            video_id="v8",
            title="Video 8",
            channel_id="c7",
            channel_title="Creator 7",
            published_at=collected_at - timedelta(days=200),  # 200 days old -> 8000/200 = 40 vpd
            view_count=8000,
        ),
    ]

    metrics = service.calculate_derived_metrics(
        observations=observations,
        collected_at=collected_at,
        estimated_result_count=1500,
    )

    assert metrics["sample_size"] == 8
    # Recent counts: age <= 7 (v1: 2d, v2: 5d) -> 2 videos
    assert metrics["recent_video_count_7d"] == 2
    # age <= 30 (v1: 2d, v2: 5d, v3: 10d, v4: 20d) -> 4 videos
    assert metrics["recent_video_count_30d"] == 4
    assert metrics["recent_share_30d"] == 0.5  # 4 / 8

    # Views: [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000]
    # Median is (4000 + 5000) / 2 = 4500.0
    assert metrics["median_views"] == 4500.0
    assert metrics["p75_views"] == pytest.approx(6250.0, rel=1e-2)

    # Views per day: sorted [40, 70, 120, 142.86, 200, 300, 400, 500]
    # Median is (142.86 + 200) / 2 = 171.43
    assert metrics["median_views_per_day"] == pytest.approx(171.43, abs=0.5)

    # Creators: c1 (2 videos), c2, c3, c4, c5, c6, c7 -> 7 unique creators
    assert metrics["unique_creator_count"] == 7
    # Top creator (c1) has 2 / 8 = 0.25
    assert metrics["top_creator_share"] == 0.25
    assert metrics["estimated_result_count"] == 1500


# ------------------------------------------------------------------------------
# Test B — Viral Outlier Robustness
# ------------------------------------------------------------------------------
def test_b_viral_outlier_robustness():
    """Assert median-based velocity is resistant to a single extreme viral outlier."""
    collected_at = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
    service = YouTubeMarketSignalService()

    # 9 ordinary videos (views = 1,000; age = 10 days -> 100 vpd each)
    observations = [
        MarketVideoObservation(
            video_id=f"v_norm_{i}",
            title=f"Normal Video {i}",
            channel_id=f"chan_{i}",
            channel_title=f"Creator {i}",
            published_at=collected_at - timedelta(days=10),
            view_count=1000,
        )
        for i in range(9)
    ]
    # 1 extreme viral video (views = 10,000,000; age = 10 days -> 1,000,000 vpd)
    observations.append(
        MarketVideoObservation(
            video_id="v_viral",
            title="Viral Video Extreme",
            channel_id="chan_viral",
            channel_title="Viral Creator",
            published_at=collected_at - timedelta(days=10),
            view_count=10000000,
        )
    )

    metrics = service.calculate_derived_metrics(
        observations=observations,
        collected_at=collected_at,
    )

    # Median views per day MUST remain 100.0, completely insulated from the viral 1,000,000 vpd
    assert metrics["median_views_per_day"] == 100.0
    # Median views is 1,000
    assert metrics["median_views"] == 1000.0
    # Maximum views would be 10,000,000, showing extreme difference
    max_views = max(v.view_count for v in observations)
    assert max_views == 10000000
    assert metrics["median_views"] != max_views


# ------------------------------------------------------------------------------
# Test C — Real Market Values Not LLM Values
# ------------------------------------------------------------------------------
def test_c_real_market_values_not_llm_values(channel, test_repo):
    """Assert Opportunity Engine calculates market scores deterministically, ignoring LLM attempts to supply them."""
    backend = MockReasoningBackend(
        hypotheses=[
            OpportunityHypothesis(
                keyword="SQLite Optimization",
                angle="WAL Mode Internals",
                rationale="Database performance",
                supporting_video_ids=["v1"],
            )
        ],
        channel_fit=9.0,
    )
    # The EditorialEvaluationOutput model schema cannot even accept demand/freshness/competition
    fields = EditorialEvaluationOutput.model_fields.keys()
    assert "demand" not in fields
    assert "freshness" not in fields
    assert "competition" not in fields
    assert "historical_fit" not in fields

    # Mock market signal service returning fixed metrics
    mock_market = MagicMock(spec=YouTubeMarketSignalService)
    obs = [
        MarketVideoObservation(
            video_id="v1",
            title="SQLite Architecture",
            channel_id="c1",
            channel_title="DB Guru",
            published_at=datetime.now(timezone.utc) - timedelta(days=10),
            view_count=2000,
        )
        for _ in range(6)
    ]
    mock_market.fetch_market_observations.return_value = (obs, 500)
    mock_snap = MarketSignalSnapshot(
        id="mss-test-01",
        batch_id="opp-batch-1",
        channel_id=channel.id,
        query="SQLite Optimization",
        collected_at=datetime.now(timezone.utc),
        sample_video_ids=["v1"],
        sample_size=6,
        recent_video_count_7d=2,
        recent_video_count_30d=5,
        recent_share_30d=0.83,
        median_views=2000.0,
        p75_views=2000.0,
        median_age_days=10.0,
        median_views_per_day=200.0,
        p75_views_per_day=200.0,
        unique_creator_count=3,
        top_creator_share=0.5,
        estimated_result_count=500,
        confidence="HIGH",
    )
    mock_market.get_market_signal_snapshot.return_value = mock_snap

    engine = OpportunityEngine(
        repository=test_repo,
        market_signal_service=mock_market,
        backend=backend,
    )
    portfolio = engine.discover_opportunities(channel=channel, seed_queries=["SQLite"])

    assert len(portfolio.candidates) == 1
    cand = portfolio.candidates[0]
    # Demand is computed deterministically (log10(200)*2 = 4.6), NOT an invented 10.0
    assert cand.demand == pytest.approx(4.6, abs=0.1)
    assert cand.channel_fit == 9.0


# ------------------------------------------------------------------------------
# Test D — Supporting Video ID Validation
# ------------------------------------------------------------------------------
def test_d_supporting_video_id_validation(channel, test_repo):
    """Assert server-side validation strips invented supporting video IDs not in observed sample."""
    # Backend proposes one real ID ('v_real') and one invented ID ('v_hallucinated')
    backend = MockReasoningBackend(
        hypotheses=[
            OpportunityHypothesis(
                keyword="AI Memory Agents",
                angle="Local SQLite Storage",
                rationale="Architecture",
                supporting_video_ids=["v_real", "v_hallucinated_xyz"],
            )
        ]
    )

    observed_videos = [
        MarketVideoObservation(
            video_id="v_real",
            title="Real Video",
            channel_id="c1",
            channel_title="AI Creator",
            published_at=datetime.now(timezone.utc) - timedelta(days=2),
            view_count=5000,
        )
    ]

    engine = OpportunityEngine(repository=test_repo, backend=backend)
    validated = engine._generate_topic_hypotheses(channel, observed_videos)

    assert len(validated) == 1
    # 'v_hallucinated_xyz' MUST be rejected and removed
    assert "v_hallucinated_xyz" not in validated[0].supporting_video_ids
    assert validated[0].supporting_video_ids == ["v_real"]


# ------------------------------------------------------------------------------
# Test E — Insufficient Signal
# ------------------------------------------------------------------------------
def test_e_insufficient_signal(channel, test_repo):
    """Assert candidates with sample size below MIN_VALID_VIDEO_SAMPLE are flagged and blocked."""
    backend = MockReasoningBackend(
        hypotheses=[
            OpportunityHypothesis(
                keyword="Obscure Niche Protocol",
                angle="Deep Dive",
                rationale="Niche topic",
                supporting_video_ids=["v1"],
            )
        ]
    )

    mock_market = MagicMock(spec=YouTubeMarketSignalService)
    # Only 2 videos observed (below MIN_VALID_VIDEO_SAMPLE = 5)
    obs = [
        MarketVideoObservation(
            video_id="v1",
            title="Small Video 1",
            channel_id="c1",
            channel_title="C1",
            published_at=datetime.now(timezone.utc) - timedelta(days=5),
            view_count=100,
        ),
        MarketVideoObservation(
            video_id="v2",
            title="Small Video 2",
            channel_id="c2",
            channel_title="C2",
            published_at=datetime.now(timezone.utc) - timedelta(days=8),
            view_count=150,
        ),
    ]
    mock_market.fetch_market_observations.return_value = (obs, 10)
    mock_snap = MarketSignalSnapshot(
        id="mss-insufficient",
        batch_id="opp-insufficient",
        channel_id=channel.id,
        query="Obscure Niche Protocol",
        collected_at=datetime.now(timezone.utc),
        sample_video_ids=["v1", "v2"],
        sample_size=2,
        confidence="INSUFFICIENT_SIGNAL",
    )
    mock_market.get_market_signal_snapshot.return_value = mock_snap

    engine = OpportunityEngine(
        repository=test_repo,
        market_signal_service=mock_market,
        backend=backend,
        min_valid_sample=5,
    )
    portfolio = engine.discover_opportunities(channel=channel, seed_queries=["Obscure Niche"])

    assert len(portfolio.candidates) == 1
    assert portfolio.candidates[0].confidence == "INSUFFICIENT_SIGNAL"
    # No arbitrary winner selected
    assert portfolio.selected_topic is None
    assert "No candidate met the minimum market evidence threshold" in portfolio.selection_reason


# ------------------------------------------------------------------------------
# Test F — Simulated Analytics Excluded & No-History Behavior
# ------------------------------------------------------------------------------
def test_f_simulated_analytics_excluded(test_repo, channel):
    """Assert historical fit ignores simulated analytics, returns None when real history is absent, and scores when real."""
    test_repo.save_channel(channel)
    p_sim = VideoProject(
        id="proj-sim-01",
        channel_id=channel.id,
        title="Simulated Video Topic",
        state=VideoLifecycleState.CREATED,
    )
    test_repo.save_video_project(p_sim)
    for st in [
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.VERIFIED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.RENDERED,
        VideoLifecycleState.READY_FOR_REVIEW,
        VideoLifecycleState.APPROVED,
        VideoLifecycleState.UPLOADING,
        VideoLifecycleState.PUBLISHED,
    ]:
        test_repo.update_project_state(p_sim.id, to_state=st)

    # Save a SIMULATED snapshot
    snap_sim = AnalyticsSnapshot(
        id="snap-sim-01",
        project_id=p_sim.id,
        snapshot_type="SIMULATED",
        is_simulated=True,
        views=10000,
        watch_time_hours=500.0,
        ctr_percent=12.0,
        average_view_duration_seconds=120.0,
    )
    test_repo.save_analytics_snapshot(snap_sim)

    feedback = StrategyFeedbackLoop(test_repo)
    # 1. Zero real analytics -> historical_fit MUST be None (NOT 6.0)
    fit_score = feedback.compute_historical_fit_score("Simulated Video Topic", channel.id)
    assert fit_score is None

    # 2. Add REAL analytics snapshot for another project
    p_real = VideoProject(
        id="proj-real-01",
        channel_id=channel.id,
        title="Real High Performing SQLite Agents",
        state=VideoLifecycleState.CREATED,
    )
    test_repo.save_video_project(p_real)
    for st in [
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.VERIFIED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.RENDERED,
        VideoLifecycleState.READY_FOR_REVIEW,
        VideoLifecycleState.APPROVED,
        VideoLifecycleState.UPLOADING,
        VideoLifecycleState.PUBLISHED,
    ]:
        test_repo.update_project_state(p_real.id, to_state=st)

    snap_real = AnalyticsSnapshot(
        id="snap-real-01",
        project_id=p_real.id,
        snapshot_type="REAL",
        is_simulated=False,
        views=25000,
        watch_time_hours=1200.0,
        ctr_percent=10.5,
        average_view_duration_seconds=150.0,
    )
    test_repo.save_analytics_snapshot(snap_real)

    # Now real analytics exist: matching keyword should produce a real measured score
    real_fit_score = feedback.compute_historical_fit_score("SQLite Agents Architecture", channel.id)
    assert real_fit_score is not None
    assert real_fit_score > 6.0


# ------------------------------------------------------------------------------
# Test G — Deterministic Portfolio Ranking
# ------------------------------------------------------------------------------
def test_g_deterministic_portfolio_ranking():
    """Assert identical inputs produce identical scores and ordering across multiple executions."""
    snapshots = [
        MarketSignalSnapshot(
            id="s1",
            batch_id="b1",
            channel_id="c1",
            query="Topic A",
            collected_at=datetime.now(timezone.utc),
            sample_size=10,
            recent_video_count_7d=3,
            recent_video_count_30d=6,
            recent_share_30d=0.6,
            median_views=5000.0,
            p75_views=8000.0,
            median_views_per_day=500.0,
            p75_views_per_day=800.0,
            unique_creator_count=8,
            top_creator_share=0.2,
            estimated_result_count=1000,
        ),
        MarketSignalSnapshot(
            id="s2",
            batch_id="b1",
            channel_id="c1",
            query="Topic B",
            collected_at=datetime.now(timezone.utc),
            sample_size=10,
            recent_video_count_7d=1,
            recent_video_count_30d=2,
            recent_share_30d=0.2,
            median_views=1000.0,
            p75_views=1500.0,
            median_views_per_day=100.0,
            p75_views_per_day=150.0,
            unique_creator_count=4,
            top_creator_share=0.5,
            estimated_result_count=20000,
        ),
    ]

    engine = OpportunityEngine()
    scores_1 = engine.compute_deterministic_market_scores(snapshots)
    scores_2 = engine.compute_deterministic_market_scores(snapshots)

    assert scores_1 == scores_2
    assert scores_1["s1"]["demand"] > scores_1["s2"]["demand"]
    assert scores_1["s1"]["freshness"] > scores_1["s2"]["freshness"]
    assert scores_1["s1"]["competition"] > scores_1["s2"]["competition"]


# ------------------------------------------------------------------------------
# Test H — Duplicate Filter Before Expensive Validation
# ------------------------------------------------------------------------------
def test_h_duplicate_filter_before_expensive_validation(channel, test_repo):
    """Assert candidate matching recent channel topic is rejected before candidate market search."""
    backend = MockReasoningBackend(
        hypotheses=[
            OpportunityHypothesis(
                keyword="Local AI Agents With Python",
                angle="Building Agents",
                rationale="Reasoning",
                supporting_video_ids=["v1"],
            ),
            OpportunityHypothesis(
                keyword="Fresh Distinct Architecture",
                angle="New Pattern",
                rationale="Unique",
                supporting_video_ids=["v1"],
            ),
        ]
    )

    mock_market = MagicMock(spec=YouTubeMarketSignalService)
    mock_market.fetch_market_observations.return_value = (
        [
            MarketVideoObservation(
                video_id="v1",
                title="AI Agents",
                channel_id="c1",
                channel_title="C1",
                published_at=datetime.now(timezone.utc) - timedelta(days=2),
                view_count=5000,
            )
        ],
        100,
    )
    mock_market.get_market_signal_snapshot.return_value = MarketSignalSnapshot(
        id="mss-fresh",
        batch_id="b1",
        channel_id=channel.id,
        query="Fresh Distinct Architecture",
        collected_at=datetime.now(timezone.utc),
        sample_size=6,
        confidence="HIGH",
    )

    recent = ["local ai agents with python"]

    engine = OpportunityEngine(
        repository=test_repo,
        market_signal_service=mock_market,
        backend=backend,
    )
    portfolio = engine.discover_opportunities(channel=channel, recent_topics=recent)

    # get_market_signal_snapshot MUST be called ONLY for "Fresh Distinct Architecture",
    # NOT for the duplicate "Local AI Agents With Python"
    called_queries = [call.kwargs.get("query") for call in mock_market.get_market_signal_snapshot.call_args_list]
    assert "Local AI Agents With Python" not in called_queries
    assert "Fresh Distinct Architecture" in called_queries


# ------------------------------------------------------------------------------
# Test I — Quota Exhaustion
# ------------------------------------------------------------------------------
def test_i_quota_exhaustion(test_repo, channel):
    """Assert search is blocked and raises InsufficientQuotaError when quota budget is exhausted."""
    quota_mgr = QuotaBudgetManager(test_repo, daily_limit=50)
    # search.list costs 100 units, but daily_limit is only 50
    service = YouTubeMarketSignalService(
        repository=test_repo,
        quota_manager=quota_mgr,
        api_key="fake-test-key",
    )

    with pytest.raises(InsufficientQuotaError, match="Insufficient daily YouTube API quota"):
        service.fetch_market_observations(query="Test Query")


# ------------------------------------------------------------------------------
# Test J — Persistence Round Trip
# ------------------------------------------------------------------------------
def test_j_persistence_round_trip(test_repo, channel):
    """Assert MarketSignalSnapshot round trips cleanly through SQLiteRepository."""
    test_repo.save_channel(channel)
    collected_at = datetime(2026, 9, 18, 15, 30, 0, tzinfo=timezone.utc)

    snapshot = MarketSignalSnapshot(
        id="mss-roundtrip-01",
        batch_id="msb-batch-01",
        channel_id=channel.id,
        query="High Concurrency SQLite",
        source="YOUTUBE_DATA_API_V3",
        collected_at=collected_at,
        sample_video_ids=["vid_1", "vid_2", "vid_3"],
        sample_size=3,
        recent_video_count_7d=1,
        recent_video_count_30d=2,
        recent_share_30d=0.67,
        median_views=4500.0,
        p75_views=6200.0,
        median_age_days=14.5,
        median_views_per_day=310.3,
        p75_views_per_day=427.5,
        unique_creator_count=3,
        top_creator_share=0.33,
        estimated_result_count=1850,
        formula_version="v1.0",
        confidence="HIGH",
        raw_metrics={"sample_size": 3, "test_key": "test_val"},
        derived_scores={"demand": 7.5, "freshness": 6.8, "competition": 8.0},
    )

    test_repo.save_market_signal_snapshot(snapshot)

    loaded = test_repo.get_market_signal_snapshot("mss-roundtrip-01")
    assert loaded is not None
    assert loaded.id == "mss-roundtrip-01"
    assert loaded.batch_id == "msb-batch-01"
    assert loaded.channel_id == channel.id
    assert loaded.query == "High Concurrency SQLite"
    assert loaded.source == "YOUTUBE_DATA_API_V3"
    assert loaded.sample_video_ids == ["vid_1", "vid_2", "vid_3"]
    assert loaded.sample_size == 3
    assert loaded.median_views == 4500.0
    assert loaded.median_views_per_day == 310.3
    assert loaded.unique_creator_count == 3
    assert loaded.formula_version == "v1.0"
    assert loaded.confidence == "HIGH"
    assert loaded.raw_metrics.get("test_key") == "test_val"
    assert loaded.derived_scores.get("demand") == 7.5


# ------------------------------------------------------------------------------
# Test K — End-to-End Opportunity Flow
# ------------------------------------------------------------------------------
def test_k_end_to_end_opportunity_flow(test_repo, channel):
    """Assert complete end-to-end Opportunity Engine flow produces portfolio and traceable winner."""
    test_repo.save_channel(channel)

    backend = MockReasoningBackend(
        hypotheses=[
            OpportunityHypothesis(
                keyword="SQLite High Concurrency",
                angle="WAL Mode Deep Dive",
                rationale="Architectural interest",
                supporting_video_ids=["v_seed_1"],
            ),
            OpportunityHypothesis(
                keyword="Basic Python Variables",
                angle="Beginner Syntax",
                rationale="Basic intro",
                supporting_video_ids=["v_seed_2"],
            ),
        ],
        channel_fit=9.2,
    )

    mock_market = MagicMock(spec=YouTubeMarketSignalService)
    # Seed observation
    seed_obs = [
        MarketVideoObservation(
            video_id="v_seed_1",
            title="SQLite Architecture",
            channel_id="c1",
            channel_title="DB Guru",
            published_at=datetime.now(timezone.utc) - timedelta(days=5),
            view_count=8000,
        ),
        MarketVideoObservation(
            video_id="v_seed_2",
            title="Python Tutorial",
            channel_id="c2",
            channel_title="Code Academy",
            published_at=datetime.now(timezone.utc) - timedelta(days=20),
            view_count=1000,
        ),
    ]
    mock_market.fetch_market_observations.return_value = (seed_obs, 500)

    # Candidate 1: High demand
    snap1 = MarketSignalSnapshot(
        id="mss-sqlite-01",
        batch_id="b-e2e",
        channel_id=channel.id,
        query="SQLite High Concurrency",
        collected_at=datetime.now(timezone.utc),
        sample_video_ids=["v_seed_1"] * 6,
        sample_size=6,
        recent_video_count_7d=3,
        recent_video_count_30d=6,
        recent_share_30d=1.0,
        median_views=8000.0,
        p75_views=8000.0,
        median_age_days=5.0,
        median_views_per_day=1600.0,
        p75_views_per_day=1600.0,
        unique_creator_count=5,
        top_creator_share=0.2,
        estimated_result_count=300,
        confidence="HIGH",
    )
    # Candidate 2: Low demand
    snap2 = MarketSignalSnapshot(
        id="mss-python-02",
        batch_id="b-e2e",
        channel_id=channel.id,
        query="Basic Python Variables",
        collected_at=datetime.now(timezone.utc),
        sample_video_ids=["v_seed_2"] * 6,
        sample_size=6,
        recent_video_count_7d=0,
        recent_video_count_30d=1,
        recent_share_30d=0.16,
        median_views=1000.0,
        p75_views=1000.0,
        median_age_days=20.0,
        median_views_per_day=50.0,
        p75_views_per_day=50.0,
        unique_creator_count=2,
        top_creator_share=0.8,
        estimated_result_count=50000,
        confidence="HIGH",
    )

    def mock_get_snapshot(channel, query, **kwargs):
        if "SQLite" in query:
            return snap1
        return snap2

    mock_market.get_market_signal_snapshot.side_effect = mock_get_snapshot

    engine = OpportunityEngine(
        repository=test_repo,
        market_signal_service=mock_market,
        backend=backend,
    )
    portfolio = engine.discover_opportunities(channel=channel, seed_queries=["SQLite"])

    assert len(portfolio.candidates) == 2
    assert portfolio.selected_topic is not None
    winner = portfolio.selected_topic
    # Winner must be SQLite High Concurrency due to high velocity and low saturation
    assert winner.keyword == "SQLite High Concurrency"
    assert winner.opportunity_score > portfolio.candidates[1].opportunity_score
    assert winner.market_signal_id == "mss-sqlite-01"
    # Selection reason audits deterministic factors
    assert "Selected 'SQLite High Concurrency'" in portfolio.selection_reason
    assert "mss-sqlite-01" in portfolio.selection_reason


# ------------------------------------------------------------------------------
# Test L — No Real Signal = No Fake Success
# ------------------------------------------------------------------------------
def test_l_no_real_signal_no_fake_success(test_repo, channel):
    """Assert YouTube API failure raises a typed error and never falls back to dummy success metrics."""
    # Custom client that fails with 500 error
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal YouTube API Error"
    mock_client.get.return_value = mock_resp

    service = YouTubeMarketSignalService(
        repository=test_repo,
        http_client=mock_client,
        api_key="valid-looking-key",
    )

    with pytest.raises(YouTubeMarketSignalError, match="YouTube search.list failed"):
        service.fetch_market_observations(query="Any Query")
