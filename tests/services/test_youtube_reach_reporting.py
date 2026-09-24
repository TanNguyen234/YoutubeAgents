"""Tests for YouTube Reporting API Reach Ingestion Service (Phase 1).

Covers:
- Test A: Reach report type discovery
- Test B: Existing Reach job reused
- Test C: Missing Reach job created exactly once
- Test D: Missing report type fails closed
- Test E: No report yet creates no fake metrics
- Test F: Report CSV header-order independence
- Test G: Unknown video row does not create project
- Test H: Dry-run publication cannot receive trusted Reach attribution
- Test I: Valid zero impressions persisted as observed zero
- Test J: Blank CTR persists None
- Test K: Report idempotency by report ID
- Test L: Newer backfill replaces older daily observation
- Test M: Older report cannot overwrite newer observation
- Test N: Empty header-only report produces successful receipt but zero observations
- Test T: Reach sync does not mutate PackagingTournament
- Test W: Bare ReachObservation cannot mint Reporting API trust
- Test Z: Full end-to-end mocked Reporting API flow
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from typing import Any, Dict, List, Optional
import httpx
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    AnalyticsSource,
    PackagingAttributionStatus,
    PackagingTournamentStatus,
    PrivacyStatus,
    PublicationStatus,
    ReachSyncStatus,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    PackagingCandidate,
    PackagingTournament,
    PublicationJob,
    ReachObservation,
    VideoProject,
)
from app.services.youtube_oauth import YouTubeOAuthManager
from app.services.youtube_reach_reporting import (
    REACH_JOB_NAME,
    REACH_REPORT_TYPE_ID,
    REPORTING_API_BASE_URL,
    ReportingAuthScopeError,
    ReportingParseError,
    ReportingReachUnsupportedError,
    YouTubeReachReportingService,
)


class DummyOAuthManager(YouTubeOAuthManager):
    """Mock OAuth manager returning test access token."""
    def __init__(self, token: str = "ya29.test-mock-reach-token"):
        self._token = token

    def get_access_token(self) -> str:
        return self._token


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = Path(tmpdir) / "test_reach.db"
        init_database(db_path)
        yield SQLiteRepository(db_path)


def _setup_published_project_with_job(
    repo: SQLiteRepository,
    project_id: str = "proj-reach-01",
    youtube_video_id: str = "yt-vid-reach-1",
    channel_id: str = "chan-reach-01",
    status: PublicationStatus = PublicationStatus.COMPLETED,
    packaging_tournament_id: Optional[str] = None,
    packaging_candidate_id: Optional[str] = None,
    deployed_title: Optional[str] = None,
    deployed_thumbnail_sha256: Optional[str] = None,
    packaging_fingerprint: Optional[str] = None,
    packaging_attribution_status: Optional[str] = None,
) -> tuple[VideoProject, PublicationJob]:
    channel = repo.get_channel(channel_id)
    if not channel:
        channel = Channel(
            id=channel_id,
            title="Tech Channel",
            handle="@TechChannel",
            niche="Tech",
            target_audience="Developers",
        )
        repo.save_channel(channel)

    project = VideoProject(
        id=project_id,
        channel_id=channel_id,
        title="Reach Test Project",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)
    states_sequence = [
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.VERIFIED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.RENDERED,
        VideoLifecycleState.READY_FOR_REVIEW,
        VideoLifecycleState.APPROVED,
        VideoLifecycleState.UPLOADING,
    ]
    if status == PublicationStatus.COMPLETED:
        states_sequence.append(VideoLifecycleState.PUBLISHED)
    elif status == PublicationStatus.SCHEDULED:
        states_sequence.append(VideoLifecycleState.SCHEDULED)
    elif status == PublicationStatus.PENDING:
        states_sequence.append(VideoLifecycleState.BLOCKED)
    else:
        states_sequence.append(VideoLifecycleState.PUBLISHED)
    for st in states_sequence:
        repo.update_project_state(project.id, to_state=st)

    job = PublicationJob(
        id=f"pub-{project_id}",
        project_id=project_id,
        channel_id=channel_id,
        status=status,
        privacy_status=PrivacyStatus.PUBLIC,
        youtube_video_id=youtube_video_id,
        published_at=datetime.now(timezone.utc),
        contains_synthetic_media=False,
        packaging_tournament_id=packaging_tournament_id,
        packaging_candidate_id=packaging_candidate_id,
        deployed_title=deployed_title,
        deployed_thumbnail_sha256=deployed_thumbnail_sha256,
        packaging_fingerprint=packaging_fingerprint,
        packaging_attribution_status=packaging_attribution_status,
        created_at=datetime.now(timezone.utc),
    )
    repo.save_publication_job(job)
    return project, job


def test_reach_report_type_discovery(temp_repo):
    """Test A: Verify service discovers channel_reach_basic_a1 among multiple report types."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/reportTypes"
        return httpx.Response(
            200,
            json={
                "reportTypes": [
                    {"id": "channel_basic_a3", "name": "Basic Channel Report"},
                    {"id": REACH_REPORT_TYPE_ID, "name": "Channel Reach Basic"},
                    {"id": "channel_reach_combined_a1", "name": "Channel Reach Combined"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    types = service.list_report_types()
    matching = [t for t in types if t.get("id") == REACH_REPORT_TYPE_ID]
    assert len(matching) == 1
    assert matching[0]["id"] == "channel_reach_basic_a1"


def test_existing_reach_job_reused(temp_repo):
    """Test B: When matching Reporting API job exists remotely, reuse it without calling jobs.create."""
    jobs_create_called = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jobs_create_called
        if request.method == "GET" and request.url.path == "/v1/jobs":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "job-existing-999",
                            "reportTypeId": REACH_REPORT_TYPE_ID,
                            "name": REACH_JOB_NAME,
                            "createTime": "2026-09-01T00:00:00Z",
                        }
                    ]
                },
            )
        elif request.method == "POST" and request.url.path == "/v1/jobs":
            jobs_create_called += 1
            return httpx.Response(201, json={"id": "should-not-be-called"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    job_state = service.ensure_reach_reporting_job()
    assert job_state.job_id == "job-existing-999"
    assert jobs_create_called == 0

    # Repeat call: verify zero creation calls
    job_state_2 = service.ensure_reach_reporting_job()
    assert job_state_2.job_id == "job-existing-999"
    assert jobs_create_called == 0


def test_missing_reach_job_created_exactly_once(temp_repo):
    """Test C: When no matching job exists, check reportTypes and call POST /v1/jobs exactly once."""
    calls = {"jobs_list": 0, "report_types": 0, "jobs_create": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/jobs":
            calls["jobs_list"] += 1
            return httpx.Response(200, json={"jobs": []})
        elif request.method == "GET" and request.url.path == "/v1/reportTypes":
            calls["report_types"] += 1
            return httpx.Response(
                200,
                json={"reportTypes": [{"id": REACH_REPORT_TYPE_ID, "name": "Reach Basic"}]},
            )
        elif request.method == "POST" and request.url.path == "/v1/jobs":
            calls["jobs_create"] += 1
            body = json.loads(request.content.decode("utf-8"))
            assert body["reportTypeId"] == REACH_REPORT_TYPE_ID
            assert body["name"] == REACH_JOB_NAME
            assert "monetary" not in str(body).lower()
            return httpx.Response(
                201,
                json={
                    "id": "job-new-101",
                    "reportTypeId": REACH_REPORT_TYPE_ID,
                    "name": REACH_JOB_NAME,
                    "createTime": "2026-09-24T12:00:00Z",
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    job_state = service.ensure_reach_reporting_job()
    assert job_state.job_id == "job-new-101"
    assert calls["jobs_create"] == 1

    # Persisted in local repository
    saved = temp_repo.get_reporting_job_by_id("job-new-101")
    assert saved is not None
    assert saved.report_type_id == REACH_REPORT_TYPE_ID


def test_missing_report_type_fails_closed(temp_repo):
    """Test D: When channel does not support channel_reach_basic_a1, fail closed with typed error."""
    jobs_create_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jobs_create_called
        if request.method == "GET" and request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": []})
        elif request.method == "GET" and request.url.path == "/v1/reportTypes":
            return httpx.Response(
                200,
                json={"reportTypes": [{"id": "other_report_type", "name": "Other"}]},
            )
        elif request.method == "POST" and request.url.path == "/v1/jobs":
            jobs_create_called = True
            return httpx.Response(201, json={"id": "fail"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    with pytest.raises(ReportingReachUnsupportedError) as exc_info:
        service.ensure_reach_reporting_job()
    assert REACH_REPORT_TYPE_ID in str(exc_info.value)
    assert not jobs_create_called


def test_no_report_yet_creates_no_fake_metrics(temp_repo):
    """Test E: Job exists but reports.list returns empty list -> status NO_REPORT_YET, no fake zeros."""
    _setup_published_project_with_job(temp_repo, project_id="proj-no-rep", youtube_video_id="yt-no-rep")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/jobs":
            return httpx.Response(
                200,
                json={"jobs": [{"id": "job-existing-1", "reportTypeId": REACH_REPORT_TYPE_ID}]},
            )
        elif request.method == "GET" and "/reports" in request.url.path:
            return httpx.Response(200, json={"reports": []})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    assert res.status == ReachSyncStatus.NO_REPORT_YET
    assert res.reports_processed == 0

    obs = temp_repo.get_reach_observations_by_project("proj-no-rep")
    assert len(obs) == 0


def test_report_csv_header_order_independence(temp_repo):
    """Test F: Parser functions correctly regardless of column positions in CSV."""
    service = YouTubeReachReportingService(repository=temp_repo, oauth_manager=DummyOAuthManager())

    # Shuffled order of columns
    shuffled_csv = (
        "video_thumbnail_impressions_ctr,video_id,date,video_thumbnail_impressions,channel_id\n"
        "4.25,yt-vid-shuffled,2026-09-20,1500,chan-001\n"
    ).encode("utf-8")

    rows = service.parse_reach_csv(shuffled_csv)
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-09-20"
    assert r["channel_id"] == "chan-001"
    assert r["video_id"] == "yt-vid-shuffled"
    assert r["video_thumbnail_impressions"] == 1500
    assert r["video_thumbnail_impressions_ctr"] == 4.25


def test_unknown_video_row_does_not_create_project(temp_repo):
    """Test G: CSV containing video ID not known to repository does not create fake projects."""
    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-unknown-video-999,2500,5.0\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-unknown-1",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/unknown",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    assert res.reports_processed == 1
    assert res.unknown_video_rows == 1
    assert res.observations_created == 0

    # Receipt is still persisted
    receipt = temp_repo.get_report_receipt("rep-unknown-1")
    assert receipt is not None
    # No video project created
    assert temp_repo.get_video_project("yt-unknown-video-999") is None


def test_dry_run_publication_cannot_receive_trusted_reach(temp_repo):
    """Test H: Dry-run publications (yt-dryrun-*, PENDING) must not be matched to reach observations."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-dryrun-01",
        youtube_video_id="yt-dryrun-abcdef12345",
        status=PublicationStatus.PENDING,
    )

    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-dryrun-abcdef12345,500,2.5\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-dryrun-1",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/dryrun",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    assert res.observations_created == 0
    obs = temp_repo.get_reach_observations_by_project("proj-dryrun-01")
    assert len(obs) == 0


def test_observed_zero_impressions_and_blank_ctr(temp_repo):
    """Test I & J: Valid 0 impressions persisted as 0; blank CTR cell persisted as None."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-zero-reach",
        youtube_video_id="yt-zero-vid",
    )

    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-zero-vid,0,\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-zero-1",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/zero",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    assert res.observations_created == 1

    obs = temp_repo.get_reach_observations_by_project("proj-zero-reach")
    assert len(obs) == 1
    assert obs[0].thumbnail_impressions == 0
    assert obs[0].thumbnail_impressions_ctr is None
    assert obs[0].source == AnalyticsSource.YOUTUBE_REPORTING_API


def test_report_idempotency_by_report_id(temp_repo):
    """Test K: If report_id has already been processed, skip downloading/processing."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-idempotent-rep",
        youtube_video_id="yt-idemp-vid",
    )

    download_count = 0
    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-idemp-vid,100,5.0\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal download_count
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-idemp-1",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/idemp",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            download_count += 1
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    # First sync: downloads and processes
    res1 = service.sync_reach()
    assert res1.reports_processed == 1
    assert download_count == 1
    assert len(temp_repo.get_reach_observations_by_project("proj-idempotent-rep")) == 1

    # Second sync: report_id already present in receipts -> skipped
    res2 = service.sync_reach()
    assert res2.reports_processed == 0
    assert download_count == 1
    assert len(temp_repo.get_reach_observations_by_project("proj-idempotent-rep")) == 1


def test_newer_backfill_replaces_older_daily_observation(temp_repo):
    """Test L: Replacement report covering same date with newer createTime replaces older metrics."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-backfill-01",
        youtube_video_id="yt-backfill-vid",
    )

    # Report 1 at T1
    report_1_csv = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-backfill-vid,1000,4.0\n"
    ).encode("utf-8")

    # Report 2 at T2 (T2 > T1)
    report_2_csv = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-backfill-vid,1250,4.4\n"
    ).encode("utf-8")

    current_report = 1

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            if current_report == 1:
                return httpx.Response(
                    200,
                    json={
                        "reports": [
                            {
                                "id": "rep-t1",
                                "startTime": "2026-09-20T00:00:00Z",
                                "endTime": "2026-09-21T00:00:00Z",
                                "createTime": "2026-09-21T04:00:00Z",
                                "downloadUrl": "https://download.report/data",
                            }
                        ]
                    },
                )
            else:
                return httpx.Response(
                    200,
                    json={
                        "reports": [
                            {
                                "id": "rep-t2",
                                "startTime": "2026-09-20T00:00:00Z",
                                "endTime": "2026-09-21T00:00:00Z",
                                "createTime": "2026-09-22T05:00:00Z",
                                "downloadUrl": "https://download.report/data",
                            }
                        ]
                    },
                )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=report_1_csv if current_report == 1 else report_2_csv)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    # First sync
    res1 = service.sync_reach()
    assert res1.observations_created == 1
    obs1 = temp_repo.get_reach_observations_by_project("proj-backfill-01")
    assert len(obs1) == 1
    assert obs1[0].thumbnail_impressions == 1000
    assert obs1[0].thumbnail_impressions_ctr == 4.0
    assert obs1[0].source_report_id == "rep-t1"

    # Second sync with newer backfill
    current_report = 2
    res2 = service.sync_reach()
    assert res2.observations_updated == 1

    # Exactly one observation survives, with updated metrics
    obs2 = temp_repo.get_reach_observations_by_project("proj-backfill-01")
    assert len(obs2) == 1
    assert obs2[0].thumbnail_impressions == 1250
    assert obs2[0].thumbnail_impressions_ctr == 4.4
    assert obs2[0].source_report_id == "rep-t2"


def test_older_report_cannot_overwrite_newer_observation(temp_repo):
    """Test M: An older report (T1 < T2) must not overwrite an already existing newer observation (T2)."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-no-regress-01",
        youtube_video_id="yt-no-regress-vid",
    )

    # Existing observation from T2 (createTime = 2026-09-22T05:00:00Z)
    obs_t2 = ReachObservation(
        id="reach-t2",
        project_id="proj-no-regress-01",
        publication_job_id="pub-proj-no-regress-01",
        youtube_video_id="yt-no-regress-vid",
        report_date="2026-09-20",
        thumbnail_impressions=1250,
        thumbnail_impressions_ctr=4.4,
        source_report_id="rep-t2",
        source_report_create_time=datetime(2026, 9, 22, 5, 0, 0, tzinfo=timezone.utc),
        source=AnalyticsSource.YOUTUBE_REPORTING_API,
    )
    temp_repo.save_reach_observation(obs_t2)

    # Incoming report from T1 (createTime = 2026-09-21T04:00:00Z)
    report_t1_csv = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-no-regress-vid,900,3.5\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-t1-late",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T04:00:00Z",
                            "downloadUrl": "https://download.report/data",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=report_t1_csv)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    # Observation must NOT have updated
    assert res.observations_updated == 0

    obs = temp_repo.get_reach_observations_by_project("proj-no-regress-01")
    assert len(obs) == 1
    assert obs[0].thumbnail_impressions == 1250
    assert obs[0].thumbnail_impressions_ctr == 4.4
    assert obs[0].source_report_id == "rep-t2"


def test_empty_header_only_report_produces_receipt_zero_observations(temp_repo):
    """Test N: Header-only report produces successful receipt but 0 observations."""
    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-empty-hdr",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/empty",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    assert res.status == ReachSyncStatus.PROCESSED_EMPTY_REPORT
    assert res.reports_processed == 1
    assert res.observations_created == 0

    receipt = temp_repo.get_report_receipt("rep-empty-hdr")
    assert receipt is not None


def test_reach_sync_does_not_mutate_packaging_tournament(temp_repo):
    """Test T: Reach sync must never mutate PackagingTournament fields."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-tourn-immut",
        youtube_video_id="yt-tourn-vid",
    )

    candidates = [
        PackagingCandidate(
            id="cand-1",
            title="Candidate 1 Title",
            title_strategy="CURIOSITY_GAP",
            thumbnail_visual_strategy="diagram",
            description_snippet="Snippet 1",
            content_sha256="sha-cand-1",
            quality_score=0.85,
            score_breakdown={"ctr": 0.85},
        ),
        PackagingCandidate(
            id="cand-2",
            title="Candidate 2 Title",
            title_strategy="DIRECT_VALUE",
            thumbnail_visual_strategy="code_result",
            description_snippet="Snippet 2",
            content_sha256="sha-cand-2",
            quality_score=0.92,
            score_breakdown={"ctr": 0.92},
        ),
    ]
    tournament = PackagingTournament(
        id="tourn-immut-01",
        project_id="proj-tourn-immut",
        candidates=candidates,
        selected_candidate_id="cand-2",
        selection_reason="Highest offline predicted score",
        native_ab_eligible=True,
        scoring_version="v1.0",
        status=PackagingTournamentStatus.COMPLETED,
    )
    temp_repo.save_packaging_tournament(tournament)

    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-tourn-vid,5000,3.2\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "job-1", "reportTypeId": REACH_REPORT_TYPE_ID}]})
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-tourn-1",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/tourn",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    service.sync_reach()

    # Verify tournament is completely unchanged
    reloaded = temp_repo.get_packaging_tournament("proj-tourn-immut")
    assert reloaded.selected_candidate_id == "cand-2"
    assert reloaded.selection_reason == "Highest offline predicted score"
    assert reloaded.status == PackagingTournamentStatus.COMPLETED
    assert reloaded.candidates[1].quality_score == 0.92


def test_bare_reach_observation_cannot_mint_reporting_trust():
    """Test W: Bare ReachObservation instantiation without explicit source defaults to LEGACY_UNVERIFIED."""
    bare_obs = ReachObservation(
        project_id="proj-test",
        publication_job_id="pub-test",
        youtube_video_id="yt-test",
        report_date="2026-09-20",
        thumbnail_impressions=100,
        thumbnail_impressions_ctr=5.0,
        source_report_id="rep-dummy",
        source_report_create_time=datetime.now(timezone.utc),
    )
    assert bare_obs.source == AnalyticsSource.LEGACY_UNVERIFIED
    assert bare_obs.source != AnalyticsSource.YOUTUBE_REPORTING_API


def test_full_end_to_end_mocked_reporting_api_flow(temp_repo):
    """Test Z: Full end-to-end mocked Reporting API reach ingestion."""
    _setup_published_project_with_job(
        temp_repo,
        project_id="proj-e2e-01",
        youtube_video_id="yt-e2e-vid",
        packaging_candidate_id="cand-e2e",
        packaging_attribution_status=PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value,
    )

    csv_content = (
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-09-20,chan-001,yt-e2e-vid,12400,4.7\n"
    ).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": []})
        elif request.method == "GET" and request.url.path == "/v1/reportTypes":
            return httpx.Response(200, json={"reportTypes": [{"id": REACH_REPORT_TYPE_ID}]})
        elif request.method == "POST" and request.url.path == "/v1/jobs":
            return httpx.Response(
                201,
                json={"id": "job-e2e-1", "reportTypeId": REACH_REPORT_TYPE_ID, "name": REACH_JOB_NAME},
            )
        elif "/reports" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "reports": [
                        {
                            "id": "rep-e2e-1",
                            "startTime": "2026-09-20T00:00:00Z",
                            "endTime": "2026-09-21T00:00:00Z",
                            "createTime": "2026-09-21T05:00:00Z",
                            "downloadUrl": "https://download.report/e2e",
                        }
                    ]
                },
            )
        elif request.url.host == "download.report":
            return httpx.Response(200, content=csv_content)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = YouTubeReachReportingService(
        repository=temp_repo,
        oauth_manager=DummyOAuthManager(),
        http_client=client,
    )

    res = service.sync_reach()
    assert res.status == ReachSyncStatus.SYNCED
    assert res.reports_processed == 1
    assert res.observations_created == 1

    obs = temp_repo.get_reach_observations_by_project("proj-e2e-01")
    assert len(obs) == 1
    assert obs[0].youtube_video_id == "yt-e2e-vid"
    assert obs[0].thumbnail_impressions == 12400
    assert obs[0].thumbnail_impressions_ctr == 4.7
    assert obs[0].source == AnalyticsSource.YOUTUBE_REPORTING_API
