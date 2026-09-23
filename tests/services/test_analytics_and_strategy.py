"""Unit and contract tests for Stage 14 Analytics Tracker and Stage 15 Strategy Feedback Loop."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import AnalyticsSource, VideoLifecycleState
from app.domain.models import AnalyticsSnapshot, Channel, VideoProject
from app.services.analytics_tracker import AnalyticsTrackerError, YouTubeAnalyticsTracker
from app.services.strategy_feedback import StrategyFeedbackLoop


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_analytics.db"
        init_database(db_path)
        r = SQLiteRepository(db_path)
        yield r


def _setup_published_project(repo, project_id, title, channel_id="chan-strat-01"):
    channel = repo.get_channel(channel_id)
    if not channel:
        channel = Channel(
            id=channel_id,
            title="Strategy Channel",
            handle="@Strategy",
            niche="Databases",
            target_audience="Engineers",
        )
        repo.save_channel(channel)

    project = VideoProject(
        id=project_id,
        channel_id=channel_id,
        title=title,
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    # Legally advance state to PUBLISHED
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


def test_record_analytics_snapshot_success(repo):
    project = _setup_published_project(repo, "proj-snap-01", "SQLite WAL Concurrency")
    tracker = YouTubeAnalyticsTracker(repo)

    snapshot = tracker.record_snapshot(
        project_id=project.id,
        views=1500,
        watch_time_hours=45.5,
        ctr_percent=8.2,
        average_view_duration_seconds=109.2,
        retention_at_3s_percent=65.0,
        youtube_video_id="yt-wal-123",
    )

    assert snapshot.project_id == project.id
    assert snapshot.views == 1500
    assert snapshot.ctr_percent == 8.2
    assert snapshot.youtube_video_id == "yt-wal-123"
    assert snapshot.source == AnalyticsSource.LEGACY_UNVERIFIED

    history = tracker.get_project_snapshots(project.id)
    assert len(history) == 1
    assert history[0].views == 1500
    assert history[0].source == AnalyticsSource.LEGACY_UNVERIFIED


def test_record_analytics_snapshot_fails_if_unreleased(repo):
    channel = Channel(
        id="chan-err",
        title="Err",
        handle="@Err",
        niche="Tech",
        target_audience="Devs",
    )
    repo.save_channel(channel)
    project = VideoProject(
        id="proj-unreleased",
        channel_id=channel.id,
        title="Draft Only",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    tracker = YouTubeAnalyticsTracker(repo)
    with pytest.raises(AnalyticsTrackerError, match="requires project in PUBLISHED or SCHEDULED"):
        tracker.record_snapshot(
            project_id=project.id,
            views=100,
            watch_time_hours=2.0,
            ctr_percent=5.0,
            average_view_duration_seconds=30.0,
        )


def test_strategy_feedback_analysis_and_scoring(repo):
    # Setup 2 published projects
    p1 = _setup_published_project(repo, "proj-hit-01", "SQLite WAL Performance Tuning")
    p2 = _setup_published_project(repo, "proj-low-01", "Basic SQL Syntax Overview")

    # Authoritative API observations (source=YOUTUBE_ANALYTICS_API)
    # P1 is high performer
    repo.save_analytics_snapshot(
        AnalyticsSnapshot(
            id="snap-strat-p1",
            project_id=p1.id,
            source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
            report_start_date="2026-09-01",
            report_end_date="2026-09-20",
            views=5000,
            watch_time_hours=150.0,
            ctr_percent=9.5,
            average_view_duration_seconds=120.0,
            retention_at_3s_percent=70.0,
        )
    )
    # P2 is lower performer
    repo.save_analytics_snapshot(
        AnalyticsSnapshot(
            id="snap-strat-p2",
            project_id=p2.id,
            source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
            report_start_date="2026-09-01",
            report_end_date="2026-09-20",
            views=800,
            watch_time_hours=18.0,
            ctr_percent=3.2,
            average_view_duration_seconds=45.0,
            retention_at_3s_percent=42.0,
        )
    )

    feedback = StrategyFeedbackLoop(repo)
    analysis = feedback.analyze_channel_performance("chan-strat-01")

    assert analysis["total_snapshots"] == 2
    assert analysis["has_data"] is True
    assert analysis["mean_views"] == 2900.0
    assert len(analysis["top_projects"]) == 1
    assert analysis["top_projects"][0]["title"] == "SQLite WAL Performance Tuning"

    # Test historical fit score
    # Keyword matching top performer should score higher than unrelated keyword
    wal_score = feedback.compute_historical_fit_score("SQLite WAL Optimization", "chan-strat-01")
    unrelated_score = feedback.compute_historical_fit_score("Docker Networking Fundamentals", "chan-strat-01")

    assert wal_score > unrelated_score
    assert wal_score > 6.0
