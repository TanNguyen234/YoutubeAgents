"""Unit tests for TopicStrategist scoring, YAML weights loading, and candidate evaluation."""

from pathlib import Path
import pytest
from app.domain.models import Channel, TopicCandidate, TopicScoreBreakdown
from app.services.topic_strategist import TopicStrategist


@pytest.fixture
def channel():
    return Channel(
        id="chan-tech-01",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Artificial Intelligence & Python Development",
        target_audience="Software engineers, ML practitioners, and Python developers",
    )


@pytest.fixture
def strategist():
    weights_path = Path("config/topic_weights.yaml")
    return TopicStrategist(weights_config_path=weights_path)


def test_weights_loaded_correctly(strategist):
    weights = strategist.weights
    assert "demand" in weights
    assert "freshness" in weights
    assert "channel_fit" in weights
    assert "originality" in weights
    assert "evidence_quality" in weights
    assert sum(weights.values()) == pytest.approx(1.0, rel=1e-2)


def test_evaluate_topic_produces_score_breakdown_and_composite(strategist, channel):
    breakdown = TopicScoreBreakdown(
        demand=8.0,
        freshness=9.0,
        competition=7.0,
        channel_fit=9.5,
        originality=8.5,
        evidence_quality=9.0,
        production_feasibility=8.0,
        historical_fit=7.5,
        composite_score=0.0,  # will be computed
    )
    evaluated = strategist.evaluate_candidate(
        topic_id="top-001",
        channel=channel,
        keyword="Local AI Agents with Antigravity",
        raw_scores=breakdown,
        rationale="High demand for local sovereign AI workflows",
        recent_channel_topics=[],
    )
    assert isinstance(evaluated, TopicCandidate)
    assert evaluated.id == "top-001"
    assert evaluated.channel_id == channel.id
    assert evaluated.opportunity_score > 7.0
    assert evaluated.authority_score == 9.5
    assert evaluated.score_breakdown is not None
    assert evaluated.score_breakdown.composite_score == evaluated.opportunity_score


def test_duplicate_candidate_is_rejected_or_flagged(strategist, channel):
    recent = ["Local AI Agents with Antigravity"]
    breakdown = TopicScoreBreakdown(
        demand=8.0,
        freshness=8.0,
        competition=6.0,
        channel_fit=9.0,
        originality=7.0,
        evidence_quality=8.0,
        production_feasibility=8.0,
        historical_fit=7.0,
        composite_score=0.0,
    )
    with pytest.raises(ValueError, match="Duplicate topic detected"):
        strategist.evaluate_candidate(
            topic_id="top-002",
            channel=channel,
            keyword="local ai agents with antigravity",
            raw_scores=breakdown,
            rationale="Duplicate test",
            recent_channel_topics=recent,
        )
