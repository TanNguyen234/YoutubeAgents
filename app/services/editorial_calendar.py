"""Editorial Calendar and Content Series Service."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from app.db.repository import SQLiteRepository
from app.domain.enums import EditorialSlotStatus, PlatformFormat
from app.domain.models import ContentSeries, EditorialSlot


class EditorialCalendarService:
    """Manages channel content series, editorial release calendars, and episodic continuity."""

    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    def register_series(
        self,
        channel_id: str,
        title: str,
        target_niche: str,
        series_id: Optional[str] = None,
        description: Optional[str] = None,
        default_format: PlatformFormat = PlatformFormat.SHORTS_9_16,
        frequency_per_week: int = 3,
        playlist_id: Optional[str] = None,
        visual_style_preset: str = "modern_tech",
    ) -> ContentSeries:
        """Register a new recurring episodic series for a channel."""
        sid = series_id or f"ser-{uuid.uuid4().hex[:8]}"
        series = ContentSeries(
            id=sid,
            channel_id=channel_id,
            title=title,
            description=description,
            target_niche=target_niche,
            default_format=default_format,
            frequency_per_week=frequency_per_week,
            playlist_id=playlist_id,
            visual_style_preset=visual_style_preset,
            next_episode_number=1,
            is_active=True,
        )
        self.repository.save_content_series(series)
        return series

    def get_series(self, series_id: str) -> Optional[ContentSeries]:
        return self.repository.get_content_series(series_id)

    def list_channel_series(self, channel_id: str) -> List[ContentSeries]:
        return self.repository.list_content_series(channel_id)

    def create_slot(
        self,
        channel_id: str,
        slot_time: datetime,
        target_topic: str,
        series_id: Optional[str] = None,
        slot_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> EditorialSlot:
        """Create an individual scheduled editorial slot."""
        sid = slot_id or f"slot-{uuid.uuid4().hex[:8]}"
        episode_num = None
        if series_id:
            series = self.repository.get_content_series(series_id)
            if series:
                episode_num = series.next_episode_number

        slot = EditorialSlot(
            id=sid,
            channel_id=channel_id,
            series_id=series_id,
            slot_time=slot_time,
            status=EditorialSlotStatus.PLANNED,
            target_topic=target_topic,
            episode_number=episode_num,
            notes=notes,
        )
        self.repository.save_editorial_slot(slot)
        return slot

    def generate_slots(
        self,
        channel_id: str,
        series_id: str,
        start_date: datetime,
        num_weeks: int = 2,
        posting_hour_utc: int = 18,
    ) -> List[EditorialSlot]:
        """Automatically generate cadence slots for a series across a time window."""
        series = self.repository.get_content_series(series_id)
        if not series:
            raise ValueError(f"Content series '{series_id}' not found.")

        slots: List[EditorialSlot] = []
        freq = max(1, min(series.frequency_per_week, 7))

        # Interval between posts in days
        interval_days = 7 / freq
        current_dt = start_date.replace(hour=posting_hour_utc, minute=0, second=0, microsecond=0)

        total_slots = freq * num_weeks
        for i in range(total_slots):
            slot_time = current_dt + timedelta(days=round(i * interval_days))
            slot = self.create_slot(
                channel_id=channel_id,
                series_id=series_id,
                slot_time=slot_time,
                target_topic=f"{series.title} Episode {series.next_episode_number + i}",
            )
            slots.append(slot)
        return slots

    def book_slot(self, slot_id: str, project_id: str) -> EditorialSlot:
        """Assign a video project to an editorial slot and mark it IN_PRODUCTION."""
        slot = self.repository.get_editorial_slot(slot_id)
        if not slot:
            raise ValueError(f"Editorial slot '{slot_id}' not found.")

        self.repository.update_editorial_slot_status(
            slot_id=slot_id,
            status=EditorialSlotStatus.IN_PRODUCTION,
            project_id=project_id,
        )
        if slot.series_id:
            self.repository.increment_series_episode(slot.series_id)

        updated = self.repository.get_editorial_slot(slot_id)
        return updated  # type: ignore

    def get_episodic_continuity_context(
        self, series_id: str, current_episode: int
    ) -> Dict[str, Any]:
        """Produce narrative continuity tokens for the scriptwriter and thumbnail designer."""
        series = self.repository.get_content_series(series_id)
        series_title = series.title if series else "Independent Feature"
        badge = f"{series_title} | Ep {current_episode:02d}"

        return {
            "series_id": series_id,
            "series_title": series_title,
            "current_episode": current_episode,
            "display_badge": badge,
            "call_to_action": f"Subscribe to follow the '{series_title}' series. Next episode coming soon!",
            "playlist_id": series.playlist_id if series else None,
        }
