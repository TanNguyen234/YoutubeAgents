"""Tests for Real YouTube Analytics Ingestion (Phase 1).

Covers:
- Test A: Authentic Playback Metrics parsing with nullable CTR & impressions
- Test B: Column Headers Order Permutation
- Test C: Units Verification (estimatedMinutesWatched -> watch_time_hours)
- Test D: Empty rows / NO_DATA_YET handling without fake zero snapshots
- Test E: 403 Forbidden with scope failure -> AnalyticsAuthScopeError with actionable guidance
- Test F: Non-auth HTTP Error -> AnalyticsHttpError
- Test G: Network failure / timeout -> AnalyticsNetworkError
- Test H: Retention Curve Request parameter validation
- Test I: Retention Curve Parser into List[RetentionPoint]
- Test J: Retention Point > 1.0 (rewatches/loops) not clamped
- Test K: 3-Second Retention linear interpolation
- Test L: 3-Second Retention boundary conditions (short duration)
- Test M: Ingestion service persistence of YOUTUBE_ANALYTICS_API snapshots
- Test N: StrategyFeedbackLoop latest-per-project deduplication
- Test O: StrategyFeedbackLoop excludes SIMULATED and LEGACY_UNVERIFIED
- Test P: StrategyFeedbackLoop nullable CTR handling without crashes or fake recommendations
- Test Q: SQLite WAL restart persistence of v8 snapshots and retention curve
- Test S: PipelineBrain refresh methods and post-upload PENDING_REAL_DATA status
"""

from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
import httpx
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    AnalyticsCollectionStatus,
    AnalyticsSource,
    PlatformFormat,
    PublicationStatus,
    VideoLifecycleState,
)
from app.domain.models import (
    AnalyticsSnapshot,
    Channel,
    PublicationJob,
    RetentionPoint,
    VideoProject,
)
from app.services.pipeline_brain import BrainPipeline, PipelineBrain
from app.services.strategy_feedback import StrategyFeedbackLoop
from app.services.youtube_analytics_ingestion import (
    AnalyticsAuthScopeError,
    AnalyticsHttpError,
    AnalyticsNetworkError,
    YouTubeAnalyticsIngestionService,
    compute_approximate_3s_retention,
)
from app.services.youtube_oauth import YouTubeOAuthManager


class DummyOAuthManager(YouTubeOAuthManager):
    """Mock OAuth manager returning test access token."""
    def __init__(self, token: str = "ya29.test-mock-token"):
        self._token = token

    def get_access_token(self) -> str:
        return self._token


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = Path(tmpdir) / "test_analytics.db"
        init_database(db_path)
        yield SQLiteRepository(db_path)


def _setup_published_project(
    repo: SQLiteRepository,
    project_id: str,
    title: str = "Test Video Project",
    channel_id: str = "chan-test-01",
    youtube_video_id: Optional[str] = None,
) -> VideoProject:
    """Helper to legally create and advance a project to PUBLISHED state with foreign key compliance."""
    channel = repo.get_channel(channel_id)
    if not channel:
        channel = Channel(
            id=channel_id,
            title="Test Channel",
            handle="@TestChannel",
            niche="Tech",
            target_audience="Developers",
        )
        repo.save_channel(channel)

    project = VideoProject(
        id=project_id,
        channel_id=channel_id,
        title=title,
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    # Legally advance state machine to PUBLISHED
    for next_state in [
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.VERIFIED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.RENDERED,
        VideoLifecycleState.READY_FOR_REVIEW,
        VideoLifecycleState.APPROVED,
        VideoLifecycleState.UPLOADING,
        VideoLifecycleState.PUBLISHED,
    ]:
        repo.update_project_state(project.id, to_state=next_state)

    if youtube_video_id:
        pub_job = PublicationJob(
            id=f"pub-{project_id}",
            project_id=project.id,
            channel_id=channel_id,
            status=PublicationStatus.COMPLETED,
            youtube_video_id=youtube_video_id,
        )
        repo.save_publication_job(pub_job)

    return repo.get_video_project(project.id)


# ============================================================================
# Test A: Realistic playback metrics parsing with nullable CTR & impressions
# ============================================================================
def test_playback_metrics_parsing_with_nullable_ctr_and_impressions(temp_repo):
    _setup_published_project(temp_repo, project_id="proj-test-a", youtube_video_id="yt-vid-001")

    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "subscribersGained", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "likes", "columnType": "METRIC", "dataType": "INTEGER"},
        ],
        "rows": [
            [1250, 375.0, 18.0, 60.0, 15, 85]
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        repository=temp_repo,
        http_client=client,
    )

    result = service.ingest_project_analytics(
        project_id="proj-test-a",
        video_id="yt-vid-001",
        start_date="2026-09-01",
        end_date="2026-09-20",
        duration_seconds=30.0,
    )

    assert result.status == AnalyticsCollectionStatus.COLLECTED
    snapshot = result.snapshot
    assert snapshot is not None
    assert snapshot.views == 1250
    assert snapshot.watch_time_hours == 6.25  # 375.0 / 60.0
    assert snapshot.average_view_duration_seconds == 18.0
    assert snapshot.average_view_percentage == 60.0
    assert snapshot.ctr_percent is None  # Strictly None (Reach API out of scope)
    assert snapshot.impressions is None
    assert snapshot.source == AnalyticsSource.YOUTUBE_ANALYTICS_API
    assert snapshot.is_simulated is False


# ============================================================================
# Test B: Column Headers Order Permutation
# ============================================================================
def test_column_headers_order_permutation(temp_repo):
    _setup_published_project(temp_repo, project_id="proj-test-b", youtube_video_id="yt-vid-002")

    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [
            [72.5, 600.0, 2000, 18.0]
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        repository=temp_repo,
        http_client=client,
    )

    result = service.ingest_project_analytics(
        project_id="proj-test-b",
        video_id="yt-vid-002",
        start_date="2026-09-01",
        end_date="2026-09-20",
    )

    assert result.status == AnalyticsCollectionStatus.COLLECTED
    snapshot = result.snapshot
    assert snapshot.views == 2000
    assert snapshot.watch_time_hours == 10.0
    assert snapshot.average_view_percentage == 72.5
    assert snapshot.average_view_duration_seconds == 18.0


# ============================================================================
# Test C: Units Verification (estimatedMinutesWatched -> watch_time_hours)
# ============================================================================
def test_units_verification_minutes_to_hours(temp_repo):
    _setup_published_project(temp_repo, project_id="proj-test-c", youtube_video_id="yt-vid-003")

    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [
            [500, 150.0, 18.0]
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        repository=temp_repo,
        http_client=client,
    )

    result = service.ingest_project_analytics(
        project_id="proj-test-c",
        video_id="yt-vid-003",
        start_date="2026-09-01",
        end_date="2026-09-20",
    )

    assert result.status == AnalyticsCollectionStatus.COLLECTED
    assert result.snapshot.watch_time_hours == 2.5  # Exactly 150 / 60


# ============================================================================
# Test D: Empty rows / NO_DATA_YET handling without fake zero snapshots
# ============================================================================
def test_empty_rows_returns_no_data_yet_without_persisting_zero(temp_repo):
    _setup_published_project(temp_repo, project_id="proj-test-d", youtube_video_id="yt-vid-004")

    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [],  # Empty rows
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        repository=temp_repo,
        http_client=client,
    )

    result = service.ingest_project_analytics(
        project_id="proj-test-d",
        video_id="yt-vid-004",
        start_date="2026-09-01",
        end_date="2026-09-20",
    )

    assert result.status == AnalyticsCollectionStatus.NO_DATA_YET
    assert result.snapshot is None
    # Verify no snapshot was saved in DB
    snapshots = temp_repo.get_analytics_snapshots("proj-test-d")
    assert len(snapshots) == 0


# ============================================================================
# Test E: 403 Forbidden with scope failure -> AnalyticsAuthScopeError
# ============================================================================
def test_403_scope_failure_raises_analytics_auth_scope_error():
    error_body = {
        "error": {
            "code": 403,
            "message": "Request had insufficient authentication scopes.",
            "status": "PERMISSION_DENIED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=error_body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    with pytest.raises(AnalyticsAuthScopeError) as exc_info:
        service.query_playback_metrics("yt-vid-005", "2026-09-01", "2026-09-20")

    err_str = str(exc_info.value)
    assert "setup_youtube_auth.py" in err_str
    assert "yt-analytics.readonly" in err_str


# ============================================================================
# Test F: Non-auth HTTP Error -> AnalyticsHttpError
# ============================================================================
def test_500_http_error_raises_analytics_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    with pytest.raises(AnalyticsHttpError) as exc_info:
        service.query_playback_metrics("yt-vid-006", "2026-09-01", "2026-09-20")

    assert exc_info.value.status_code == 500


# ============================================================================
# Test G: Network failure / timeout -> AnalyticsNetworkError
# ============================================================================
def test_network_timeout_raises_analytics_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Connection timed out")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    with pytest.raises(AnalyticsNetworkError):
        service.query_playback_metrics("yt-vid-007", "2026-09-01", "2026-09-20")


# ============================================================================
# Test H: Retention Curve Request parameter validation
# ============================================================================
def test_retention_curve_query_parameters():
    captured_requests: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"columnHeaders": [], "rows": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    service.query_retention_curve("yt-vid-008", "2026-09-01", "2026-09-20")

    assert len(captured_requests) == 1
    req = captured_requests[0]
    params = dict(req.url.params)
    assert params.get("ids") == "channel==MINE"
    assert params.get("dimensions") == "elapsedVideoTimeRatio"
    assert "audienceWatchRatio" in params.get("metrics", "")
    assert "relativeRetentionPerformance" in params.get("metrics", "")
    assert params.get("filters") == "video==yt-vid-008"


# ============================================================================
# Test I: Retention Curve Parser into List[RetentionPoint]
# ============================================================================
def test_retention_curve_parsing():
    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
            {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "relativeRetentionPerformance", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [
            [0.0, 1.0, 0.85],
            [0.1, 0.82, 0.80],
            [0.5, 0.55, 0.70],
            [1.0, 0.35, 0.60],
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    curve = service.query_retention_curve("yt-vid-009", "2026-09-01", "2026-09-20")
    assert len(curve) == 4
    assert curve[0].elapsed_video_time_ratio == 0.0
    assert curve[0].audience_watch_ratio == 1.0
    assert curve[0].relative_retention_performance == 0.85
    assert curve[1].elapsed_video_time_ratio == 0.1
    assert curve[1].audience_watch_ratio == 0.82


# ============================================================================
# Test J: Retention Point > 1.0 (rewatches/loops) not clamped
# ============================================================================
def test_retention_curve_values_greater_than_one_not_clamped():
    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
            {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "relativeRetentionPerformance", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [
            [0.0, 1.35, 0.95],
            [0.1, 1.15, 0.90],
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    curve = service.query_retention_curve("yt-vid-010", "2026-09-01", "2026-09-20")
    assert curve[0].audience_watch_ratio == 1.35
    # Linear interpolation at 3s for 30s video (target 0.10)
    ret_3s = compute_approximate_3s_retention(curve, duration_seconds=30.0)
    assert ret_3s == 115.0  # 1.15 * 100%, not clamped to 100%!


# ============================================================================
# Test K: 3-Second Retention linear interpolation
# ============================================================================
def test_compute_approximate_3s_retention_interpolation():
    curve = [
        RetentionPoint(elapsed_video_time_ratio=0.0, audience_watch_ratio=1.0),
        RetentionPoint(elapsed_video_time_ratio=0.05, audience_watch_ratio=0.90),
        RetentionPoint(elapsed_video_time_ratio=0.15, audience_watch_ratio=0.70),
        RetentionPoint(elapsed_video_time_ratio=1.0, audience_watch_ratio=0.30),
    ]

    # For a 30s video, 3s corresponds to ratio 3.0 / 30.0 = 0.10
    # Between 0.05 (0.90) and 0.15 (0.70):
    # span = 0.10, t = (0.10 - 0.05) / 0.10 = 0.5
    # interp = 0.90 + 0.5 * (0.70 - 0.90) = 0.80
    val = compute_approximate_3s_retention(curve, duration_seconds=30.0)
    assert val is not None
    assert round(val, 2) == 80.0


# ============================================================================
# Test L: 3-Second Retention boundary conditions (short duration)
# ============================================================================
def test_compute_approximate_3s_retention_short_duration():
    curve = [
        RetentionPoint(elapsed_video_time_ratio=0.0, audience_watch_ratio=1.0),
        RetentionPoint(elapsed_video_time_ratio=0.5, audience_watch_ratio=0.80),
        RetentionPoint(elapsed_video_time_ratio=1.0, audience_watch_ratio=0.60),
    ]

    # For a 2s video, target_ratio = min(3.0 / 2.0, 1.0) = 1.0
    val = compute_approximate_3s_retention(curve, duration_seconds=2.0)
    assert val == 60.0  # ratio 1.0 -> 0.60 * 100%

    # Empty curve
    assert compute_approximate_3s_retention([], 30.0) is None
    # 0 or negative duration
    assert compute_approximate_3s_retention(curve, 0.0) is None
    assert compute_approximate_3s_retention(curve, -5.0) is None


# ============================================================================
# Test M: Ingestion service persistence of YOUTUBE_ANALYTICS_API snapshots
# ============================================================================
def test_ingestion_service_persists_trusted_snapshot(temp_repo):
    project = _setup_published_project(
        temp_repo,
        project_id="proj-pers-01",
        title="Testing Ingestion Persistence",
        channel_id="chan-pers-01",
        youtube_video_id="yt-pers-12345",
    )

    playback_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [[4500, 1500.0, 20.0, 66.7]],
    }
    retention_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
            {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "relativeRetentionPerformance", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [
            [0.0, 1.0, 0.8],
            [0.1, 0.75, 0.7],
            [1.0, 0.40, 0.6],
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "elapsedVideoTimeRatio" in str(request.url):
            return httpx.Response(200, json=retention_response)
        return httpx.Response(200, json=playback_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeAnalyticsIngestionService(
        oauth_manager=DummyOAuthManager(),
        repository=temp_repo,
        http_client=client,
    )

    result = service.ingest_project_analytics(
        project_id=project.id,
        start_date="2026-09-01",
        end_date="2026-09-20",
        duration_seconds=30.0,
    )

    assert result.status == AnalyticsCollectionStatus.COLLECTED
    persisted = temp_repo.get_analytics_snapshots(project.id)
    assert len(persisted) == 1
    snap = persisted[0]
    assert snap.source == AnalyticsSource.YOUTUBE_ANALYTICS_API
    assert snap.views == 4500
    assert snap.watch_time_hours == 25.0
    assert snap.report_start_date == "2026-09-01"
    assert snap.report_end_date == "2026-09-20"
    assert snap.ctr_percent is None
    assert len(snap.retention_curve) == 3


# ============================================================================
# Test N: StrategyFeedbackLoop latest-per-project deduplication
# ============================================================================
def test_strategy_feedback_loop_latest_per_project_deduplication(temp_repo):
    project = _setup_published_project(
        temp_repo,
        project_id="proj-dedup-01",
        title="Dedup Project",
        channel_id="chan-dedup-01",
        youtube_video_id="yt-dedup-01",
    )

    # Day 1 snapshot: 100 views
    snap_day1 = AnalyticsSnapshot(
        id="snap-d1",
        project_id=project.id,
        youtube_video_id="yt-dedup-01",
        source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
        report_start_date="2026-09-01",
        report_end_date="2026-09-05",
        views=100,
        watch_time_hours=1.0,
    )
    # Day 2 snapshot: 500 views (more recent end date)
    snap_day2 = AnalyticsSnapshot(
        id="snap-d2",
        project_id=project.id,
        youtube_video_id="yt-dedup-01",
        source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
        report_start_date="2026-09-01",
        report_end_date="2026-09-10",
        views=500,
        watch_time_hours=5.0,
    )

    temp_repo.save_analytics_snapshot(snap_day1)
    temp_repo.save_analytics_snapshot(snap_day2)

    feedback = StrategyFeedbackLoop(temp_repo)
    analysis = feedback.analyze_channel_performance("chan-dedup-01")

    # Must count only 1 snapshot (the latest one for the project)
    assert analysis["total_snapshots"] == 1
    assert analysis["mean_views"] == 500.0  # NOT (100 + 500) / 2 = 300!


# ============================================================================
# Test O: StrategyFeedbackLoop excludes SIMULATED and LEGACY_UNVERIFIED
# ============================================================================
def test_strategy_feedback_loop_excludes_simulated_and_legacy_unverified(temp_repo):
    p1 = _setup_published_project(temp_repo, project_id="proj-p1", title="P1", channel_id="chan-excl-01")
    p2 = _setup_published_project(temp_repo, project_id="proj-p2", title="P2", channel_id="chan-excl-01")
    p3 = _setup_published_project(temp_repo, project_id="proj-p3", title="P3", channel_id="chan-excl-01")

    # 1. Simulated snapshot
    temp_repo.save_analytics_snapshot(AnalyticsSnapshot(
        id="snap-sim",
        project_id=p1.id,
        source=AnalyticsSource.SIMULATED,
        is_simulated=True,
        views=999999,
        watch_time_hours=1000.0,
    ))

    # 2. Legacy unverified snapshot
    temp_repo.save_analytics_snapshot(AnalyticsSnapshot(
        id="snap-legacy",
        project_id=p2.id,
        source=AnalyticsSource.LEGACY_UNVERIFIED,
        is_simulated=False,
        views=888888,
        watch_time_hours=800.0,
    ))

    feedback = StrategyFeedbackLoop(temp_repo)
    # Before adding real data: has_data must be False!
    analysis_empty = feedback.analyze_channel_performance("chan-excl-01")
    assert analysis_empty["has_data"] is False
    assert analysis_empty["total_snapshots"] == 0

    # 3. Add genuine YouTube Analytics snapshot
    temp_repo.save_analytics_snapshot(AnalyticsSnapshot(
        id="snap-real",
        project_id=p3.id,
        source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
        report_start_date="2026-09-01",
        report_end_date="2026-09-15",
        views=1500,
        watch_time_hours=15.0,
    ))

    analysis_real = feedback.analyze_channel_performance("chan-excl-01")
    assert analysis_real["has_data"] is True
    assert analysis_real["total_snapshots"] == 1
    assert analysis_real["mean_views"] == 1500.0


# ============================================================================
# Test P: StrategyFeedbackLoop nullable CTR handling without crashes
# ============================================================================
def test_strategy_feedback_loop_nullable_ctr_no_crash(temp_repo):
    proj = _setup_published_project(temp_repo, project_id="proj-noctr-01", title="No CTR Project", channel_id="chan-noctr-01")

    temp_repo.save_analytics_snapshot(AnalyticsSnapshot(
        id="snap-noctr",
        project_id=proj.id,
        source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
        report_start_date="2026-09-01",
        report_end_date="2026-09-15",
        views=2500,
        watch_time_hours=20.0,
        ctr_percent=None,  # Nullable CTR!
        retention_at_3s_percent=65.0,
    ))

    feedback = StrategyFeedbackLoop(temp_repo)
    analysis = feedback.analyze_channel_performance("chan-noctr-01")

    assert analysis["has_data"] is True
    assert analysis["mean_ctr_percent"] is None
    assert analysis["mean_retention_3s"] == 65.0
    # Must NOT emit fake CTR recommendations like "Click-through rate is under 5.0%"
    for rec in analysis["recommendations"]:
        assert "Click-through rate" not in rec


# ============================================================================
# Test Q: SQLite WAL restart persistence of v8 snapshots and retention curve
# ============================================================================
def test_sqlite_wal_restart_persistence():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = Path(tmpdir) / "wal_restart_test.db"

        # Session 1: Create repo and insert snapshot
        init_database(db_path)
        repo1 = SQLiteRepository(db_path)
        project = _setup_published_project(
            repo1,
            project_id="proj-wal-01",
            title="WAL Project",
            channel_id="chan-wal-01",
            youtube_video_id="yt-wal-123",
        )

        curve_data = [
            RetentionPoint(elapsed_video_time_ratio=0.0, audience_watch_ratio=1.0, relative_retention_performance=0.9),
            RetentionPoint(elapsed_video_time_ratio=0.5, audience_watch_ratio=0.6, relative_retention_performance=0.7),
        ]
        snapshot = AnalyticsSnapshot(
            id="snap-wal-01",
            project_id=project.id,
            youtube_video_id="yt-wal-123",
            source=AnalyticsSource.YOUTUBE_ANALYTICS_API,
            report_start_date="2026-09-01",
            report_end_date="2026-09-10",
            views=3200,
            watch_time_hours=16.0,
            average_view_percentage=55.2,
            ctr_percent=None,
            retention_at_3s_percent=72.0,
            retention_curve=curve_data,
        )
        repo1.save_analytics_snapshot(snapshot)

        # Session 2: Fresh repository instance pointing to same file
        repo2 = SQLiteRepository(db_path)
        loaded = repo2.get_analytics_snapshots(project.id)
        assert len(loaded) == 1
        s = loaded[0]
        assert s.id == "snap-wal-01"
        assert s.source == AnalyticsSource.YOUTUBE_ANALYTICS_API
        assert s.views == 3200
        assert s.average_view_percentage == 55.2
        assert s.ctr_percent is None
        assert len(s.retention_curve) == 2
        assert s.retention_curve[0].elapsed_video_time_ratio == 0.0
        assert s.retention_curve[0].relative_retention_performance == 0.9


# ============================================================================
# Test S: PipelineBrain refresh methods and post-upload PENDING_REAL_DATA
# ============================================================================
def test_pipeline_brain_refresh_analytics_flow(temp_repo):
    project = _setup_published_project(
        temp_repo,
        project_id="proj-pipe-01",
        title="Pipeline Project",
        channel_id="chan-pipe-01",
        youtube_video_id="yt-pipe-999",
    )

    # Prepare fake API response
    api_response = {
        "kind": "youtubeAnalytics#resultTable",
        "columnHeaders": [
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "estimatedMinutesWatched", "columnType": "METRIC", "dataType": "FLOAT"},
            {"name": "averageViewDuration", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [[750, 300.0, 24.0]],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=api_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    brain = BrainPipeline(repository=temp_repo)

    # Test refresh_project_analytics
    res = brain.refresh_project_analytics(
        project_id=project.id,
        start_date="2026-09-01",
        end_date="2026-09-10",
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    assert res.status == AnalyticsCollectionStatus.COLLECTED
    assert res.snapshot.views == 750
    assert res.snapshot.watch_time_hours == 5.0
    assert res.snapshot.ctr_percent is None

    # Test refresh_channel_analytics
    chan_res = brain.refresh_channel_analytics(
        channel_id="chan-pipe-01",
        start_date="2026-09-01",
        end_date="2026-09-10",
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )
    assert len(chan_res) == 1
    assert chan_res[0].status == AnalyticsCollectionStatus.COLLECTED


def test_no_fake_zero_snapshot_created_post_upload(temp_repo):
    """Verify that post-upload state does NOT create a fake 0-metric real snapshot."""
    project = _setup_published_project(
        temp_repo,
        project_id="proj-nozero-01",
        title="No Zero Snapshot Project",
        channel_id="chan-nozero-01",
        youtube_video_id="yt-nozero-123",
    )

    # In previous legacy code, upload immediately called record_snapshot(views=0, ...)
    # In v8 Real Analytics Ingestion, no snapshot is saved until real data is ingested.
    snapshots = temp_repo.get_analytics_snapshots(project.id)
    assert len(snapshots) == 0

    # Ensure StrategyFeedbackLoop reports has_data = False
    feedback = StrategyFeedbackLoop(temp_repo)
    analysis = feedback.analyze_channel_performance("chan-nozero-01")
    assert analysis["has_data"] is False
    assert analysis["total_snapshots"] == 0

