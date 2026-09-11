"""Tests for lightweight animated visual primitives, MotionCue, and true motion metrics."""

from pathlib import Path
import pytest

from app.domain.models import Scene, Script
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    ChartDatum,
    ContentFormat,
    MotionCue,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualModality,
)
from app.media.director.quality_evaluator import VisualShotEvaluator
from app.media.renderers.motion_graphics import MotionGraphicsRenderer


def test_motion_cue_model_and_shot_spec_integration():
    """ShotSpec can define explicit temporal animation cues."""
    cue1 = MotionCue(cue_type="type_prompt", start=0.0, duration=0.8, target="input_box", value="The capital of France is")
    cue2 = MotionCue(cue_type="reveal_candidates", start=0.8, duration=0.6, target="candidates_box")
    cue3 = MotionCue(cue_type="grow_bars", start=1.4, duration=0.8, value=0.82)
    cue4 = MotionCue(cue_type="highlight_pulse", start=2.2, duration=0.6, target="Paris")
    cue5 = MotionCue(cue_type="append_token", start=2.8, duration=0.7, target="Paris")

    shot = ShotSpec(
        shot_id="s_anim_01",
        beat_id="b_01",
        narration_segment="The model computes probabilities and appends Paris.",
        visual_modality=VisualModality.DATA_VISUALIZATION,
        duration_seconds=3.5,
        motion_cues=[cue1, cue2, cue3, cue4, cue5],
    )

    assert len(shot.motion_cues) == 5
    assert shot.motion_cues[0].cue_type == "type_prompt"
    assert shot.motion_cues[2].value == 0.82
    assert shot.motion_cues[3].target == "Paris"


def test_render_animated_token_prediction_creates_video(tmp_path: Path):
    """LLM token fixture renders true temporal progression as an MP4 video."""
    renderer = MotionGraphicsRenderer(width=540, height=960)
    out_video = tmp_path / "llm_token_progression.mp4"

    p, sha = renderer.render_animated_token_prediction(
        prompt_text="The capital of France is",
        candidates=[("Paris", 0.82), ("London", 0.08), ("Berlin", 0.06), ("Rome", 0.04)],
        selected_token="Paris",
        output_path=out_video,
        duration=2.0,
        fps=15,
    )

    out_p = Path(p)
    assert out_p.exists()
    assert out_p.stat().st_size > 0
    assert len(sha) == 64
    assert out_p.suffix.lower() in [".mp4", ".png"]


def test_render_animated_terminal_video_creates_video(tmp_path: Path):
    """Terminal typing and line reveal renders as temporal video."""
    renderer = MotionGraphicsRenderer(width=540, height=960)
    out_video = tmp_path / "terminal_typing.mp4"

    p, sha = renderer.render_animated_terminal_video(
        command="git rebase main",
        output_lines=[
            "First, rewinding head to replay your work...",
            "Applying: feat(director): initial implementation",
            "Successfully rebased and updated refs/heads/feature.",
        ],
        output_path=out_video,
        duration=2.0,
        fps=15,
        window_title="bash — rebase",
    )

    out_p = Path(p)
    assert out_p.exists()
    assert out_p.stat().st_size > 0
    assert len(sha) == 64


def test_creative_quality_report_distinguishes_motion_metrics():
    """Quality report correctly calculates static_semantic_ratio, ken_burns_only_ratio, and true_motion_ratio."""
    evaluator = VisualShotEvaluator()

    # Timeline with 1 animated video shot (4.0s) and 1 static diagram (4.0s)
    shots = [
        TimelineShot(
            shot_id="s1",
            scene_index=0,
            beat_id="b1",
            start=0.0,
            end=4.0,
            duration=4.0,
            asset_path="anim.mp4",
            asset_sha256="h1",
            modality=VisualModality.CODE_ANIMATION,
            is_animated=True,
        ),
        TimelineShot(
            shot_id="s2",
            scene_index=0,
            beat_id="b2",
            start=4.0,
            end=8.0,
            duration=4.0,
            asset_path="diag.png",
            asset_sha256="h2",
            modality=VisualModality.DIAGRAM,
            is_animated=False,
        ),
    ]
    timeline = ShotTimeline(shots=shots, total_duration=8.0)
    storyboard = Storyboard(
        project_id="proj_motion_test",
        total_duration=8.0,
        shots=[
            ShotSpec(shot_id="s1", beat_id="b1", narration_segment="Running git command in terminal.", visual_modality=VisualModality.CODE_ANIMATION, duration_seconds=4.0),
            ShotSpec(shot_id="s2", beat_id="b2", narration_segment="Architecture diagram overview.", visual_modality=VisualModality.DIAGRAM, duration_seconds=4.0),
        ],
    )

    report = evaluator.generate_quality_report(timeline=timeline, storyboard=storyboard)
    # 4s out of 8s is true motion (50%)
    assert report.true_motion_ratio == 0.5
    # 4s out of 8s is static semantic graphic with Ken Burns (50%)
    assert report.static_semantic_ratio == 0.5
    assert report.ken_burns_only_ratio == 0.5
    assert report.static_card_ratio == 0.0


def test_llm_token_fixture_produces_true_motion_in_director(tmp_path: Path):
    """AutoDirector plans and renders the LLM token fixture with actual temporal motion."""
    director = AutoDirectorService()
    script = Script(
        id="scr-token-fixture",
        title="LLM Next Token Selection",
        hook="How AI generates words",
        scenes=[
            Scene(
                scene_index=0,
                narration="When given 'The capital of France is', the model computes likelihoods, predicts the next token, and appends Paris.",
                hook="Intro",
                visual_prompt="Autoregressive next token probability selection",
            )
        ],
        total_word_count=18,
        estimated_duration_seconds=6.0,
        content_format=ContentFormat.EXPLAINER,
    )

    out_dir = tmp_path / "out_director_token"
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="proj_token_fx",
        script=script,
        channel_name="AI Channel",
        total_audio_duration=6.0,
        output_dir=out_dir,
    )

    evaluator = VisualShotEvaluator()
    report = evaluator.generate_quality_report(timeline=timeline, storyboard=storyboard)

    # Must contain at least one shot with true motion
    assert report.true_motion_ratio > 0.0
    assert any(s.is_animated or Path(s.asset_path).suffix.lower() == ".mp4" for s in timeline.shots)
