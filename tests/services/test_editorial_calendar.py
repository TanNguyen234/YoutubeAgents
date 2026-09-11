"""Tests for EditorialCalendarService."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import EditorialSlotStatus, PlatformFormat, VideoLifecycleState
from app.domain.models import Channel, ContentSeries, VideoProject
from app.services.editorial_calendar import EditorialCalendarService


@pytest.fixture
def calendar_service(tmp_path: Path) -> EditorialCalendarService:
    db_file = tmp_path / "test_calendar.db"
    repo = SQLiteRepository(db_file)
    channel = Channel(
        id="chan-01",
        title="Tech Vanguard",
        handle="@TechVanguard",
        niche="Deep Tech",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    return EditorialCalendarService(repository=repo)


def test_create_and_fetch_series(calendar_service: EditorialCalendarService):
    series = calendar_service.register_series(
        channel_id="chan-01",
        series_id="ser-deep-dive",
        title="Deep Tech Weekly",
        description="Weekly in-depth architecture reviews",
        target_niche="Deep Tech",
        default_format=PlatformFormat.LONG_FORM_16_9,
        frequency_per_week=1,
    )
    assert series.id == "ser-deep-dive"
    assert series.next_episode_number == 1

    fetched = calendar_service.get_series("ser-deep-dive")
    assert fetched is not None
    assert fetched.title == "Deep Tech Weekly"


def test_generate_schedule_slots(calendar_service: EditorialCalendarService):
    series = calendar_service.register_series(
        channel_id="chan-01",
        series_id="ser-shorts",
        title="Daily Tech Shorts",
        target_niche="Tech News",
        default_format=PlatformFormat.SHORTS_9_16,
        frequency_per_week=3,
    )

    start_time = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    slots = calendar_service.generate_slots(
        channel_id="chan-01",
        series_id=series.id,
        start_date=start_time,
        num_weeks=2,
        posting_hour_utc=18,
    )
    # 3 per week * 2 weeks = 6 slots
    assert len(slots) == 6
    assert all(s.channel_id == "chan-01" for s in slots)
    assert all(s.series_id == "ser-shorts" for s in slots)
    assert all(s.status == EditorialSlotStatus.PLANNED for s in slots)


def test_assign_project_to_slot_and_continuity(calendar_service: EditorialCalendarService):
    series = calendar_service.register_series(
        channel_id="chan-01",
        series_id="ser-shorts",
        title="Daily Tech Shorts",
        target_niche="Tech News",
    )
    slot_time = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
    slot = calendar_service.create_slot(
        channel_id="chan-01",
        slot_id="slot-01",
        series_id=series.id,
        slot_time=slot_time,
        target_topic="Transformer KV Cache Optimization",
    )
    assert slot.episode_number == 1

    # Project to assign
    project = VideoProject(
        id="proj-01",
        channel_id="chan-01",
        title="KV Cache Optimization",
        state=VideoLifecycleState.CREATED,
    )
    calendar_service.repository.save_video_project(project)

    # Assign to slot
    assigned_slot = calendar_service.book_slot(
        slot_id="slot-01",
        project_id="proj-01",
    )
    assert assigned_slot.status == EditorialSlotStatus.IN_PRODUCTION
    assert assigned_slot.project_id == "proj-01"

    # Verify series continuity context
    continuity = calendar_service.get_episodic_continuity_context(series_id=series.id, current_episode=1)
    assert continuity["series_title"] == "Daily Tech Shorts"
    assert continuity["current_episode"] == 1
    assert "Ep 01" in continuity["display_badge"]
