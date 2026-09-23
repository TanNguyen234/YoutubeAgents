"""YouTube Analytics API v2 Ingestion Service.

Fetches genuine playback metrics and retention curve observations from YouTube Analytics API,
computes approximate 3-second hook retention via linear interpolation, and persists
trusted AnalyticsSnapshot records to SQLite.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4
import httpx

from app.db.repository import Repository, SQLiteRepository
from app.domain.enums import AnalyticsCollectionStatus, AnalyticsSource, PublicationStatus
from app.domain.models import AnalyticsCollectionResult, AnalyticsSnapshot, RetentionPoint
from app.services.youtube_oauth import (
    YouTubeOAuthError,
    YouTubeOAuthManager,
    YOUTUBE_ANALYTICS_READONLY_SCOPE,
)

logger = logging.getLogger("youtube_analytics_ingestion")


class AnalyticsIngestionError(RuntimeError):
    """Base exception for YouTube Analytics ingestion failures."""
    pass


class AnalyticsAuthScopeError(AnalyticsIngestionError):
    """Raised when YouTube Analytics API rejects request due to missing yt-analytics.readonly scope."""
    pass


class AnalyticsHttpError(AnalyticsIngestionError):
    """Raised when YouTube Analytics API returns non-200 HTTP error."""
    def __init__(self, status_code: int, message: str, response_body: str = ""):
        super().__init__(f"YouTube Analytics API error ({status_code}): {message}")
        self.status_code = status_code
        self.response_body = response_body


class AnalyticsNetworkError(AnalyticsIngestionError):
    """Raised on network transport or timeout failures contacting YouTube Analytics API."""
    pass


def compute_approximate_3s_retention(
    retention_curve: List[RetentionPoint],
    duration_seconds: float,
) -> Optional[float]:
    """Calculate approximate 3-second retention percentage via linear interpolation.

    Args:
        retention_curve: Ordered list of RetentionPoint from YouTube Analytics API.
        duration_seconds: Total duration of the video in seconds.

    Returns:
        Estimated retention percentage (e.g. 78.5 for 78.5%) or None if insufficient data.
        Note: Does NOT clamp to 100% because audienceWatchRatio can exceed 1.0 on rewinds.
    """
    if not retention_curve or duration_seconds <= 0:
        return None

    target_ratio = min(3.0 / duration_seconds, 1.0)
    sorted_curve = sorted(retention_curve, key=lambda p: p.elapsed_video_time_ratio)

    if len(sorted_curve) == 1:
        return round(float(sorted_curve[0].audience_watch_ratio) * 100.0, 4)

    if target_ratio <= sorted_curve[0].elapsed_video_time_ratio:
        return round(float(sorted_curve[0].audience_watch_ratio) * 100.0, 4)

    if target_ratio >= sorted_curve[-1].elapsed_video_time_ratio:
        return round(float(sorted_curve[-1].audience_watch_ratio) * 100.0, 4)

    for i in range(len(sorted_curve) - 1):
        p0 = sorted_curve[i]
        p1 = sorted_curve[i + 1]
        if p0.elapsed_video_time_ratio <= target_ratio <= p1.elapsed_video_time_ratio:
            span = p1.elapsed_video_time_ratio - p0.elapsed_video_time_ratio
            if span <= 0:
                return round(float(p0.audience_watch_ratio) * 100.0, 4)
            t = (target_ratio - p0.elapsed_video_time_ratio) / span
            interp = p0.audience_watch_ratio + t * (p1.audience_watch_ratio - p0.audience_watch_ratio)
            return round(float(interp) * 100.0, 4)

    return round(float(sorted_curve[-1].audience_watch_ratio) * 100.0, 4)


class YouTubeAnalyticsIngestionService:
    """Service to ingest authentic performance and retention data from YouTube Analytics API."""

    def __init__(
        self,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        repository: Optional[Repository] = None,
        http_client: Optional[httpx.Client] = None,
        api_base_url: str = "https://youtubeanalytics.googleapis.com/v2/reports",
    ):
        self.oauth_manager = oauth_manager
        self.repository = repository
        self._custom_http_client = http_client
        self.api_base_url = api_base_url

    def _get_client(self) -> httpx.Client:
        if self._custom_http_client is not None:
            return self._custom_http_client
        return httpx.Client(timeout=30.0)

    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.oauth_manager:
            raise AnalyticsAuthScopeError(
                "No YouTubeOAuthManager configured. Re-run 'python scripts/setup_youtube_auth.py' to authenticate."
            )
        try:
            token = self.oauth_manager.get_access_token()
        except YouTubeOAuthError as e:
            raise AnalyticsAuthScopeError(
                f"Failed to obtain valid YouTube OAuth token: {e}. "
                "Please authenticate by running 'python scripts/setup_youtube_auth.py'."
            ) from e
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _parse_result_table(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        headers = [col.get("name") for col in data.get("columnHeaders", [])]
        rows = data.get("rows", [])
        parsed: List[Dict[str, Any]] = []
        for row in rows:
            record: Dict[str, Any] = {}
            for idx, col_name in enumerate(headers):
                if col_name and idx < len(row):
                    record[col_name] = row[idx]
            parsed.append(record)
        return parsed

    def _check_and_raise_error(self, response: httpx.Response) -> None:
        if response.status_code == 200:
            return

        body_text = response.text
        if response.status_code == 403:
            # Check for scope or permission failure
            lowered = body_text.lower()
            if any(term in lowered for term in ["scope", "insufficientpermissions", "forbidden", "access_token_scope_insufficient"]):
                raise AnalyticsAuthScopeError(
                    f"YouTube Analytics API rejected request with 403 Forbidden: Missing scope '{YOUTUBE_ANALYTICS_READONLY_SCOPE}'. "
                    f"To resolve: Ensure YouTube Analytics API is enabled in Google Cloud Console, and re-authenticate "
                    f"by running 'python scripts/setup_youtube_auth.py'."
                )

        raise AnalyticsHttpError(
            status_code=response.status_code,
            message=response.reason_phrase or "HTTP Error",
            response_body=body_text,
        )

    def query_playback_metrics(
        self,
        video_id: str,
        start_date: str,
        end_date: str,
    ) -> Optional[Dict[str, Any]]:
        """Query core playback metrics for a given video over a date window."""
        headers = self._get_auth_headers()
        params = {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,likes",
            "filters": f"video=={video_id}",
        }

        try:
            client = self._get_client()
            response = client.get(self.api_base_url, params=params, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AnalyticsNetworkError(f"Network error querying YouTube Analytics: {exc}") from exc

        self._check_and_raise_error(response)
        data = response.json()
        rows = self._parse_result_table(data)
        if not rows:
            return None
        return rows[0]

    def query_retention_curve(
        self,
        video_id: str,
        start_date: str,
        end_date: str,
    ) -> List[RetentionPoint]:
        """Query audience retention curve for a given video over a date window."""
        headers = self._get_auth_headers()
        params = {
            "ids": "channel==MINE",
            "startDate": start_date,
            "endDate": end_date,
            "metrics": "audienceWatchRatio,relativeRetentionPerformance",
            "dimensions": "elapsedVideoTimeRatio",
            "filters": f"video=={video_id}",
        }

        try:
            client = self._get_client()
            response = client.get(self.api_base_url, params=params, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AnalyticsNetworkError(f"Network error querying YouTube retention curve: {exc}") from exc

        self._check_and_raise_error(response)
        data = response.json()
        rows = self._parse_result_table(data)

        points: List[RetentionPoint] = []
        for r in rows:
            elapsed = float(r.get("elapsedVideoTimeRatio", 0.0))
            watch_ratio = float(r.get("audienceWatchRatio", 0.0))
            rel_perf = r.get("relativeRetentionPerformance")
            rel_perf_val = float(rel_perf) if rel_perf is not None else None
            points.append(
                RetentionPoint(
                    elapsed_video_time_ratio=elapsed,
                    audience_watch_ratio=watch_ratio,
                    relative_retention_performance=rel_perf_val,
                )
            )

        points.sort(key=lambda p: p.elapsed_video_time_ratio)
        return points

    def ingest_project_analytics(
        self,
        project_id: str,
        video_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> AnalyticsCollectionResult:
        """Collect genuine playback & retention observations and store in database."""
        # 1. Resolve video_id and duration if not directly provided
        project = None
        if self.repository:
            if hasattr(self.repository, "get_project"):
                project = self.repository.get_project(project_id)
            elif hasattr(self.repository, "get_video_project"):
                project = self.repository.get_video_project(project_id)

        if not video_id:
            if project and getattr(project, "youtube_video_id", None):
                video_id = project.youtube_video_id
            elif self.repository:
                if hasattr(self.repository, "get_publication_job_by_project"):
                    pub_job = self.repository.get_publication_job_by_project(project_id)
                    if pub_job and pub_job.youtube_video_id:
                        video_id = pub_job.youtube_video_id
                if not video_id and hasattr(self.repository, "get_publication_queue"):
                    jobs = self.repository.get_publication_queue(status=PublicationStatus.COMPLETED)
                    for j in jobs:
                        if j.project_id == project_id and j.youtube_video_id:
                            video_id = j.youtube_video_id
                            break

        if not video_id:
            return AnalyticsCollectionResult(
                project_id=project_id,
                video_id=None,
                status=AnalyticsCollectionStatus.NO_YOUTUBE_VIDEO_ID,
                error_message=f"Project '{project_id}' has no published YouTube video ID.",
            )

        if duration_seconds is None and project:
            if getattr(project, "quality", None) and project.quality.duration_seconds:
                duration_seconds = float(project.quality.duration_seconds)
            elif project.script and getattr(project.script, "estimated_duration_seconds", None):
                duration_seconds = float(project.script.estimated_duration_seconds)

        # 2. Compute date window (default: today-2d to account for YouTube processing lag)
        now_utc = datetime.now(timezone.utc)
        if not end_date:
            end_date = (now_utc - timedelta(days=2)).strftime("%Y-%m-%d")
        if not start_date:
            # Default start date: 28 days before end date
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            start_date = (end_dt - timedelta(days=28)).strftime("%Y-%m-%d")

        if start_date > end_date:
            start_date = end_date

        # 3. Query playback metrics
        try:
            playback_data = self.query_playback_metrics(video_id, start_date, end_date)
        except AnalyticsAuthScopeError as e:
            return AnalyticsCollectionResult(
                project_id=project_id,
                video_id=video_id,
                status=AnalyticsCollectionStatus.NO_DATA_YET,
                error_message=f"Authentication scope error: {e}",
            )
        except AnalyticsHttpError as e:
            return AnalyticsCollectionResult(
                project_id=project_id,
                video_id=video_id,
                status=AnalyticsCollectionStatus.NO_DATA_YET,
                error_message=f"YouTube Analytics HTTP error ({e.status_code}): {e}",
            )

        if not playback_data:
            return AnalyticsCollectionResult(
                project_id=project_id,
                video_id=video_id,
                status=AnalyticsCollectionStatus.NO_DATA_YET,
                error_message=f"No playback metrics returned for video {video_id} between {start_date} and {end_date}.",
            )

        views = int(playback_data.get("views", 0))
        est_minutes = float(playback_data.get("estimatedMinutesWatched", 0.0))
        watch_time_hours = est_minutes / 60.0
        avg_view_duration = float(playback_data.get("averageViewDuration", 0.0))
        raw_avg_view_pct = playback_data.get("averageViewPercentage")
        avg_view_percentage = float(raw_avg_view_pct) if raw_avg_view_pct is not None else None
        subs_gained = int(playback_data.get("subscribersGained", 0))
        likes = int(playback_data.get("likes", 0))

        # If views is 0 and no watch time, data is not ready yet
        if views == 0 and est_minutes == 0.0:
            return AnalyticsCollectionResult(
                project_id=project_id,
                video_id=video_id,
                status=AnalyticsCollectionStatus.NO_DATA_YET,
                error_message=f"Video {video_id} returned 0 views and 0 watch time in window {start_date} to {end_date}.",
            )

        # 4. Query retention curve
        retention_curve: List[RetentionPoint] = []
        try:
            retention_curve = self.query_retention_curve(video_id, start_date, end_date)
        except Exception as e:
            logger.warning(f"Could not fetch retention curve for video {video_id}: {e}")

        # 5. Approximate 3s retention
        retention_at_3s = None
        if retention_curve and duration_seconds and duration_seconds > 0:
            retention_at_3s = compute_approximate_3s_retention(retention_curve, duration_seconds)

        # 6. Build trusted snapshot (strictly nullable CTR)
        snapshot = AnalyticsSnapshot(
            id=f"snap-{uuid4().hex[:8]}",
            project_id=project_id,
            youtube_video_id=video_id,
            views=views,
            watch_time_hours=watch_time_hours,
            average_view_duration_seconds=avg_view_duration,
            average_view_percentage=avg_view_percentage,
            ctr_percent=None,  # Strictly None (Reach API out of scope)
            impressions=None,
            retention_at_3s_percent=retention_at_3s,
            retention_curve=retention_curve,
            source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
            report_start_date=start_date,
            report_end_date=end_date,
            is_simulated=False,
        )

        # 7. Persist to database if repository available
        if self.repository:
            self.repository.save_analytics_snapshot(snapshot)

        return AnalyticsCollectionResult(
            project_id=project_id,
            video_id=video_id,
            status=AnalyticsCollectionStatus.COLLECTED,
            snapshot=snapshot,
        )
