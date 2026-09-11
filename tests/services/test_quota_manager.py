"""Tests for QuotaBudgetManager service."""

from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import VideoLifecycleState
from app.domain.models import Channel, VideoProject
from app.services.quota_manager import InsufficientQuotaError, QuotaBudgetManager


@pytest.fixture
def quota_manager(tmp_path: Path) -> QuotaBudgetManager:
    db_file = tmp_path / "test_quota.db"
    repo = SQLiteRepository(db_file)
    chan = Channel(
        id="chan-01",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Artificial Intelligence",
        target_audience="Engineers",
    )
    repo.save_channel(chan)
    p1 = VideoProject(
        id="proj-01",
        channel_id="chan-01",
        title="Test MoE Video",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(p1)
    return QuotaBudgetManager(repository=repo, daily_limit=10000)


def test_quota_tracking_and_preflight(quota_manager: QuotaBudgetManager):
    # Initially 0 spent, 10000 remaining
    assert quota_manager.get_remaining_units() == 10000
    assert quota_manager.can_spend("videos.insert") is True

    # Record a video upload (1600 units)
    rec = quota_manager.record_spend("videos.insert", project_id="proj-01")
    assert rec.units_consumed == 1600
    assert quota_manager.get_spent_units() == 1600
    assert quota_manager.get_remaining_units() == 8400

    # Record thumbnail upload (50 units)
    quota_manager.record_spend("thumbnails.set", project_id="proj-01")
    assert quota_manager.get_spent_units() == 1650
    assert quota_manager.get_remaining_units() == 8350


def test_quota_exceeded_blocking(quota_manager: QuotaBudgetManager):
    # Set manager with tiny budget for testing
    tight_manager = QuotaBudgetManager(repository=quota_manager.repository, daily_limit=2000)

    # First video upload fits (1600 <= 2000)
    tight_manager.ensure_budget("videos.insert")
    tight_manager.record_spend("videos.insert", project_id="proj-01")

    # Second video upload requires 1600, but only 400 remaining -> should raise
    assert tight_manager.can_spend("videos.insert") is False
    with pytest.raises(InsufficientQuotaError) as exc_info:
        tight_manager.ensure_budget("videos.insert")
    assert "Insufficient daily YouTube API quota" in str(exc_info.value)
