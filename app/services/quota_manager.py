"""YouTube Data API v3 Quota Budget Manager."""

from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

try:
    from zoneinfo import ZoneInfo
    PT_TZ = ZoneInfo("America/Los_Angeles")
except Exception:
    # Fallback to UTC -8 if zoneinfo isn't available
    from datetime import timezone, timedelta
    PT_TZ = timezone(timedelta(hours=-8))

from app.db.repository import SQLiteRepository
from app.domain.models import QuotaUsageRecord


class InsufficientQuotaError(RuntimeError):
    """Raised when an operation would exceed the daily YouTube API quota allocation."""
    pass


# Official YouTube Data API v3 Quota Costs
OPERATION_COSTS: Dict[str, int] = {
    "videos.insert": 1600,
    "thumbnails.set": 50,
    "playlists.insert": 50,
    "playlistItems.insert": 50,
    "search.list": 100,
    "videos.list": 1,
    "channels.list": 1,
    "commentThreads.insert": 50,
}


class QuotaBudgetManager:
    """Manages and enforces daily YouTube Data API v3 quota budget limits."""

    def __init__(self, repository: SQLiteRepository, daily_limit: int = 10000):
        self.repository = repository
        self.daily_limit = daily_limit

    def _get_current_pt_date(self) -> str:
        """Get the current date formatted as YYYY-MM-DD in Pacific Time (PT)."""
        now_pt = datetime.now(PT_TZ)
        return now_pt.strftime("%Y-%m-%d")

    def get_spent_units(self, target_date: Optional[str] = None) -> int:
        """Get total units consumed on the given date (defaults to current PT date)."""
        dt = target_date or self._get_current_pt_date()
        return self.repository.get_daily_quota_spent(dt)

    def get_remaining_units(self, target_date: Optional[str] = None) -> int:
        """Get remaining available quota units for the day."""
        spent = self.get_spent_units(target_date)
        return max(0, self.daily_limit - spent)

    def can_spend(self, operation: str, target_date: Optional[str] = None) -> bool:
        """Check if an operation can be performed without exceeding daily budget."""
        cost = OPERATION_COSTS.get(operation, 1)
        remaining = self.get_remaining_units(target_date)
        return remaining >= cost

    def ensure_budget(self, operation: str, target_date: Optional[str] = None) -> None:
        """Validate sufficient budget is available, or raise InsufficientQuotaError."""
        cost = OPERATION_COSTS.get(operation, 1)
        remaining = self.get_remaining_units(target_date)
        if remaining < cost:
            raise InsufficientQuotaError(
                f"Insufficient daily YouTube API quota for '{operation}'. "
                f"Requires {cost} units, but only {remaining} of {self.daily_limit} units remain."
            )

    def record_spend(
        self, operation: str, project_id: Optional[str] = None, target_date: Optional[str] = None
    ) -> QuotaUsageRecord:
        """Record consumption of quota units after a successful API call."""
        cost = OPERATION_COSTS.get(operation, 1)
        dt = target_date or self._get_current_pt_date()

        record = QuotaUsageRecord(
            id=f"qta-{uuid.uuid4().hex[:8]}",
            operation=operation,
            units_consumed=cost,
            daily_budget=self.daily_limit,
            consumed_date=dt,
            project_id=project_id,
            timestamp=datetime.now(timezone.utc),
        )
        self.repository.save_quota_usage_record(record)
        return record
