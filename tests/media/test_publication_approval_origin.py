"""Tests for human review gate approval origin and publication privacy policy."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    ApprovalOrigin,
    PrivacyStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import Channel, VideoProject
from app.services.review_gate import HumanReviewGateService, ReviewGateError


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_approval.db"
        init_database(db_path)
        yield SQLiteRepository(db_path)


def _setup_ready_project(repo: SQLiteRepository, project_id: str = "proj-rev-01") -> VideoProject:
    channel = Channel(
        id="chan-rev-01",
        title="Review Channel",
        handle="@RevChan",
        niche="Security",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    project = VideoProject(
        id=project_id,
        channel_id=channel.id,
        title="Zero Trust Architecture",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    # Transition to READY_FOR_REVIEW
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
    return repo.get_video_project(project.id)


def test_public_requires_human_approval_origin(repo):
    project = _setup_ready_project(repo)
    service = HumanReviewGateService(repo)

    # Automated approval for PUBLIC publication must be rejected
    with pytest.raises(ReviewGateError, match="Approval for public publication requires ApprovalOrigin.HUMAN"):
        service.submit_review(
            project_id=project.id,
            operator="BotOperator",
            action=ReviewAction.APPROVE,
            approved_privacy_status=PrivacyStatus.PUBLIC,
            approval_origin=ApprovalOrigin.AUTOMATION,
        )


def test_public_succeeds_with_human_origin(repo):
    project = _setup_ready_project(repo)
    service = HumanReviewGateService(repo)

    rec = service.submit_review(
        project_id=project.id,
        operator="Alice Engineer",
        action=ReviewAction.APPROVE,
        notes="All factual sources verified manually.",
        approved_privacy_status=PrivacyStatus.PUBLIC,
        approval_origin=ApprovalOrigin.HUMAN,
    )
    assert rec.approval_origin == ApprovalOrigin.HUMAN
    assert rec.approved_privacy_status == PrivacyStatus.PUBLIC
    assert rec.operator == "Alice Engineer"

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.APPROVED

    history = repo.get_review_history(project.id)
    assert len(history) == 1
    assert history[0].approval_origin == ApprovalOrigin.HUMAN


def test_autonomous_operator_cannot_claim_human_origin(repo):
    project = _setup_ready_project(repo)
    service = HumanReviewGateService(repo)

    with pytest.raises(ReviewGateError, match="AutonomousOperator cannot masquerade as ApprovalOrigin.HUMAN"):
        service.submit_review(
            project_id=project.id,
            operator="AutonomousOperator",
            action=ReviewAction.APPROVE,
            approved_privacy_status=PrivacyStatus.PUBLIC,
            approval_origin=ApprovalOrigin.HUMAN,
        )


def test_private_allows_automation_approval(repo):
    project = _setup_ready_project(repo)
    service = HumanReviewGateService(repo)

    rec = service.submit_review(
        project_id=project.id,
        operator="AutonomousOperator",
        action=ReviewAction.APPROVE,
        notes="Automated pipeline private dry-run verification.",
        approved_privacy_status=PrivacyStatus.PRIVATE,
        approval_origin=ApprovalOrigin.AUTOMATION,
    )
    assert rec.action == ReviewAction.APPROVE
    assert rec.approval_origin == ApprovalOrigin.AUTOMATION
    assert rec.approved_privacy_status == PrivacyStatus.PRIVATE

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.APPROVED


def test_unlisted_requires_human_unless_explicitly_overridden(repo):
    project = _setup_ready_project(repo)
    service = HumanReviewGateService(repo)

    with pytest.raises(ReviewGateError, match="Approval for unlisted publication requires ApprovalOrigin.HUMAN"):
        service.submit_review(
            project_id=project.id,
            operator="BotOperator",
            action=ReviewAction.APPROVE,
            approved_privacy_status=PrivacyStatus.UNLISTED,
            approval_origin=ApprovalOrigin.AUTOMATION,
        )

    # Allowed with explicit override
    rec = service.submit_review(
        project_id=project.id,
        operator="BotOperator",
        action=ReviewAction.APPROVE,
        approved_privacy_status=PrivacyStatus.UNLISTED,
        approval_origin=ApprovalOrigin.AUTOMATION,
        allow_autonomous_public=True,
    )
    assert rec.action == ReviewAction.APPROVE
    assert rec.approved_privacy_status == PrivacyStatus.UNLISTED
