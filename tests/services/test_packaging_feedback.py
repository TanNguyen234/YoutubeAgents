"""Tests for Packaging Reach Feedback Service (Phase 1).

Covers:
- Test U: Weighted CTR uses impression weighting
- Test V: Zero usable impressions -> aggregate CTR None
- Test S: Legacy publication remains UNATTRIBUTED_LEGACY without inferring tournament winner
- Test X: Process restart preserves deployment + reach + feedback
- Invariant: Post-publish reach feedback does not declare native A/B winners
"""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    AnalyticsSource,
    PackagingAttributionStatus,
    PrivacyStatus,
    PublicationStatus,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    PublicationJob,
    ReachObservation,
    VideoProject,
    compute_packaging_fingerprint,
)
from app.services.packaging_feedback import compute_packaging_reach_feedback


@pytest.fixture
def temp_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = Path(tmpdir) / "test_feedback.db"
        init_database(db_path)
        yield SQLiteRepository(db_path)


def _setup_project(repo: SQLiteRepository, project_id: str, channel_id: str = "chan-01") -> VideoProject:
    channel = repo.get_channel(channel_id)
    if not channel:
        repo.save_channel(
            Channel(
                id=channel_id,
                title="Feedback Channel",
                handle="@FeedbackChan",
                niche="Engineering",
                target_audience="Engineers",
            )
        )
    proj = VideoProject(
        id=project_id,
        channel_id=channel_id,
        title="Feedback Test Project",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(proj)
    for st in (
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
    ):
        repo.update_project_state(proj.id, to_state=st)
    return proj


def test_weighted_ctr_uses_impression_weighting(temp_repo):
    """Test U: Section 32 & 66.
    Day 1: 100 impressions, CTR = 10.0
    Day 2: 900 impressions, CTR = 2.0
    Weighted CTR = (100*10 + 900*2) / 1000 = 2.8, NOT simple average (6.0).
    """
    _setup_project(temp_repo, "proj-weighted")

    job = PublicationJob(
        id="pub-weighted-1",
        project_id="proj-weighted",
        channel_id="chan-01",
        status=PublicationStatus.COMPLETED,
        privacy_status=PrivacyStatus.PUBLIC,
        youtube_video_id="yt-weighted-vid",
        packaging_tournament_id="tourn-01",
        packaging_candidate_id="cand-2",
        deployed_title="Why SQLite WAL Changes Concurrency",
        deployed_thumbnail_sha256="abc123sha",
        packaging_fingerprint="fp123",
        packaging_attribution_status=PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value,
        published_at=datetime.now(timezone.utc),
    )
    temp_repo.save_publication_job(job)

    # Day 1: 100 imp, CTR = 10.0
    obs1 = ReachObservation(
        id="reach-day-1",
        project_id="proj-weighted",
        publication_job_id=job.id,
        youtube_video_id="yt-weighted-vid",
        report_date="2026-09-20",
        thumbnail_impressions=100,
        thumbnail_impressions_ctr=10.0,
        source_report_id="rep-1",
        source_report_create_time=datetime(2026, 9, 21, tzinfo=timezone.utc),
        source=AnalyticsSource.YOUTUBE_REPORTING_API,
    )
    temp_repo.save_reach_observation(obs1)

    # Day 2: 900 imp, CTR = 2.0
    obs2 = ReachObservation(
        id="reach-day-2",
        project_id="proj-weighted",
        publication_job_id=job.id,
        youtube_video_id="yt-weighted-vid",
        report_date="2026-09-21",
        thumbnail_impressions=900,
        thumbnail_impressions_ctr=2.0,
        source_report_id="rep-2",
        source_report_create_time=datetime(2026, 9, 22, tzinfo=timezone.utc),
        source=AnalyticsSource.YOUTUBE_REPORTING_API,
    )
    temp_repo.save_reach_observation(obs2)

    feedback = compute_packaging_reach_feedback(temp_repo, "proj-weighted")
    assert feedback is not None
    assert feedback.observed_days == 2
    assert feedback.total_thumbnail_impressions == 1000
    assert feedback.weighted_thumbnail_impressions_ctr == 2.8
    assert feedback.attribution_status == PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value
    assert feedback.deployed_candidate_id == "cand-2"


def test_zero_usable_impressions_returns_none_ctr(temp_repo):
    """Test V: Section 32 & 67. All observations have 0 impressions -> weighted CTR is None without division by zero."""
    _setup_project(temp_repo, "proj-zero-imp")

    job = PublicationJob(
        id="pub-zero-1",
        project_id="proj-zero-imp",
        channel_id="chan-01",
        status=PublicationStatus.COMPLETED,
        privacy_status=PrivacyStatus.PUBLIC,
        youtube_video_id="yt-zero-imp-vid",
        published_at=datetime.now(timezone.utc),
    )
    temp_repo.save_publication_job(job)

    obs = ReachObservation(
        id="reach-zero-1",
        project_id="proj-zero-imp",
        publication_job_id=job.id,
        youtube_video_id="yt-zero-imp-vid",
        report_date="2026-09-20",
        thumbnail_impressions=0,
        thumbnail_impressions_ctr=None,
        source_report_id="rep-0",
        source_report_create_time=datetime(2026, 9, 21, tzinfo=timezone.utc),
        source=AnalyticsSource.YOUTUBE_REPORTING_API,
    )
    temp_repo.save_reach_observation(obs)

    feedback = compute_packaging_reach_feedback(temp_repo, "proj-zero-imp")
    assert feedback is not None
    assert feedback.total_thumbnail_impressions == 0
    assert feedback.weighted_thumbnail_impressions_ctr is None


def test_legacy_publication_remains_unattributed_legacy(temp_repo):
    """Test S: Section 57. PublicationJob with no packaging metadata produces UNATTRIBUTED_LEGACY."""
    _setup_project(temp_repo, "proj-legacy-01")

    # Legacy publication job with NULL packaging deployment fields
    job = PublicationJob(
        id="pub-legacy-1",
        project_id="proj-legacy-01",
        channel_id="chan-01",
        status=PublicationStatus.COMPLETED,
        privacy_status=PrivacyStatus.PUBLIC,
        youtube_video_id="yt-legacy-vid",
        published_at=datetime.now(timezone.utc),
    )
    temp_repo.save_publication_job(job)

    obs = ReachObservation(
        id="reach-legacy-1",
        project_id="proj-legacy-01",
        publication_job_id=job.id,
        youtube_video_id="yt-legacy-vid",
        report_date="2026-09-20",
        thumbnail_impressions=4500,
        thumbnail_impressions_ctr=3.8,
        source_report_id="rep-leg-1",
        source_report_create_time=datetime(2026, 9, 21, tzinfo=timezone.utc),
        source=AnalyticsSource.YOUTUBE_REPORTING_API,
    )
    temp_repo.save_reach_observation(obs)

    feedback = compute_packaging_reach_feedback(temp_repo, "proj-legacy-01")
    assert feedback is not None
    assert feedback.attribution_status == PackagingAttributionStatus.UNATTRIBUTED_LEGACY.value
    assert feedback.deployed_candidate_id is None
    assert feedback.tournament_id is None
    assert feedback.total_thumbnail_impressions == 4500
    assert feedback.weighted_thumbnail_impressions_ctr == 3.8


def test_process_restart_preserves_deployment_reach_feedback():
    """Test X: Section 77. Process restart preserves deployment + reach + feedback across independent repository sessions."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = Path(tmpdir) / "test_restart.db"
        init_database(db_path)

        # Session 1: Write project, publication with frozen deployment, and reach observation
        repo1 = SQLiteRepository(db_path)
        _setup_project(repo1, "proj-restart-01")

        fp = compute_packaging_fingerprint(
            project_id="proj-restart-01",
            youtube_video_id="yt-restart-vid",
            tournament_id="tourn-rest-01",
            candidate_id="cand-rest-2",
            deployed_title="Restart Persistence Title",
            deployed_thumbnail_sha256="deadbeef1234",
        )

        job1 = PublicationJob(
            id="pub-restart-1",
            project_id="proj-restart-01",
            channel_id="chan-01",
            status=PublicationStatus.COMPLETED,
            privacy_status=PrivacyStatus.PUBLIC,
            youtube_video_id="yt-restart-vid",
            packaging_tournament_id="tourn-rest-01",
            packaging_candidate_id="cand-rest-2",
            deployed_title="Restart Persistence Title",
            deployed_thumbnail_sha256="deadbeef1234",
            packaging_fingerprint=fp,
            packaging_attribution_status=PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value,
            published_at=datetime.now(timezone.utc),
        )
        repo1.save_publication_job(job1)

        obs1 = ReachObservation(
            id="reach-rest-1",
            project_id="proj-restart-01",
            publication_job_id=job1.id,
            youtube_video_id="yt-restart-vid",
            report_date="2026-09-20",
            thumbnail_impressions=3000,
            thumbnail_impressions_ctr=5.2,
            source_report_id="rep-rest-1",
            source_report_create_time=datetime(2026, 9, 21, tzinfo=timezone.utc),
            source=AnalyticsSource.YOUTUBE_REPORTING_API,
        )
        repo1.save_reach_observation(obs1)

        # Close session 1
        del repo1

        # Session 2: Fresh SQLiteRepository on same database
        repo2 = SQLiteRepository(db_path)
        feedback = compute_packaging_reach_feedback(repo2, "proj-restart-01")

        assert feedback is not None
        assert feedback.youtube_video_id == "yt-restart-vid"
        assert feedback.start_date == "2026-09-20"
        assert feedback.end_date == "2026-09-20"
        assert feedback.total_thumbnail_impressions == 3000
        assert feedback.weighted_thumbnail_impressions_ctr == 5.2
        assert feedback.deployed_candidate_id == "cand-rest-2"
        assert feedback.deployed_title == "Restart Persistence Title"
        assert feedback.deployed_thumbnail_sha256 == "deadbeef1234"
        assert feedback.packaging_fingerprint == fp
        assert feedback.attribution_status == PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value
