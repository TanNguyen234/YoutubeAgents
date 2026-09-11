"""Tests for Stage 12 Human Review Gate domain contracts, schemas, and persistence."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    PrivacyStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import Channel, ReviewRecord, VideoProject


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_review.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)
        yield repo


def test_review_record_schema_and_validation():
    """Verify ReviewRecord Pydantic model validation and default fields."""
    record = ReviewRecord(
        id="rev-001",
        project_id="proj-001",
        operator="test_operator",
        action=ReviewAction.APPROVE,
        notes="High quality video, approved for publishing",
        approved_privacy_status=PrivacyStatus.PRIVATE,
    )
    assert record.id == "rev-001"
    assert record.action == ReviewAction.APPROVE
    assert record.approved_privacy_status == PrivacyStatus.PRIVATE
    assert record.media_overrides == {}
    assert isinstance(record.reviewed_at, datetime)


def test_save_and_retrieve_review_record(temp_repo):
    """Verify SQLite persistence and query of review records with foreign key enforcement."""
    channel = Channel(
        id="chan-rev-01",
        title="Review Channel",
        handle="@ReviewChannel",
        niche="Tech",
        target_audience="Engineers",
    )
    temp_repo.save_channel(channel)

    project = VideoProject(
        id="proj-rev-01",
        channel_id=channel.id,
        title="Testing Review Persistence",
        state=VideoLifecycleState.CREATED,
    )
    temp_repo.save_video_project(project)

    record = ReviewRecord(
        id="rev-test-01",
        project_id=project.id,
        operator="Alice Operator",
        action=ReviewAction.APPROVE,
        notes="Script verified, visuals checked, approved",
        approved_privacy_status=PrivacyStatus.PRIVATE,
        media_overrides={"voice": "en-US-GuyNeural"},
    )
    temp_repo.save_review_record(record)

    history = temp_repo.get_review_history(project.id)
    assert len(history) == 1
    retrieved = history[0]
    assert retrieved.id == "rev-test-01"
    assert retrieved.project_id == "proj-rev-01"
    assert retrieved.operator == "Alice Operator"
    assert retrieved.action == ReviewAction.APPROVE
    assert retrieved.notes == "Script verified, visuals checked, approved"
    assert retrieved.approved_privacy_status == PrivacyStatus.PRIVATE
    assert retrieved.media_overrides == {"voice": "en-US-GuyNeural"}
