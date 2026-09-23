"""Stage 14 YouTube Analytics Tracker ingesting and persisting video performance metrics."""

from datetime import datetime, timezone
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.db.repository import SQLiteRepository
from app.domain.enums import AnalyticsSource, VideoLifecycleState
from app.domain.models import AnalyticsSnapshot, RetentionPoint, VideoProject


class AnalyticsTrackerError(RuntimeError):
    """Raised when analytics tracking preconditions fail."""
    pass


class YouTubeAnalyticsTracker:
    """Ingests, parses, and persists YouTube Analytics performance metrics for published projects."""

    def __init__(self, repository: SQLiteRepository):
        self.repo = repository

    def record_snapshot(
        self,
        project_id: str,
        views: int,
        watch_time_hours: float,
        ctr_percent: Optional[float] = None,
        average_view_duration_seconds: float = 0.0,
        retention_at_3s_percent: Optional[float] = None,
        youtube_video_id: Optional[str] = None,
        is_simulated: bool = False,
        source: Optional[AnalyticsSource] = None,
        report_start_date: Optional[str] = None,
        report_end_date: Optional[str] = None,
        average_view_percentage: Optional[float] = None,
        impressions: Optional[int] = None,
        retention_curve: Optional[List[RetentionPoint]] = None,
    ) -> AnalyticsSnapshot:
        """Create and persist a performance snapshot for a project."""
        project = self.repo.get_video_project(project_id)
        if not project:
            raise AnalyticsTrackerError(f"Project '{project_id}' not found.")

        # Require published or scheduled state
        if project.state not in (VideoLifecycleState.PUBLISHED, VideoLifecycleState.SCHEDULED):
            raise AnalyticsTrackerError(
                f"Analytics tracking requires project in PUBLISHED or SCHEDULED state, but '{project_id}' is in {project.state.value}."
            )

        video_id = youtube_video_id
        if not video_id:
            # Look up from publication queue/jobs
            jobs = self.repo.get_publication_queue()
            for j in jobs:
                if j.project_id == project_id and j.youtube_video_id:
                    video_id = j.youtube_video_id
                    break

        if is_simulated:
            snapshot_type = "SIMULATED"
            if not video_id:
                video_id = f"sim-{project_id[:8]}"
            chosen_source = source or AnalyticsSource.SIMULATED
        else:
            snapshot_type = "REAL"
            # NOTE: record_snapshot is a manual compatibility utility and defaults to LEGACY_UNVERIFIED.
            # Authoritative YOUTUBE_ANALYTICS_API production evidence can ONLY be created by
            # YouTubeAnalyticsIngestionService successfully parsing genuine YouTube Analytics API responses.
            chosen_source = source or AnalyticsSource.LEGACY_UNVERIFIED
            # Do NOT invent fake youtube video ID like yt-auto-...

        ctr_val = max(0.0, min(100.0, float(ctr_percent))) if ctr_percent is not None else None
        snapshot_id = f"snap-{uuid4().hex[:8]}"
        snapshot = AnalyticsSnapshot(
            id=snapshot_id,
            project_id=project_id,
            youtube_video_id=video_id,
            source=chosen_source,
            snapshot_type=snapshot_type,
            is_simulated=is_simulated,
            report_start_date=report_start_date,
            report_end_date=report_end_date,
            views=max(0, views),
            watch_time_hours=max(0.0, float(watch_time_hours)),
            average_view_duration_seconds=max(0.0, float(average_view_duration_seconds)),
            average_view_percentage=average_view_percentage,
            impressions=impressions,
            ctr_percent=ctr_val,
            retention_at_3s_percent=retention_at_3s_percent,
            retention_curve=retention_curve or [],
            captured_at=datetime.now(timezone.utc),
        )

        self.repo.save_analytics_snapshot(snapshot)
        return snapshot

    def get_project_snapshots(self, project_id: str) -> List[AnalyticsSnapshot]:
        """Retrieve historical performance snapshots for a given project."""
        return self.repo.get_analytics_snapshots(project_id)
