"""Unit tests verifying domain schema models, validation rules, and default safeguards."""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from app.domain.enums import (
    AssetType,
    ExperimentStatus,
    PlatformFormat,
    PrivacyStatus,
    PublicationStatus,
    QualityStatus,
    VideoLifecycleState,
)
from app.domain.models import (
    AnalyticsSnapshot,
    Asset,
    Channel,
    Claim,
    Experiment,
    PublicationJob,
    QualityResult,
    ResearchDossier,
    ResearchSource,
    Scene,
    Script,
    TopicCandidate,
    VideoProject,
)


def test_channel_model() -> None:
    """Verify Channel model instantiation and field constraints."""
    channel = Channel(
        id="chan-001",
        title="AI Engineering Hub",
        handle="@AIEngineeringHub",
        niche="Artificial Intelligence",
        target_audience="Software Engineers",
        default_language="en",
    )
    assert channel.id == "chan-001"
    assert channel.handle == "@AIEngineeringHub"
    assert channel.is_active is True


def test_topic_candidate_validation_and_no_fabricated_cpm() -> None:
    """Verify TopicCandidate validation and ensure estimated_cpm defaults to None."""
    topic = TopicCandidate(
        id="topic-101",
        channel_id="chan-001",
        keyword="Local AI Agents",
        opportunity_score=8.5,
        authority_score=9.0,
    )
    assert topic.opportunity_score == 8.5
    assert topic.authority_score == 9.0
    # Invariant: No fabricated default CPM (must be None)
    assert topic.estimated_cpm is None

    # Score out of bounds should fail
    with pytest.raises(ValidationError):
        TopicCandidate(
            id="topic-102",
            channel_id="chan-001",
            keyword="Invalid Score",
            opportunity_score=11.0,  # Max is 10.0
        )


def test_research_source_license_provenance_default() -> None:
    """Verify ResearchSource license_type defaults to UNKNOWN or None, not fabricated CC."""
    source = ResearchSource(
        id="src-01",
        url="https://arxiv.org/abs/2601.12345",
        title="Autonomous Coding Agents",
        authors=["Alice", "Bob"],
        content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert source.license_type in ("UNKNOWN", None)


def test_research_source_and_claim_provenance() -> None:
    """Verify ResearchSource and Claim integrity."""
    source = ResearchSource(
        id="src-01",
        url="https://arxiv.org/abs/2601.12345",
        title="Autonomous Coding Agents",
        authors=["Alice", "Bob"],
        content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        license_type="CC-BY-4.0",
    )
    claim = Claim(
        id="claim-01",
        source_id=source.id,
        statement="Local agents reduce inference cost by 90%",
        verified=True,
        confidence_score=0.95,
    )
    dossier = ResearchDossier(
        id="dos-01",
        topic_id="topic-101",
        sources=[source],
        claims=[claim],
        summary="Thorough analysis of autonomous coding agent benchmarks.",
    )
    assert len(dossier.sources) == 1
    assert len(dossier.claims) == 1
    assert dossier.claims[0].verified is True


def test_quality_result_defaults_to_pending_not_passed() -> None:
    """Verify QualityResult defaults to PENDING (implicit success is forbidden)."""
    qa = QualityResult(
        id="qa-01",
        project_id="proj-01",
        loudness_lufs=-14.2,
        duration_seconds=59.8,
    )
    assert qa.status == QualityStatus.PENDING


def test_video_project_and_scene_structure() -> None:
    """Verify VideoProject, Script, Scene, and Asset relationships."""
    scene1 = Scene(
        index=0,
        hook="Have you ever wanted to run private AI agents locally?",
        narration="In this tutorial, we construct a production agent using native tools.",
        target_duration_seconds=5.0,
        visual_prompt="Close up code editor showing clean Python script",
    )
    script = Script(
        id="script-01",
        title="Build Local AI Agents in 10 Minutes",
        hook=scene1.hook,
        scenes=[scene1],
        total_word_count=150,
        estimated_duration_seconds=60.0,
    )
    asset = Asset(
        id="asset-01",
        project_id="proj-01",
        asset_type=AssetType.VIDEO_CLIP,
        file_path="output/clips/scene_0.mp4",
        source_url="https://pexels.com/video/12345",
        license_type="Pexels License",
        content_sha256="abc123sha",
    )
    qa = QualityResult(
        id="qa-01",
        project_id="proj-01",
        status=QualityStatus.PASSED,
        loudness_lufs=-14.2,
        duration_seconds=59.8,
        sync_drift_ms=12.0,
    )
    project = VideoProject(
        id="proj-01",
        channel_id="chan-001",
        title=script.title,
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.READY_FOR_REVIEW,
        script=script,
        assets=[asset],
        quality=qa,
    )
    assert project.state == VideoLifecycleState.READY_FOR_REVIEW
    assert project.format == PlatformFormat.SHORTS_9_16
    assert len(project.assets) == 1
    assert project.quality.status == QualityStatus.PASSED


def test_publication_job_privacy_status_enum_default() -> None:
    """Verify PublicationJob uses PrivacyStatus enum and enforces default PRIVATE."""
    job = PublicationJob(
        id="pub-01",
        project_id="proj-01",
        channel_id="chan-001",
        status=PublicationStatus.PENDING,
    )
    assert job.privacy_status == PrivacyStatus.PRIVATE
    assert job.privacy_status.value == "private"
    assert isinstance(job.privacy_status, PrivacyStatus)


def test_analytics_and_experiment_models() -> None:
    """Verify AnalyticsSnapshot and Experiment model contracts."""
    analytics = AnalyticsSnapshot(
        id="ana-01",
        project_id="proj-01",
        youtube_video_id="dQw4w9WgXcQ",
        views=15420,
        watch_time_hours=312.5,
        ctr_percent=8.4,
        average_view_duration_seconds=42.1,
    )
    experiment = Experiment(
        id="exp-01",
        project_id="proj-01",
        hypothesis="Dynamic animated hooks improve 3-second retention by 15%",
        status=ExperimentStatus.RUNNING,
    )
    assert analytics.views == 15420
    assert experiment.status == ExperimentStatus.RUNNING
