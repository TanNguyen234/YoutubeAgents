"""Tests for Automated Video Director core architecture, beat decomposition, modality routing, and storyboard planning."""

from pathlib import Path
import pytest

from app.domain.models import Scene, Script
from app.media.director.beat_decomposer import BeatDecomposer
from app.media.director.modality_router import VisualModalityRouter
from app.media.director.models import (
    BeatPurpose,
    ChannelCreativeProfile,
    ContentFormat,
    NarrativeBeat,
    ShotTimeline,
    TimelineShot,
    VisualIntent,
    VisualModality,
)
from app.media.director.storyboard_planner import StoryboardPlanner


def test_narrative_beat_model_validation():
    """Verify NarrativeBeat schema constraints and defaults."""
    beat = NarrativeBeat(
        beat_id="b_01_01",
        scene_index=0,
        narration="Large language models predict the next token based on probability distributions.",
        purpose=BeatPurpose.HOOK,
        visual_intent=VisualIntent.SHOW_DATA,
        key_entities=["LLM", "token"],
        preferred_modalities=[VisualModality.DATA_VISUALIZATION],
    )
    assert beat.beat_id == "b_01_01"
    assert beat.purpose == BeatPurpose.HOOK
    assert beat.visual_intent == VisualIntent.SHOW_DATA
    assert VisualModality.STATIC_CARD in beat.avoid_modalities


def test_beat_decomposer_multiple_beats_per_scene():
    """Verify one script scene decomposes into multiple distinct narrative beats."""
    decomposer = BeatDecomposer()
    scene = Scene(
        scene_index=0,
        narration="NVIDIA's revenue exploded as AI companies rushed to buy GPU compute, because traditional servers could not handle massive model parallelism.",
        hook="Why did NVIDIA stock jump 200%?",
        target_duration_seconds=9.0,
    )

    beats = decomposer.decompose_scene_deterministic(
        scene=scene,
        scene_index=0,
        total_scenes=1,
        scene_duration=9.0,
        topic_title="NVIDIA AI Explosion",
    )

    # Must produce at least 2 beats for a 9-second scene
    assert len(beats) >= 2
    total_beat_dur = sum(b.duration_hint for b in beats)
    assert abs(total_beat_dur - 9.0) < 0.5
    for b in beats:
        assert b.duration_hint >= 1.0
        assert b.visual_intent is not None
        assert b.purpose is not None


def test_modality_router_tech_prioritization():
    """Verify router prefers diagrams, charts, and code over generic static cards."""
    router = VisualModalityRouter()

    # Case 1: Data intent
    beat_data = NarrativeBeat(
        beat_id="b_data",
        narration="The model improved benchmark accuracy by 18% with only 7 billion parameters.",
        visual_intent=VisualIntent.SHOW_DATA,
        requires_evidence=True,
    )
    mod_data = router.route_modality(beat_data)
    assert mod_data in (VisualModality.DATA_VISUALIZATION, VisualModality.DOCUMENT_EVIDENCE)

    # Case 2: Code / terminal intent
    beat_code = NarrativeBeat(
        beat_id="b_code",
        narration="Run git rebase interactive to rewrite and squash your commit graph.",
        visual_intent=VisualIntent.SHOW_CODE,
    )
    mod_code = router.route_modality(beat_code)
    assert mod_code in (VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION)

    # Case 3: Mechanism intent (Git / Docker / SQLite)
    beat_mech = NarrativeBeat(
        beat_id="b_mech",
        narration="SQLite WAL mode appends transactions to a write-ahead log without locking concurrent readers.",
        visual_intent=VisualIntent.SHOW_MECHANISM,
    )
    mod_mech = router.route_modality(beat_mech)
    assert mod_mech in (VisualModality.DIAGRAM, VisualModality.MOTION_GRAPHICS)
    assert mod_mech != VisualModality.STATIC_CARD


def test_storyboard_planner_multi_shot_generation(tmp_path: Path):
    """Verify StoryboardPlanner builds rich shot specifications and saves valid artifact."""
    planner = StoryboardPlanner()
    script = Script(
        id="sc_director_test",
        title="Understanding LLM Next-Token Prediction",
        hook="How does ChatGPT actually write?",
        scenes=[
            Scene(
                scene_index=0,
                narration="You type a sentence, and the neural net computes probabilities for thousands of possible next words.",
                hook="How does ChatGPT write?",
                target_duration_seconds=5.0,
            ),
            Scene(
                scene_index=1,
                narration="Instead of thinking like a human, it chooses the highest probability token like Paris at eighty-two percent.",
                target_duration_seconds=5.0,
            ),
        ],
        total_word_count=32,
        estimated_duration_seconds=10.0,
    )

    decomposer = BeatDecomposer()
    beats = decomposer.decompose_script(script, total_audio_duration=10.0)
    assert len(beats) >= 3  # 2 scenes decomposed into 3+ beats

    storyboard = planner.plan_storyboard(
        project_id="proj_story_01",
        script=script,
        beats=beats,
        total_audio_duration=10.0,
        content_format=ContentFormat.EXPLAINER,
    )

    assert len(storyboard.shots) >= 3
    assert storyboard.total_duration == 10.0
    for shot in storyboard.shots:
        assert shot.duration_seconds > 0.0
        assert shot.visual_modality is not None
        # Headline text must not duplicate the whole sentence
        if shot.headline_text:
            assert len(shot.headline_text.split()) <= 6

    # Test artifact serialization
    artifact_path = tmp_path / "storyboard.json"
    planner.save_storyboard_artifact(storyboard, artifact_path)
    assert artifact_path.exists()
    assert artifact_path.stat().st_size > 500


def test_timeline_continuity_validation():
    """Verify ShotTimeline detects gaps or overlaps accurately."""
    shots = [
        TimelineShot(
            shot_id="s1",
            scene_index=0,
            beat_id="b1",
            start=0.0,
            end=2.5,
            duration=2.5,
            asset_path="dummy1.png",
            asset_sha256="h1",
            modality=VisualModality.DIAGRAM,
        ),
        TimelineShot(
            shot_id="s2",
            scene_index=0,
            beat_id="b2",
            start=2.5,
            end=5.0,
            duration=2.5,
            asset_path="dummy2.png",
            asset_sha256="h2",
            modality=VisualModality.DATA_VISUALIZATION,
        ),
    ]
    timeline = ShotTimeline(shots=shots, total_duration=5.0)
    issues = timeline.validate_continuity()
    assert len(issues) == 0

    # Introduce gap
    gap_shots = [
        TimelineShot(
            shot_id="s1",
            scene_index=0,
            beat_id="b1",
            start=0.0,
            end=2.0,
            duration=2.0,
            asset_path="dummy1.png",
            asset_sha256="h1",
            modality=VisualModality.DIAGRAM,
        ),
        TimelineShot(
            shot_id="s2",
            scene_index=0,
            beat_id="b2",
            start=2.5,
            end=5.0,
            duration=2.5,
            asset_path="dummy2.png",
            asset_sha256="h2",
            modality=VisualModality.DATA_VISUALIZATION,
        ),
    ]
    gap_timeline = ShotTimeline(shots=gap_shots, total_duration=5.0)
    gap_issues = gap_timeline.validate_continuity()
    assert len(gap_issues) > 0
    assert "drift" in gap_issues[0]
