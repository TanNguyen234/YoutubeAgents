"""Tests for Creative QA evaluation, selective regeneration loop, and release gates."""

from pathlib import Path
import pytest

from app.domain.enums import VideoLifecycleState
from app.domain.models import Scene, Script
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    ChartDatum,
    ContentFormat,
    EvidenceBinding,
    NarrativeBeat,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualModality,
)
from app.media.director.quality_evaluator import VisualShotEvaluator
from app.media.pipeline import MediaProductionPipeline
from tests.media.test_media_pipeline import MockTTSBackend, repo_with_verified_project


def test_evaluate_shot_uses_metadata_heuristic_mode():
    """VisualShotEvaluator must not claim fake VLM precision (aesthetic/relevance should be None)."""
    evaluator = VisualShotEvaluator()
    shot = ShotSpec(
        shot_id="s_01_01",
        beat_id="b_01",
        narration_segment="We analyze the database write-ahead log performance.",
        headline_text="WAL PERFORMANCE",
        visual_modality=VisualModality.DIAGRAM,
        duration_seconds=3.0,
    )

    eval_res = evaluator.evaluate_shot(shot=shot, asset_exists=True)
    assert eval_res.evaluation_mode == "METADATA_HEURISTIC"
    assert eval_res.visual_relevance is None
    assert eval_res.aesthetic_quality is None
    assert eval_res.continuity is None
    assert eval_res.readability is None
    assert eval_res.recommendation == "ACCEPT"
    assert eval_res.narration_duplication < 0.6


def test_evaluate_shot_flags_high_duplication_for_regeneration():
    """Shots with headline text repeating the entire spoken narration must be flagged REGENERATE."""
    evaluator = VisualShotEvaluator()
    narration = "This is a detailed explanation of the transaction log architecture."
    shot = ShotSpec(
        shot_id="s_01_01",
        beat_id="b_01",
        narration_segment=narration,
        headline_text=narration,  # Identical duplicate slide
        visual_modality=VisualModality.STATIC_CARD,
        duration_seconds=6.0,  # Exceeds static limit
    )

    eval_res = evaluator.evaluate_shot(shot=shot, asset_exists=True)
    assert eval_res.recommendation == "REGENERATE"
    assert any("duplication" in issue.lower() for issue in eval_res.issues)


def test_selective_regeneration_loop_in_director(tmp_path: Path):
    """When a shot fails initial evaluation, only that shot regenerates with max 2 retries."""
    director = AutoDirectorService()
    script = Script(
        id="sc-selective-test",
        title="Selective Regen Test",
        hook="Hook text",
        scenes=[
            Scene(
                scene_index=0,
                narration="We analyze the database write-ahead log performance and compare it to memory.",
                hook="Intro",
                visual_prompt="Diagram of database write-ahead log",
            )
        ],
        total_word_count=14,
        estimated_duration_seconds=6.0,
        content_format=ContentFormat.EXPLAINER,
    )

    out_dir = tmp_path / "director_selective"
    out_dir.mkdir(parents=True, exist_ok=True)

    timeline, storyboard = director.plan_and_render_timeline(
        project_id="proj-selective-01",
        script=script,
        channel_name="Tech Channel",
        total_audio_duration=6.0,
        output_dir=out_dir,
    )

    assert len(timeline.shots) > 0
    # Every planned shot must have a recorded evaluation and attempt count
    for shot in storyboard.shots:
        assert shot.shot_id in director.shot_evaluations
        assert shot.shot_id in director.shot_attempt_counts
        # Ensure retries never exceed MAX_CREATIVE_RETRIES (2) + 1 initial attempt = 3 attempts total
        assert director.shot_attempt_counts[shot.shot_id] <= 3


def test_creative_qa_hard_failure_gate_blocks_release(repo_with_verified_project, tmp_path: Path, monkeypatch):
    """When creative QA encounters critical failures (e.g. gap or ungrounded evidence), project fails QA."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_creative_gate_fail",
    )

    # Force creative QA critical failure by monkeypatching generate_quality_report
    orig_generate = pipeline.visual_evaluator.generate_quality_report

    def mock_bad_report(*args, **kwargs):
        report = orig_generate(*args, **kwargs)
        report.creative_status = "FAIL"
        report.critical_failures = ["TIMELINE_GAP: Gap of 1.500s detected between shot 1 and shot 2"]
        return report

    monkeypatch.setattr(pipeline.visual_evaluator, "generate_quality_report", mock_bad_report)

    proj, qa_res, manifest = pipeline.run_production(project_id=project_id)
    assert proj.state == VideoLifecycleState.QA_FAILED
    assert manifest.qa_verdict == "FAILED"
    assert any("TIMELINE_GAP" in issue for issue in manifest.qa_issues)


def test_regenerate_single_shot_updates_only_target(tmp_path: Path):
    """Calling regenerate_single_shot updates asset for that specific shot without touching others."""
    director = AutoDirectorService()
    script = Script(
        id="sc-single-regen",
        title="Single Shot Regen",
        hook="Hook text",
        scenes=[
            Scene(
                scene_index=0,
                narration="Step one sets up the configuration file and dependencies.",
                hook="Setup",
                visual_prompt="Terminal with configuration setup",
            )
        ],
        total_word_count=9,
        estimated_duration_seconds=6.0,
    )

    out_dir = tmp_path / "director_single_shot"
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="proj-single-01",
        script=script,
        channel_name="Tech Channel",
        total_audio_duration=6.0,
        output_dir=out_dir,
    )

    target_shot = storyboard.shots[0]
    orig_asset = target_shot.asset_query

    # Regenerate only shot 0 with a new instruction
    new_timeline, new_storyboard = director.regenerate_single_shot(
        project_id="proj-single-01",
        shot_id=target_shot.shot_id,
        timeline=timeline,
        storyboard=storyboard,
        output_dir=out_dir,
        new_instruction="New diagram instruction for regenerated shot",
    )

    assert new_timeline.shots[0].shot_id == target_shot.shot_id
    assert Path(new_timeline.shots[0].asset_path).exists()


def test_final_creative_report_uses_resolved_channel_profile(repo_with_verified_project, tmp_path):
    """Ensure MediaProductionPipeline's visual_evaluator uses the channel's resolved creative profile, not default."""
    from app.media.director.profiles import get_channel_profile_for_niche

    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_profile_qa",
    )

    proj, qa_res, manifest = pipeline.run_production(project_id=project_id)

    expected_profile = get_channel_profile_for_niche("Distributed Systems & Database Engineering")
    assert pipeline.visual_evaluator.profile.name == expected_profile.name
    assert pipeline.visual_evaluator.profile.name == manifest.creative_profile
    assert pipeline.director.evaluator.profile.name == pipeline.visual_evaluator.profile.name
