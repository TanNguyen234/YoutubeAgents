"""Tests for narration duplication scoring, visual dead-air detection, and quality evaluation."""

from app.media.director.models import (
    ChannelCreativeProfile,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualModality,
)
from app.media.director.quality_evaluator import (
    VisualShotEvaluator,
    calculate_narration_duplication,
)


def test_calculate_narration_duplication_exact_repetition():
    """Verbatim repetition of narration on visual slide must trigger high penalty (>0.7)."""
    narration = "Large language models predict the next token based on probability distribution."
    visual_text = "LARGE LANGUAGE MODELS PREDICT THE NEXT TOKEN BASED ON PROBABILITY DISTRIBUTION"

    score = calculate_narration_duplication(narration, visual_text)
    assert score >= 0.7, f"Expected high duplication penalty, got {score}"


def test_calculate_narration_duplication_semantic_complement():
    """Demonstrative or contextual visual text should receive low duplication score (<0.3)."""
    narration = "Large language models predict the next token based on probability distribution."
    visual_text = "Candidate: Paris (82%)"

    score = calculate_narration_duplication(narration, visual_text)
    assert score < 0.3, f"Expected low duplication score for complementary visual, got {score}"


def test_calculate_narration_duplication_empty_visual():
    """Empty or non-text visual has zero duplication."""
    narration = "NVIDIA's revenue exploded as AI companies rushed to buy GPU compute."
    score = calculate_narration_duplication(narration, "")
    assert score == 0.0


def test_visual_dead_air_detection():
    """Shots exceeding static duration thresholds must trigger VISUAL_DEAD_AIR warnings."""
    evaluator = VisualShotEvaluator()

    # Timeline with a 7.5s static card
    shots = [
        TimelineShot(
            shot_id="shot_01",
            beat_id="beat_01",
            start=0.0,
            end=7.5,
            duration=7.5,
            asset_path="dummy.png",
            asset_sha256="dummyhash",
            modality=VisualModality.STATIC_CARD,
        ),
        TimelineShot(
            shot_id="shot_02",
            beat_id="beat_02",
            start=7.5,
            end=10.0,
            duration=2.5,
            asset_path="diagram.png",
            asset_sha256="dummyhash2",
            modality=VisualModality.DIAGRAM,
        ),
    ]
    timeline = ShotTimeline(shots=shots, total_duration=10.0)

    warnings = evaluator.detect_visual_dead_air(timeline, max_static_duration=5.0)
    assert len(warnings) >= 1
    assert any("VISUAL_DEAD_AIR" in w and "shot_01" in w for w in warnings)


def test_quality_report_static_card_ratio_warning():
    """When static card runtime exceeds acceptable ratio (e.g. 15%), emit warning."""
    evaluator = VisualShotEvaluator()

    shots = [
        TimelineShot(
            shot_id="s1",
            beat_id="b1",
            start=0.0,
            end=4.0,
            duration=4.0,
            asset_path="card1.png",
            asset_sha256="h1",
            modality=VisualModality.STATIC_CARD,
        ),
        TimelineShot(
            shot_id="s2",
            beat_id="b2",
            start=4.0,
            end=8.0,
            duration=4.0,
            asset_path="card2.png",
            asset_sha256="h2",
            modality=VisualModality.STATIC_CARD,
        ),
    ]
    timeline = ShotTimeline(shots=shots, total_duration=8.0)
    storyboard = Storyboard(
        project_id="proj_test",
        total_duration=8.0,
        shots=[
            ShotSpec(shot_id="s1", beat_id="b1", narration_segment="test", visual_modality=VisualModality.STATIC_CARD, duration_seconds=4.0),
            ShotSpec(shot_id="s2", beat_id="b2", narration_segment="test", visual_modality=VisualModality.STATIC_CARD, duration_seconds=4.0),
        ],
    )

    report = evaluator.generate_quality_report(timeline=timeline, storyboard=storyboard, max_static_card_ratio=0.15)
    assert report.static_card_ratio == 1.0
    assert any("static-card based" in w for w in report.visual_dead_air_warnings)
    assert report.overall_visual_score < 0.8
