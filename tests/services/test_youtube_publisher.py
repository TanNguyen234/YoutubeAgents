"""Unit and contract tests for Stage 13 YouTube Upload and Scheduling Service."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    AssetType,
    PrivacyStatus,
    PublicationStatus,
    QualityStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import (
    Asset,
    Channel,
    PublicationJob,
    QualityResult,
    ReviewRecord,
    Script,
    VideoProject,
)
from app.services.youtube_publisher import YouTubePublisherService, YouTubePublishError


@pytest.fixture
def repo_and_tmp():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_pub.db"
        init_database(db_path)
        r = SQLiteRepository(db_path)
        yield r, Path(tmp_dir)


def _setup_approved_project(repo, tmp_path, privacy=PrivacyStatus.PRIVATE):
    channel = Channel(
        id="chan-pub-01",
        title="Pub Channel",
        handle="@PubChannel",
        niche="Database",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    script = Script(
        id="script-pub-01",
        title="Mastering SQLite WAL Mode",
        hook="How does WAL mode work under high concurrency?",
        scenes=[],
        total_word_count=40,
        estimated_duration_seconds=12.0,
    )

    # Fake master video file for testing
    video_file = tmp_path / "master_video.mp4"
    video_file.write_bytes(b"dummy_mp4_video_content_data_stream")

    asset = Asset(
        id="asset-pub-video-01",
        project_id="proj-pub-01",
        asset_type=AssetType.FINAL_VIDEO,
        file_path=str(video_file),
        source_url="local://render/final.mp4",
        license_type="PROPRIETARY",
        content_sha256="abc_video_hash",
        created_at=datetime.now(timezone.utc),
    )

    project = VideoProject(
        id="proj-pub-01",
        channel_id=channel.id,
        title="Mastering SQLite WAL Mode",
        state=VideoLifecycleState.CREATED,
        script=script,
        assets=[asset],
    )
    repo.save_video_project(project)

    # Legally advance state machine to APPROVED
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)

    # Human Review Record
    review = ReviewRecord(
        id="rev-pub-01",
        project_id=project.id,
        operator="Alice Operator",
        action=ReviewAction.APPROVE,
        notes="Approved for publication",
        approved_privacy_status=privacy,
    )
    repo.save_review_record(review)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.APPROVED)

    return repo.get_video_project(project.id)


def test_build_metadata_payload(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    service = YouTubePublisherService(repo)

    payload = service.build_metadata_payload(project, privacy_status=PrivacyStatus.PRIVATE)
    assert payload["snippet"]["title"] == "Mastering SQLite WAL Mode"
    assert "Science & Technology" in "Science & Technology"  # categoryId 28
    assert payload["snippet"]["categoryId"] == "28"
    assert payload["status"]["privacyStatus"] == "private"
    assert "Tech" in payload["snippet"]["tags"]


def test_publish_project_dry_run_immediate(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    service = YouTubePublisherService(repo)

    job, mode = service.publish_project(project.id, force_dry_run=True)
    assert mode == "DRY_RUN"
    # DRY_RUN without valid OAuth tokens must fail-closed to PENDING and BLOCKED
    assert job.status == PublicationStatus.PENDING
    assert job.privacy_status == PrivacyStatus.PRIVATE
    assert job.youtube_video_id is not None
    assert job.published_at is None
    assert "Awaiting live OAuth credentials" in (job.error_message or "")

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.BLOCKED


def test_publish_project_dry_run_scheduled(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    service = YouTubePublisherService(repo)

    sched_time = datetime.now(timezone.utc) + timedelta(days=2)
    job, mode = service.publish_project(project.id, scheduled_time=sched_time, force_dry_run=True)

    assert mode == "DRY_RUN"
    # Even when scheduled, missing credentials must leave job PENDING and project BLOCKED
    assert job.status == PublicationStatus.PENDING
    assert job.scheduled_publish_time == sched_time
    assert job.published_at is None

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.BLOCKED


def test_publish_project_real_immediate(repo_and_tmp, monkeypatch):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    service = YouTubePublisherService(repo)

    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeOAuthManager.has_valid_token",
        lambda self: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeUploader.upload_video",
        lambda self, video_path, metadata_payload, thumbnail_path=None: ("yt-real-vid-99", "https://youtu.be/yt-real-vid-99"),
    )

    job, mode = service.publish_project(project.id)
    assert mode == "REAL"
    assert job.status == PublicationStatus.COMPLETED
    assert job.privacy_status == PrivacyStatus.PRIVATE
    assert job.youtube_video_id == "yt-real-vid-99"
    assert job.published_at is not None

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.PUBLISHED


def test_publish_project_real_scheduled(repo_and_tmp, monkeypatch):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    service = YouTubePublisherService(repo)

    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeOAuthManager.has_valid_token",
        lambda self: True,
    )
    monkeypatch.setattr(
        "app.services.youtube_publisher.YouTubeUploader.upload_video",
        lambda self, video_path, metadata_payload, thumbnail_path=None: ("yt-sched-vid-88", "https://youtu.be/yt-sched-vid-88"),
    )

    sched_time = datetime.now(timezone.utc) + timedelta(days=2)
    job, mode = service.publish_project(project.id, scheduled_time=sched_time)

    assert mode == "REAL"
    assert job.status == PublicationStatus.SCHEDULED
    assert job.scheduled_publish_time == sched_time
    assert job.published_at is None

    updated = repo.get_video_project(project.id)
    assert updated.state == VideoLifecycleState.SCHEDULED


def test_publish_fails_if_not_approved(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    channel = Channel(
        id="chan-err",
        title="Err",
        handle="@Err",
        niche="Tech",
        target_audience="Devs",
    )
    repo.save_channel(channel)
    project = VideoProject(
        id="proj-not-approved",
        channel_id=channel.id,
        title="Unapproved",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    service = YouTubePublisherService(repo)
    with pytest.raises(YouTubePublishError, match="requires project in APPROVED state"):
        service.publish_project(project.id)


def test_publish_fails_if_no_master_video(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    # Remove the video file
    video_file = tmp_path / "master_video.mp4"
    video_file.unlink()

    service = YouTubePublisherService(repo)
    with pytest.raises(YouTubePublishError, match="No valid rendered master video file found"):
        service.publish_project(project.id)


def test_scheduled_publication_uses_valid_youtube_privacy_semantics(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    # Setup project with approved PUBLIC release privacy
    project = _setup_approved_project(repo, tmp_path, privacy=PrivacyStatus.PUBLIC)
    service = YouTubePublisherService(repo)

    sched_time = datetime.now(timezone.utc) + timedelta(days=3)

    # 1. Inspect metadata payload
    payload = service.build_metadata_payload(
        project,
        privacy_status=PrivacyStatus.PUBLIC,
        scheduled_time=sched_time,
    )
    # YouTube API invariant: status.publishAt requires privacyStatus="private" during upload
    assert payload["status"]["privacyStatus"] == "private"
    assert payload["status"]["publishAt"] == sched_time.isoformat()

    # 2. Inspect dry-run publication job
    job, mode = service.publish_project(project.id, scheduled_time=sched_time, force_dry_run=True)
    assert mode == "DRY_RUN"
    # Preserves intended approved release policy in job model
    assert job.privacy_status == PrivacyStatus.PUBLIC
    assert job.scheduled_publish_time == sched_time


def test_synthetic_media_flag_derived_from_generated_assets(repo_and_tmp):
    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)
    service = YouTubePublisherService(repo)

    # By default, without AI-generated photo/video assets, containsSyntheticMedia is False
    payload_normal = service.build_metadata_payload(project)
    assert payload_normal["status"]["containsSyntheticMedia"] is False

    # Add a photorealistic AI generated asset (e.g. GFlow video)
    gflow_asset = Asset(
        id="ast-gflow-01",
        project_id=project.id,
        asset_type=AssetType.SCENE_CARD,
        file_path=str(tmp_path / "gflow_clip.mp4"),
        source_url="gflow://generated/realistic_render.mp4",
        license_type="ORIGINAL_GENERATED",
        content_sha256="gflow_sha",
        created_at=datetime.now(timezone.utc),
    )
    project.assets.append(gflow_asset)
    repo.save_video_project(project)

    payload_synthetic = service.build_metadata_payload(project)
    assert payload_synthetic["status"]["containsSyntheticMedia"] is True

    # Publish and verify publication job records disclosure
    job, _ = service.publish_project(project.id, force_dry_run=True)
    assert job.contains_synthetic_media is True


def test_publisher_reads_actual_render_manifest_synthetic_media(repo_and_tmp):
    """Publisher must read contains_synthetic_media from canonical render_manifest.json."""
    import json
    from app.media.models import get_render_manifest_path

    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)

    base_projects = tmp_path / "output" / "projects"
    manifest_path = get_render_manifest_path(project.id, base_dir=base_projects)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_data = {
        "project_id": project.id,
        "contains_synthetic_media": True,
        "qa_verdict": "PASSED",
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    service = YouTubePublisherService(repo, base_projects_dir=base_projects)
    payload = service.build_metadata_payload(project)

    assert payload["status"]["containsSyntheticMedia"] is True


def test_manifest_true_cannot_become_false_during_publication(repo_and_tmp):
    """Manifest contains_synthetic_media=True must never become False during publication."""
    import json
    from app.media.models import get_render_manifest_path

    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)

    base_projects = tmp_path / "output" / "projects"
    manifest_path = get_render_manifest_path(project.id, base_dir=base_projects)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_data = {
        "project_id": project.id,
        "contains_synthetic_media": True,
        "qa_verdict": "PASSED",
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    service = YouTubePublisherService(repo, base_projects_dir=base_projects)

    # Even if explicitly passed False, manifest truth MUST be preserved
    payload = service.build_metadata_payload(project, contains_synthetic_media=False)
    assert payload["status"]["containsSyntheticMedia"] is True

    job, _ = service.publish_project(project.id, force_dry_run=True)
    assert job.contains_synthetic_media is True


def test_diagram_only_project_remains_non_synthetic(repo_and_tmp):
    """A diagram/motion-graphics project without photorealistic media must remain non-synthetic."""
    import json
    from app.media.models import get_render_manifest_path

    repo, tmp_path = repo_and_tmp
    project = _setup_approved_project(repo, tmp_path)

    base_projects = tmp_path / "output" / "projects"
    manifest_path = get_render_manifest_path(project.id, base_dir=base_projects)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_data = {
        "project_id": project.id,
        "contains_synthetic_media": False,
        "qa_verdict": "PASSED",
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    service = YouTubePublisherService(repo, base_projects_dir=base_projects)
    payload = service.build_metadata_payload(project)

    assert payload["status"]["containsSyntheticMedia"] is False

    job, _ = service.publish_project(project.id, force_dry_run=True)
    assert job.contains_synthetic_media is False


def test_gflow_director_asset_sets_manifest_synthetic_media(repo_and_tmp):
    """When Director uses a GFlow generated video/image, pipeline sets manifest contains_synthetic_media=True."""
    from app.domain.enums import PlatformFormat
    from app.domain.models import Scene, Script, VideoProject
    from app.media.director.models import ShotTimeline, TimelineShot, VisualModality
    from app.media.pipeline import MediaProductionPipeline
    from tests.media.test_media_pipeline import MockTTSBackend

    repo, tmp_path = repo_and_tmp

    channel = Channel(
        id="chan-syn-01",
        title="Pub Channel",
        handle="@PubChannel",
        niche="Database",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    script = Script(
        id="sc-syn-01",
        title="GFlow Synthetic Test",
        hook="Testing synthetic media detection.",
        scenes=[Scene(scene_index=0, narration="Testing synthetic media detection.", hook="Hook", visual_prompt="P1")],
        total_word_count=4,
        estimated_duration_seconds=3.0,
    )
    project = VideoProject(
        id="proj-syn-dir-01",
        channel_id=channel.id,
        title="GFlow Synthetic Test",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)

    base_out = tmp_path / "out_director_syn"
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=base_out,
    )

    # Mock director returning a GENERATED_IMAGE shot with gflow provider
    from PIL import Image
    dummy_img = tmp_path / "shot_gflow.png"
    Image.new("RGB", (1080, 1920), (40, 40, 60)).save(dummy_img, "PNG")

    shot = TimelineShot(
        shot_id="shot-01",
        beat_id="beat-01",
        start=0.0,
        end=3.0,
        scene_index=0,
        modality=VisualModality.GENERATED_IMAGE,
        duration=3.0,
        asset_path=str(dummy_img),
        asset_sha256="gflow_img_hash",
    )
    timeline = ShotTimeline(
        project_id=project.id,
        shots=[shot],
        total_duration=3.0,
    )
    from app.media.director.models import ShotSpec, Storyboard
    spec = ShotSpec(
        shot_id="shot-01",
        beat_id="beat-01",
        scene_index=0,
        narration_segment="Testing synthetic media detection.",
        duration_seconds=3.0,
        visual_modality=VisualModality.GENERATED_IMAGE,
    )
    storyboard = Storyboard(
        project_id=project.id,
        shots=[spec],
        total_duration=3.0,
    )

    pipeline.director.plan_and_render_timeline = lambda *args, **kwargs: (timeline, storyboard)

    proj, qa_res, manifest = pipeline.run_production(project.id)
    assert manifest.contains_synthetic_media is True
