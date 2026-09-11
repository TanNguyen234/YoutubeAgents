"""Tests proving YouTubePublisherService consumes saved SEOPackage and channel metadata."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    PrivacyStatus,
    TitleVariantType,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    Chapter,
    Script,
    SEOPackage,
    TitleVariant,
    VideoProject,
)
from app.services.youtube_publisher import YouTubePublisherService


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_seo_pub.db"
        init_database(db_path)
        yield SQLiteRepository(db_path)


def test_publisher_consumes_saved_seo_package(repo):
    channel = Channel(
        id="chan-seo-01",
        title="Tech Explanations",
        handle="@TechExplain",
        niche="Cloud Computing",
        target_audience="Developers",
        youtube_category_id="28",
        default_language="en",
    )
    repo.save_channel(channel)

    script = Script(
        id="script-seo-01",
        title="Internal Project Script Title",
        hook="Why do distributed locks fail?",
        scenes=[],
        total_word_count=50,
        estimated_duration_seconds=15.0,
    )

    project = VideoProject(
        id="proj-seo-01",
        channel_id=channel.id,
        title="Internal Project Title",
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.APPROVED)

    # Save an optimized SEOPackage for this project
    seo_pkg = SEOPackage(
        id="seo-001",
        project_id=project.id,
        primary_keyword="distributed consensus",
        title_variants=[
            TitleVariant(
                angle=TitleVariantType.PROVOCATIVE_QUESTION,
                title="Can Raft Actually Fail in Production?",
                predicted_ctr_rationale="Curiosity gap",
            )
        ],
        selected_title="Raft vs Paxos: Why 90% of Distributed Systems Choose Wrong",
        description="A deep dive into distributed consensus mechanisms and real-world failure modes.",
        chapters=[
            Chapter(timestamp_seconds=0.0, timestamp_formatted="00:00", title="Introduction to Consensus"),
            Chapter(timestamp_seconds=45.0, timestamp_formatted="00:45", title="The Split-Brain Problem"),
            Chapter(timestamp_seconds=120.0, timestamp_formatted="02:00", title="Raft Leader Election"),
        ],
        tags=["Distributed Systems", "Raft", "Paxos", "Database Architecture"],
        pinned_comment="Have you encountered network partitions in your cluster?",
    )
    repo.save_seo_package(seo_pkg)

    service = YouTubePublisherService(repo)
    payload = service.build_metadata_payload(project, privacy_status=PrivacyStatus.PRIVATE)

    snippet = payload["snippet"]
    # 1. Must use SEOPackage.selected_title, NOT internal script title
    assert snippet["title"] == "Raft vs Paxos: Why 90% of Distributed Systems Choose Wrong"
    assert "Internal Project" not in snippet["title"]

    # 2. Must contain SEO description and formatted chapters
    assert "A deep dive into distributed consensus mechanisms" in snippet["description"]
    assert "=== CHAPTERS ===" in snippet["description"]
    assert "00:00 Introduction to Consensus" in snippet["description"]
    assert "00:45 The Split-Brain Problem" in snippet["description"]
    assert "02:00 Raft Leader Election" in snippet["description"]

    # 3. Must use SEOPackage tags
    assert "Raft" in snippet["tags"]
    assert "Paxos" in snippet["tags"]
    assert "Distributed Systems" in snippet["tags"]


def test_publisher_falls_back_cleanly_without_seo_package(repo):
    channel = Channel(
        id="chan-seo-02",
        title="Linux Channel",
        handle="@LinuxOps",
        niche="Linux",
        target_audience="Sysadmins",
    )
    repo.save_channel(channel)

    script = Script(
        id="script-seo-02",
        title="Understanding Linux cgroups v2",
        hook="How kernel resource limits work under pressure",
        scenes=[],
        total_word_count=30,
        estimated_duration_seconds=10.0,
    )

    project = VideoProject(
        id="proj-seo-02",
        channel_id=channel.id,
        title="Understanding Linux cgroups v2",
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.APPROVED)

    # No SEOPackage saved
    service = YouTubePublisherService(repo)
    payload = service.build_metadata_payload(project, privacy_status=PrivacyStatus.PRIVATE)

    snippet = payload["snippet"]
    # Falls back gracefully to script title and hook
    assert snippet["title"] == "Understanding Linux cgroups v2"
    assert "How kernel resource limits work" in snippet["description"]


def test_publisher_respects_channel_category_and_language_configuration(repo):
    channel = Channel(
        id="chan-custom-03",
        title="Vietnamese Tech Education",
        handle="@TechVi",
        niche="Computer Science Education",
        target_audience="Students",
        youtube_category_id="27",  # Education
        default_language="vi",
        made_for_kids=False,
        default_tags=["LapTrinh", "KhoaHocMayTinh"],
    )
    repo.save_channel(channel)

    project = VideoProject(
        id="proj-vi-01",
        channel_id=channel.id,
        title="Cau truc du lieu va giai thuat",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PRODUCING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RENDERED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.READY_FOR_REVIEW)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.APPROVED)

    service = YouTubePublisherService(repo)
    payload = service.build_metadata_payload(project, privacy_status=PrivacyStatus.PUBLIC)

    snippet = payload["snippet"]
    assert snippet["categoryId"] == "27"
    assert snippet["defaultLanguage"] == "vi"
    assert "LapTrinh" in snippet["tags"]
    assert "KhoaHocMayTinh" in snippet["tags"]
    assert payload["status"]["selfDeclaredMadeForKids"] is False
