"""Tests verifying analytics lifecycle correctness, no fake video IDs, and simulation exclusion."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import AnalyticsSource, VideoLifecycleState
from app.domain.models import AnalyticsSnapshot, Channel, VideoProject
from app.services.analytics_tracker import YouTubeAnalyticsTracker
from app.services.strategy_feedback import StrategyFeedbackLoop


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_analytics_lifecycle.db"
        init_database(db_path)
        yield SQLiteRepository(db_path)


def _setup_published_project(repo: SQLiteRepository, project_id: str = "proj-ana-01") -> VideoProject:
    channel = Channel(
        id="chan-ana-01",
        title="Analytics Test Channel",
        handle="@AnaTest",
        niche="Data Engineering",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    project = VideoProject(
        id=project_id,
        channel_id=channel.id,
        title="Kafka Stream Processing",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    # Transition to PUBLISHED
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.APPROVED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.UPLOADING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PUBLISHED)
    return repo.get_video_project(project.id)


def test_real_analytics_does_not_invent_fake_youtube_video_id(repo):
    project = _setup_published_project(repo)
    tracker = YouTubeAnalyticsTracker(repo)

    # Record snapshot without passing any youtube_video_id
    snapshot = tracker.record_snapshot(
        project_id=project.id,
        views=2500,
        watch_time_hours=80.0,
        ctr_percent=6.5,
        average_view_duration_seconds=115.0,
        retention_at_3s_percent=60.0,
        youtube_video_id=None,
        is_simulated=False,
    )

    # Must NOT fabricate a fake id like "yt-auto-..."
    assert snapshot.youtube_video_id is None
    assert snapshot.snapshot_type == "REAL"
    assert snapshot.source == AnalyticsSource.LEGACY_UNVERIFIED
    assert snapshot.is_simulated is False


def test_simulated_analytics_marked_and_excluded_from_strategy_feedback(repo):
    project = _setup_published_project(repo)
    tracker = YouTubeAnalyticsTracker(repo)

    # Record a simulated snapshot
    sim_snap = tracker.record_snapshot(
        project_id=project.id,
        views=99999,
        watch_time_hours=5000.0,
        ctr_percent=25.0,
        average_view_duration_seconds=300.0,
        retention_at_3s_percent=95.0,
        is_simulated=True,
    )

    assert sim_snap.is_simulated is True
    assert sim_snap.snapshot_type == "SIMULATED"

    # Verify StrategyFeedbackLoop ignores simulated snapshots
    feedback = StrategyFeedbackLoop(repo)
    analysis = feedback.analyze_channel_performance("chan-ana-01")

    # Has data should be False because only simulated snapshot exists
    assert analysis["has_data"] is False
    assert analysis["total_snapshots"] == 0
    assert analysis["mean_views"] == 0.0

    # Now add an authoritative REAL API snapshot
    real_snap = tracker.record_snapshot(
        project_id=project.id,
        views=1200,
        watch_time_hours=42.0,
        ctr_percent=7.0,
        average_view_duration_seconds=126.0,
        retention_at_3s_percent=55.0,
        youtube_video_id="real-yt-id-12345",
        is_simulated=False,
        source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
    )

    analysis_with_real = feedback.analyze_channel_performance("chan-ana-01")
    # Only the 1 real snapshot should be counted, mean views = 1200 (not 99999)
    assert analysis_with_real["has_data"] is True
    assert analysis_with_real["total_snapshots"] == 1
    assert analysis_with_real["mean_views"] == 1200.0


def test_blocked_project_lifecycle_skips_analytics_cleanly(repo):
    channel = Channel(
        id="chan-blk-01",
        title="Block Test Channel",
        handle="@BlockTest",
        niche="DevOps",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    project = VideoProject(
        id="proj-blk-01",
        channel_id=channel.id,
        title="Kubernetes Ingress Controllers",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.BLOCKED)

    # An attempt to record real analytics on a BLOCKED project via tracker fails with AnalyticsTrackerError
    tracker = YouTubeAnalyticsTracker(repo)
    with pytest.raises(Exception, match="requires project in PUBLISHED or SCHEDULED state"):
        tracker.record_snapshot(
            project_id=project.id,
            views=100,
            watch_time_hours=1.0,
            ctr_percent=4.0,
            average_view_duration_seconds=20.0,
        )
