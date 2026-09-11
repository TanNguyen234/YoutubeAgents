"""Tests for Channel Operating System domain models and database persistence."""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import (
    EditorialSlotStatus,
    PlatformFormat,
    TitleVariantType,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    Chapter,
    ContentSeries,
    EditorialSlot,
    QuotaUsageRecord,
    SEOPackage,
    ThumbnailPackage,
    TitleVariant,
    VideoProject,
)


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteRepository:
    db_file = tmp_path / "test_channel_os.db"
    repository = SQLiteRepository(db_file)
    # Seed a test channel
    channel = Channel(
        id="chan-tech",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Artificial Intelligence",
        target_audience="Software Engineers and AI Builders",
    )
    repository.save_channel(channel)
    return repository


def test_content_series_persistence(repo: SQLiteRepository):
    series = ContentSeries(
        id="ser-60s-tech",
        channel_id="chan-tech",
        title="60-Second Deep Tech",
        description="Quick high-impact AI architecture breakdowns",
        target_niche="Artificial Intelligence",
        default_format=PlatformFormat.SHORTS_9_16,
        frequency_per_week=3,
        playlist_id="PL_ai_tech_60s",
        visual_style_preset="cyber_neon",
        next_episode_number=1,
    )
    repo.save_content_series(series)

    loaded = repo.get_content_series("ser-60s-tech")
    assert loaded is not None
    assert loaded.title == "60-Second Deep Tech"
    assert loaded.playlist_id == "PL_ai_tech_60s"
    assert loaded.next_episode_number == 1

    # Test episode counter increment
    next_num = repo.increment_series_episode("ser-60s-tech")
    assert next_num == 2
    reloaded = repo.get_content_series("ser-60s-tech")
    assert reloaded.next_episode_number == 2


def test_editorial_calendar_slot_lifecycle(repo: SQLiteRepository):
    # First save content series for foreign key constraint
    series = ContentSeries(
        id="ser-60s-tech",
        channel_id="chan-tech",
        title="60-Second Deep Tech",
        target_niche="Artificial Intelligence",
    )
    repo.save_content_series(series)

    slot = EditorialSlot(
        id="slot-20260908-1800",
        channel_id="chan-tech",
        series_id="ser-60s-tech",
        slot_time=datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc),
        status=EditorialSlotStatus.PLANNED,
        target_topic="DeepSeek R1 Architecture",
        episode_number=1,
    )
    repo.save_editorial_slot(slot)

    loaded = repo.get_editorial_slot("slot-20260908-1800")
    assert loaded is not None
    assert loaded.target_topic == "DeepSeek R1 Architecture"
    assert loaded.status == EditorialSlotStatus.PLANNED

    # Update slot status to IN_PRODUCTION with project_id
    project = VideoProject(
        id="proj-slot-01",
        channel_id="chan-tech",
        title="DeepSeek R1 Architecture Breakdown",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    repo.update_editorial_slot_status(
        "slot-20260908-1800", EditorialSlotStatus.IN_PRODUCTION, project_id="proj-slot-01"
    )

    updated = repo.get_editorial_slot("slot-20260908-1800")
    assert updated.status == EditorialSlotStatus.IN_PRODUCTION
    assert updated.project_id == "proj-slot-01"


def test_seo_package_persistence(repo: SQLiteRepository):
    project = VideoProject(
        id="proj-seo-01",
        channel_id="chan-tech",
        title="MoE Architecture Explained",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    seo = SEOPackage(
        id="seo-001",
        project_id="proj-seo-01",
        primary_keyword="MoE Architecture",
        title_variants=[
            TitleVariant(
                angle=TitleVariantType.CURIOSITY_GAP,
                title="The Secret Behind 1-Trillion Parameter AI",
                predicted_ctr_rationale="Information gap trigger",
            ),
            TitleVariant(
                angle=TitleVariantType.DIRECT_VALUE,
                title="How MoE Slashes AI Training Cost by 80%",
                predicted_ctr_rationale="Quantifiable value promise",
            ),
            TitleVariant(
                angle=TitleVariantType.PROVOCATIVE_QUESTION,
                title="Is Dense AI Truly Dead in 2026?",
                predicted_ctr_rationale="Challenges conventional belief",
            ),
        ],
        selected_title="How MoE Slashes AI Training Cost by 80%",
        description="Full breakdown of Mixture of Experts routing and expert capacity.",
        chapters=[
            Chapter(timestamp_seconds=0.0, timestamp_formatted="00:00", title="The Monolith Dilemma"),
            Chapter(timestamp_seconds=15.0, timestamp_formatted="00:15", title="Expert Routing Explained"),
            Chapter(timestamp_seconds=45.0, timestamp_formatted="00:45", title="Real Performance Benchmarks"),
        ],
        tags=["MoE", "Machine Learning", "DeepSeek", "AI Architecture", "Sparse Models"],
        pinned_comment="Would you deploy sparse MoE or dense 70B for your agents? Let's discuss below 👇",
    )
    repo.save_seo_package(seo)

    loaded = repo.get_seo_package("proj-seo-01")
    assert loaded is not None
    assert loaded.primary_keyword == "MoE Architecture"
    assert len(loaded.title_variants) == 3
    assert loaded.title_variants[0].angle == TitleVariantType.CURIOSITY_GAP
    assert len(loaded.chapters) == 3
    assert loaded.chapters[1].timestamp_formatted == "00:15"
    assert len(loaded.tags) == 5


def test_thumbnail_package_persistence(repo: SQLiteRepository):
    project = VideoProject(
        id="proj-thumb-01",
        channel_id="chan-tech",
        title="GPU Cluster Design",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    thumb = ThumbnailPackage(
        id="thm-001",
        project_id="proj-thumb-01",
        file_path_16_9="output/thumbnails/thm_16_9.jpg",
        file_path_9_16="output/thumbnails/thm_9_16.jpg",
        headline_text="80% CHEAPER AI",
        content_sha256="abc123def456",
        provenance={"generator": "PillowCompositor", "background_source": "gflow_ai"},
    )
    repo.save_thumbnail_package(thumb)

    loaded = repo.get_thumbnail_package("proj-thumb-01")
    assert loaded is not None
    assert loaded.headline_text == "80% CHEAPER AI"
    assert loaded.provenance["background_source"] == "gflow_ai"


def test_quota_usage_record_persistence(repo: SQLiteRepository):
    p1 = VideoProject(id="proj-01", channel_id="chan-tech", title="V1", state=VideoLifecycleState.CREATED)
    p2 = VideoProject(id="proj-02", channel_id="chan-tech", title="V2", state=VideoLifecycleState.CREATED)
    repo.save_video_project(p1)
    repo.save_video_project(p2)

    # Record multiple API operations
    rec1 = QuotaUsageRecord(
        id="qta-001",
        operation="videos.insert",
        units_consumed=1600,
        consumed_date="2026-09-07",
        project_id="proj-01",
    )
    rec2 = QuotaUsageRecord(
        id="qta-002",
        operation="thumbnails.set",
        units_consumed=50,
        consumed_date="2026-09-07",
        project_id="proj-01",
    )
    rec3 = QuotaUsageRecord(
        id="qta-003",
        operation="videos.insert",
        units_consumed=1600,
        consumed_date="2026-09-08",
        project_id="proj-02",
    )
    repo.save_quota_usage_record(rec1)
    repo.save_quota_usage_record(rec2)
    repo.save_quota_usage_record(rec3)

    spent_07 = repo.get_daily_quota_spent("2026-09-07")
    spent_08 = repo.get_daily_quota_spent("2026-09-08")
    assert spent_07 == 1650
    assert spent_08 == 1600

    history = repo.get_quota_history("2026-09-07")
    assert len(history) == 2
