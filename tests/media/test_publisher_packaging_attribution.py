"""Tests for YouTubePublisherService packaging deployment snapshot and attribution (Phase 1).

Covers:
- Test O: Actual publication title frozen from payload snippet title
- Test P: Actual thumbnail SHA computed directly from uploaded file bytes
- Test Q: Matching selected candidate creates MATCHED_SELECTED_CANDIDATE and links tournament/candidate
- Test R: Title or thumbnail SHA mismatch fails attribution safely -> UNMATCHED_SYSTEM_DEPLOYMENT
- Test S: Failed upload raises YouTubePublishError and does NOT create a matched deployment record
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    AssetType,
    PackagingAttributionStatus,
    PackagingTournamentStatus,
    PrivacyStatus,
    PublicationStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import (
    Asset,
    Channel,
    PackagingCandidate,
    PackagingTournament,
    ReviewRecord,
    SEOPackage,
    ThumbnailPackage,
    VideoProject,
    compute_packaging_fingerprint,
)
from app.services.youtube_publisher import YouTubePublisherService, YouTubePublishError


@pytest.fixture
def repo_and_tmp():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_pub_attr.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)
        yield repo, tmp_path


def _setup_approved_project_with_packaging(
    repo: SQLiteRepository,
    tmp_path: Path,
    project_id: str = "proj-pub-attr-01",
    candidate_title: str = "Why SQLite WAL Changes Concurrency",
    candidate_thumb_bytes: bytes = b"matching_thumbnail_image_bytes_12345",
):
    channel = Channel(
        id="chan-attr-01",
        title="Engineering Explained",
        handle="@EngExplained",
        niche="Databases",
        target_audience="Developers",
        youtube_category_id="28",
        default_language="en",
    )
    repo.save_channel(channel)

    video_file = tmp_path / "master.mp4"
    video_file.write_bytes(b"master_video_data_stream_bytes")

    video_asset = Asset(
        id="asset-video-01",
        project_id=project_id,
        asset_type=AssetType.FINAL_VIDEO,
        file_path=str(video_file),
        source_url="local://render/final.mp4",
        license_type="PROPRIETARY",
        content_sha256=hashlib.sha256(b"master_video_data_stream_bytes").hexdigest(),
        created_at=datetime.now(timezone.utc),
    )

    project = VideoProject(
        id=project_id,
        channel_id=channel.id,
        title="Draft Title",
        state=VideoLifecycleState.CREATED,
        assets=[video_asset],
    )
    repo.save_video_project(project)

    # Advance state machine to APPROVED
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)

    review = ReviewRecord(
        id="rev-attr-01",
        project_id=project.id,
        operator="Reviewer",
        action=ReviewAction.APPROVE,
        notes="Approved",
        approved_privacy_status=PrivacyStatus.PUBLIC,
    )
    repo.save_review_record(review)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.APPROVED)

    # Save thumbnail file and ThumbnailPackage
    thumb_file = tmp_path / "thumb_16_9.png"
    thumb_file.write_bytes(candidate_thumb_bytes)
    thumb_sha = hashlib.sha256(candidate_thumb_bytes).hexdigest()

    thumb_pkg = ThumbnailPackage(
        id="thm-pkg-01",
        project_id=project.id,
        file_path_16_9=str(thumb_file),
        headline_text="SQLITE WAL",
        content_sha256=thumb_sha,
    )
    repo.save_thumbnail_package(thumb_pkg)

    # Save SEOPackage with selected_title
    seo_pkg = SEOPackage(
        id="seo-pkg-01",
        project_id=project.id,
        primary_keyword="sqlite wal",
        title_variants=[],
        selected_title=candidate_title,
        description="Detailed description of SQLite WAL mode",
        pinned_comment="Which feature of WAL mode do you rely on?",
    )
    repo.save_seo_package(seo_pkg)

    # Save PackagingTournament with selected cand-2
    candidate_2 = PackagingCandidate(
        id="cand-2",
        title=candidate_title,
        title_strategy="DIRECT_VALUE",
        thumbnail_visual_strategy="code_result",
        description_snippet="Snippet",
        content_sha256=thumb_sha,
        quality_score=0.90,
    )
    tournament = PackagingTournament(
        id="tourn-attr-01",
        project_id=project.id,
        candidates=[candidate_2],
        selected_candidate_id="cand-2",
        selection_reason="Highest predicted score",
        native_ab_eligible=True,
        scoring_version="v1.0",
        status=PackagingTournamentStatus.COMPLETED,
    )
    repo.save_packaging_tournament(tournament)

    return project, thumb_file, thumb_sha


def test_matching_selected_candidate_creates_matched_attribution(repo_and_tmp, monkeypatch):
    """Test O, P, Q: Matching title and thumbnail SHA results in MATCHED_SELECTED_CANDIDATE."""
    repo, tmp_path = repo_and_tmp
    expected_title = "Why SQLite WAL Changes Concurrency"
    project, thumb_file, thumb_sha = _setup_approved_project_with_packaging(
        repo, tmp_path, candidate_title=expected_title
    )

    uploaded_params = {}

    def mock_upload(self, video_path, metadata_payload, thumbnail_path=None):
        uploaded_params["title"] = metadata_payload["snippet"]["title"]
        uploaded_params["thumb_path"] = thumbnail_path
        return "yt-matched-video-1", "https://youtu.be/yt-matched-video-1"

    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeOAuthManager.has_valid_token",
        lambda self: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeUploader.upload_video",
        mock_upload,
    )

    service = YouTubePublisherService(repo)
    job, mode = service.publish_project(project.id)

    assert mode == "REAL"
    assert job.status == PublicationStatus.COMPLETED
    assert job.youtube_video_id == "yt-matched-video-1"

    # Test O: Title frozen from payload snippet title
    assert job.deployed_title == expected_title

    # Test P: Thumbnail SHA computed from actual file bytes
    assert job.deployed_thumbnail_sha256 == thumb_sha

    # Test Q: Attribution status and candidate IDs linked
    assert job.packaging_tournament_id == "tourn-attr-01"
    assert job.packaging_candidate_id == "cand-2"
    assert job.packaging_attribution_status == PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value

    # Verify deterministic fingerprint
    expected_fp = compute_packaging_fingerprint(
        project_id=project.id,
        youtube_video_id="yt-matched-video-1",
        tournament_id="tourn-attr-01",
        candidate_id="cand-2",
        deployed_title=expected_title,
        deployed_thumbnail_sha256=thumb_sha,
    )
    assert job.packaging_fingerprint == expected_fp


def test_thumbnail_sha_mismatch_fails_attribution_safely(repo_and_tmp, monkeypatch):
    """Test R (Thumbnail mismatch): Different thumbnail bytes -> UNMATCHED_SYSTEM_DEPLOYMENT."""
    repo, tmp_path = repo_and_tmp
    expected_title = "Why SQLite WAL Changes Concurrency"
    project, thumb_file, original_sha = _setup_approved_project_with_packaging(
        repo, tmp_path, candidate_title=expected_title
    )

    # Overwrite the thumbnail file with DIFFERENT bytes after tournament was created
    new_bytes = b"different_unrelated_thumbnail_bytes_9999"
    thumb_file.write_bytes(new_bytes)
    new_sha = hashlib.sha256(new_bytes).hexdigest()
    assert new_sha != original_sha

    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeOAuthManager.has_valid_token",
        lambda self: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeUploader.upload_video",
        lambda self, video_path, metadata_payload, thumbnail_path=None: ("yt-mismatch-thumb-1", "https://youtu.be/1"),
    )

    service = YouTubePublisherService(repo)
    job, mode = service.publish_project(project.id)

    assert mode == "REAL"
    assert job.deployed_thumbnail_sha256 == new_sha
    # Safe fallback: do not lie that cand-2 was deployed
    assert job.packaging_candidate_id is None
    assert job.packaging_attribution_status == PackagingAttributionStatus.UNMATCHED_SYSTEM_DEPLOYMENT.value


def test_title_mismatch_fails_attribution_safely(repo_and_tmp, monkeypatch):
    """Test R (Title mismatch): Deployed title != candidate.title -> UNMATCHED_SYSTEM_DEPLOYMENT."""
    repo, tmp_path = repo_and_tmp
    original_title = "Original Title In Candidate"
    project, thumb_file, thumb_sha = _setup_approved_project_with_packaging(
        repo, tmp_path, candidate_title=original_title
    )

    # Update SEOPackage selected title to something else
    seo_pkg = repo.get_seo_package(project.id)
    seo_pkg.selected_title = "Totally Different Deployed Title"
    repo.save_seo_package(seo_pkg)

    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeOAuthManager.has_valid_token",
        lambda self: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeUploader.upload_video",
        lambda self, video_path, metadata_payload, thumbnail_path=None: ("yt-mismatch-title-1", "https://youtu.be/2"),
    )

    service = YouTubePublisherService(repo)
    job, mode = service.publish_project(project.id)

    assert mode == "REAL"
    assert job.deployed_title == "Totally Different Deployed Title"
    assert job.packaging_candidate_id is None
    assert job.packaging_attribution_status == PackagingAttributionStatus.UNMATCHED_SYSTEM_DEPLOYMENT.value


def test_failed_upload_does_not_create_matched_deployment(repo_and_tmp, monkeypatch):
    """Test S (Section 51): If upload fails, no PublicationJob with MATCHED_SELECTED_CANDIDATE is recorded."""
    repo, tmp_path = repo_and_tmp
    project, thumb_file, thumb_sha = _setup_approved_project_with_packaging(repo, tmp_path)

    def failing_upload(self, video_path, metadata_payload, thumbnail_path=None):
        raise ConnectionResetError("Network connection abruptly severed by peer")

    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeOAuthManager.has_valid_token",
        lambda self: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeUploader.upload_video",
        failing_upload,
    )

    service = YouTubePublisherService(repo)
    with pytest.raises(YouTubePublishError) as exc_info:
        service.publish_project(project.id)

    assert "Network connection abruptly severed" in str(exc_info.value)

    # Verify no publication job was created with MATCHED_SELECTED_CANDIDATE
    job = repo.get_publication_job_by_project(project.id)
    assert job is None or job.packaging_attribution_status != PackagingAttributionStatus.MATCHED_SELECTED_CANDIDATE.value

    # Project lifecycle state transitioned to FAILED
    updated_proj = repo.get_video_project(project.id)
    assert updated_proj.state == VideoLifecycleState.FAILED
