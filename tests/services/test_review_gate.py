"""Unit and contract tests for Stage 12 Human Review Gate service."""

from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    PrivacyStatus,
    QualityStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import Channel, QualityResult, Script, VideoProject
from app.services.review_gate import HumanReviewGateService, ReviewGateError


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_gate.db"
        init_database(db_path)
        r = SQLiteRepository(db_path)
        yield r


def _setup_project_in_review(repo, state=VideoLifecycleState.READY_FOR_REVIEW):
    channel = Channel(
        id="chan-rg-01",
        title="Review Gate Channel",
        handle="@ReviewGate",
        niche="Cloud",
        target_audience="Devs",
    )
    repo.save_channel(channel)

    script = Script(
        id="script-rg-01",
        title="SQLite WAL Deep Dive",
        hook="Why does WAL mode make SQLite 10x faster?",
        scenes=[],
        total_word_count=50,
        estimated_duration_seconds=15.0,
    )

    quality = QualityResult(
        id="qa-rg-01",
        project_id="proj-rg-01",
        status=QualityStatus.PASSED,
        loudness_lufs=-14.2,
        duration_seconds=15.1,
        sync_drift_ms=10.0,
        issues=[],
    )

    project = VideoProject(
        id="proj-rg-01",
        channel_id=channel.id,
        title="SQLite WAL Deep Dive",
        state=VideoLifecycleState.CREATED,
        script=script,
        quality=quality,
    )
    repo.save_video_project(project)

    # Legally advance state machine
    if state != VideoLifecycleState.CREATED:
        repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
        if state == VideoLifecycleState.READY_FOR_REVIEW:
            repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
        elif state == VideoLifecycleState.QA_FAILED:
            repo.update_project_state(project.id, to_state=VideoLifecycleState.QA_FAILED)

    return repo.get_video_project(project.id)


def test_inspect_project_for_review(repo):
    project = _setup_project_in_review(repo)
    service = HumanReviewGateService(repo)

    inspection = service.inspect_project_for_review(project.id)
    assert inspection["project_id"] == project.id
    assert inspection["ready_for_review"] is True
    assert inspection["qa_status"] == "PASSED"
    assert inspection["loudness_lufs"] == -14.2
    assert "SQLite" in inspection["script_title"]


def test_submit_review_approve(repo):
    project = _setup_project_in_review(repo)
    service = HumanReviewGateService(repo)

    rec = service.submit_review(
        project_id=project.id,
        operator="Alice",
        action=ReviewAction.APPROVE,
        notes="Master audio and subtitles look pristine.",
        approved_privacy_status=PrivacyStatus.PRIVATE,
    )
    assert rec.action == ReviewAction.APPROVE
    assert rec.operator == "Alice"

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.APPROVED

    history = repo.get_review_history(project.id)
    assert len(history) == 1
    assert history[0].action == ReviewAction.APPROVE


def test_submit_review_reject(repo):
    project = _setup_project_in_review(repo)
    service = HumanReviewGateService(repo)

    rec = service.submit_review(
        project_id=project.id,
        operator="Bob",
        action=ReviewAction.REJECT,
        notes="Visuals do not align with topic requirements.",
    )
    assert rec.action == ReviewAction.REJECT

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.REJECTED


def test_submit_review_rerender(repo):
    project = _setup_project_in_review(repo)
    service = HumanReviewGateService(repo)

    rec = service.submit_review(
        project_id=project.id,
        operator="Charlie",
        action=ReviewAction.RERENDER,
        notes="Re-synthesize with female voice.",
        media_overrides={"voice": "en-US-JennyNeural"},
    )
    assert rec.action == ReviewAction.RERENDER

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.PRODUCING


def test_submit_review_block(repo):
    project = _setup_project_in_review(repo)
    service = HumanReviewGateService(repo)

    rec = service.submit_review(
        project_id=project.id,
        operator="Dan",
        action=ReviewAction.BLOCK,
        notes="Manual copyright audit needed.",
    )
    assert rec.action == ReviewAction.BLOCK

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.BLOCKED


def test_submit_review_invalid_state_fails(repo):
    channel = Channel(
        id="chan-rg-err",
        title="Err Chan",
        handle="@Err",
        niche="Tech",
        target_audience="Devs",
    )
    repo.save_channel(channel)
    project = VideoProject(
        id="proj-rg-err",
        channel_id=channel.id,
        title="Created Only",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    service = HumanReviewGateService(repo)
    with pytest.raises(ReviewGateError, match="requires project in READY_FOR_REVIEW"):
        service.submit_review(
            project_id=project.id,
            operator="Eve",
            action=ReviewAction.APPROVE,
        )
