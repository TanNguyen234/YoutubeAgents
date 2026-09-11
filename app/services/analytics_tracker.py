"""Stage 14 YouTube Analytics Tracker ingesting and persisting video performance metrics."""

from datetime import datetime, timezone
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.db.repository import SQLiteRepository
from app.domain.enums import VideoLifecycleState
from app.domain.models import AnalyticsSnapshot, VideoProject


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
        ctr_percent: float,
        average_view_duration_seconds: float,
        retention_at_3s_percent: Optional[float] = None,
        youtube_video_id: Optional[str] = None,
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
            if not video_id:
                video_id = f"yt-auto-{project_id[:8]}"

        snapshot_id = f"snap-{uuid4().hex[:8]}"
        snapshot = AnalyticsSnapshot(
            id=snapshot_id,
            project_id=project_id,
            youtube_video_id=video_id,
            views=max(0, views),
            watch_time_hours=max(0.0, float(watch_time_hours)),
            ctr_percent=max(0.0, min(100.0, float(ctr_percent))),
            average_view_duration_seconds=max(0.0, float(average_view_duration_seconds)),
            retention_at_3s_percent=retention_at_3s_percent,
            captured_at=datetime.now(timezone.utc),
        )

        self.repo.save_analytics_snapshot(snapshot)
        return snapshot

    def get_project_snapshots(self, project_id: str) -> List[AnalyticsSnapshot]:
        """Retrieve historical performance snapshots for a given project."""
        return self.repo.get_analytics_snapshots(project_id)
