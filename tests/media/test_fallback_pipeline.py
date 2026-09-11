"""Tests for Creative Pipeline Fallback and Selective Shot Regeneration."""

import logging
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import PlatformFormat, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import ContentFormat, VisualModality
from app.media.pipeline import MediaProductionPipeline
from tests.media.test_media_pipeline import MockTTSBackend


@pytest.fixture
def repo_with_verified_project(tmp_path: Path):
    """Fixture providing an initialized SQLite repository with a project in VERIFIED state."""
    db_path = tmp_path / "test_fallback_db.sqlite"
    init_database(db_path)
    repo = SQLiteRepository(db_path)

    channel = Channel(
        id="chan-01",
        title="Engineering Channel",
        handle="@engchannel",
        niche="Databases",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    script = Script(
        id="sc-verified",
        title="SQLite WAL Architecture",
        hook="How does SQLite WAL mode work?",
        scenes=[
            Scene(scene_index=0, narration="First scene explaining WAL readers.", hook="Hook 1", visual_prompt="P1"),
            Scene(scene_index=1, narration="Second scene explaining WAL writers.", hook="Hook 2", visual_prompt="P2"),
        ],
        total_word_count=12,
        estimated_duration_seconds=6.0,
    )

    project = VideoProject(
        id="proj-verified-01",
        channel_id="chan-01",
        title="SQLite WAL Architecture",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)

    return repo, project.id


def test_director_failure_blocks_production_by_default(repo_with_verified_project, tmp_path: Path):
    """By default (FAIL_CLOSED), AutoDirector failure must raise MediaProductionError and fail production."""
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_fallback_default",
        # default fallback_policy is FAIL_CLOSED
    )

    def failing_director(*args, **kwargs):
        raise RuntimeError("Simulated creative director model timeout")

    pipeline.director.plan_and_render_timeline = failing_director

    with pytest.raises(Exception) as exc_info:
        pipeline.run_production(project_id=project_id)

    assert "AutoDirector failed in FAIL_CLOSED mode" in str(exc_info.value)
    proj = repo.get_video_project(project_id)
    assert proj.state in (VideoLifecycleState.FAILED, VideoLifecycleState.PRODUCING)


def test_legacy_fallback_requires_explicit_preview_policy(repo_with_verified_project, tmp_path: Path, caplog):
    """When explicitly configured with ALLOW_LEGACY_PREVIEW, pipeline renders legacy preview but Creative QA fails."""
    from app.media.director.models import CreativeFallbackPolicy
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_fallback_preview",
        fallback_policy=CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW,
    )

    def failing_director(*args, **kwargs):
        raise RuntimeError("Simulated creative director model timeout")

    pipeline.director.plan_and_render_timeline = failing_director

    with caplog.at_level(logging.WARNING):
        proj, qa_res, manifest = pipeline.run_production(project_id=project_id)

    # Legacy slides are generated for developer inspection, but Creative QA MUST fail
    assert proj.state == VideoLifecycleState.QA_FAILED
    assert manifest.director_used is False
    assert manifest.director_fallback_occurred is True
    assert "Simulated creative director model timeout" in (manifest.creative_fallback_reason or "")
    assert manifest.qa_verdict == "FAILED"
    assert any("AutoDirector fallback occurred" in issue for issue in manifest.qa_issues)
    assert any("CREATIVE_PIPELINE_FALLBACK" in rec.message for rec in caplog.records)


def test_director_fallback_cannot_reach_approved_publication(repo_with_verified_project, tmp_path: Path):
    """Director fallback must never reach READY_FOR_REVIEW or allow publication approval."""
    from app.media.director.models import CreativeFallbackPolicy
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_fallback_no_publish",
        fallback_policy=CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW,
    )

    def failing_director(*args, **kwargs):
        raise RuntimeError("Creative failure")

    pipeline.director.plan_and_render_timeline = failing_director

    proj, qa_res, manifest = pipeline.run_production(project_id=project_id)

    assert proj.state == VideoLifecycleState.QA_FAILED
    assert manifest.qa_verdict == "FAILED"

    # Verifying state machine reject transition to REVIEW_APPROVED from QA_FAILED
    with pytest.raises(Exception):
        repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.REVIEW_APPROVED,
            expected_current_state=VideoLifecycleState.READY_FOR_REVIEW,
        )


def test_selective_shot_regeneration(tmp_path: Path):
    """Verify regenerating an individual shot without re-running other shots or TTS."""
    script = Script(
        id="scr-regen-test",
        title="Selective Regeneration Test",
        hook="How to regenerate shots selectively?",
        scenes=[
            Scene(
                index=0,
                narration="Step one explores data. Step two trains the model. Step three benchmarks accuracy.",
                target_duration_seconds=6.0,
            )
        ],
        total_word_count=15,
        estimated_duration_seconds=6.0,
    )

    director = AutoDirectorService()
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="proj_regen",
        script=script,
        channel_name="AI Channel",
        total_audio_duration=6.0,
        output_dir=tmp_path / "director_orig",
        content_format=ContentFormat.EXPLAINER,
    )

    assert len(timeline.shots) >= 2
    target_shot_id = timeline.shots[0].shot_id
    orig_hash = timeline.shots[0].asset_sha256
    orig_other_shot_hash = timeline.shots[1].asset_sha256

    # Regenerate ONLY the first shot with a different modality instruction
    updated_timeline, updated_storyboard = director.regenerate_single_shot(
        project_id="proj_regen",
        shot_id=target_shot_id,
        timeline=timeline,
        storyboard=storyboard,
        output_dir=tmp_path / "director_orig",
        fallback_modality=VisualModality.DATA_VISUALIZATION,
        new_instruction="Accuracy: 94.5% vs Baseline 81.2%",
    )

    # Verify target shot was regenerated
    updated_shot_0 = updated_timeline.shots[0]
    assert updated_shot_0.shot_id == target_shot_id
    assert updated_shot_0.modality == VisualModality.DATA_VISUALIZATION
    assert Path(updated_shot_0.asset_path).exists()
    assert updated_shot_0.asset_sha256 != orig_hash

    # Verify other shot remained completely untouched
    assert updated_timeline.shots[1].asset_sha256 == orig_other_shot_hash
