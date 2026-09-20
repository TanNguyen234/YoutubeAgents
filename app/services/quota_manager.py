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


# Official YouTube Data API v3 Granular Quota Limits and Costs
SPECIAL_DAILY_BUCKET_LIMITS: Dict[str, int] = {
    "search.list": 100,
    "videos.insert": 100,
}
GENERAL_DAILY_UNIT_LIMIT = 10000

OPERATION_COSTS: Dict[str, int] = {
    "search.list": 1,
    "videos.insert": 1,
    "videos.list": 1,
    "channels.list": 1,
    "thumbnails.set": 50,
    "playlists.insert": 50,
    "playlistItems.insert": 50,
    "commentThreads.insert": 50,
}


class QuotaBudgetManager:
    """Manages and enforces daily YouTube Data API v3 quota budget limits across granular buckets."""

    def __init__(
        self,
        repository: SQLiteRepository,
        daily_limit: int = GENERAL_DAILY_UNIT_LIMIT,
        search_daily_limit: int = 100,
        upload_daily_limit: int = 100,
    ):
        self.repository = repository
        self.daily_limit = daily_limit
        self.search_daily_limit = search_daily_limit
        self.upload_daily_limit = upload_daily_limit

    def _get_current_pt_date(self) -> str:
        """Get the current date formatted as YYYY-MM-DD in Pacific Time (PT)."""
        now_pt = datetime.now(PT_TZ)
        return now_pt.strftime("%Y-%m-%d")

    def get_operation_bucket(self, operation: str) -> str:
        """Map YouTube Data API operation to its respective quota bucket."""
        if operation == "search.list":
            return "search"
        if operation == "videos.insert":
            return "upload"
        return "general"

    def get_bucket_limit(self, bucket: str) -> int:
        """Get the daily limit configured for a given quota bucket."""
        if bucket == "search":
            return self.search_daily_limit
        if bucket == "upload":
            return self.upload_daily_limit
        return self.daily_limit

    def get_spent_units(
        self,
        target_date: Optional[str] = None,
        operation: Optional[str] = None,
        bucket: Optional[str] = None,
    ) -> int:
        """Get total units consumed on the given date for a specific bucket or general bucket."""
        dt = target_date or self._get_current_pt_date()
        target_bucket = bucket or (self.get_operation_bucket(operation) if operation else "general")
        return self.repository.get_daily_quota_spent_by_bucket(dt, target_bucket)

    def get_remaining_units(
        self,
        target_date: Optional[str] = None,
        operation: Optional[str] = None,
        bucket: Optional[str] = None,
    ) -> int:
        """Get remaining available quota units for the day in the specified bucket."""
        target_bucket = bucket or (self.get_operation_bucket(operation) if operation else "general")
        spent = self.get_spent_units(target_date=target_date, bucket=target_bucket)
        limit = self.get_bucket_limit(target_bucket)
        return max(0, limit - spent)

    def can_spend(self, operation: str, target_date: Optional[str] = None) -> bool:
        """Check if an operation can be performed without exceeding its bucket's daily budget."""
        cost = OPERATION_COSTS.get(operation, 1)
        remaining = self.get_remaining_units(target_date=target_date, operation=operation)
        return remaining >= cost

    def ensure_budget(self, operation: str, target_date: Optional[str] = None) -> None:
        """Validate sufficient budget is available in the operation's bucket, or raise InsufficientQuotaError."""
        cost = OPERATION_COSTS.get(operation, 1)
        bucket = self.get_operation_bucket(operation)
        remaining = self.get_remaining_units(target_date=target_date, operation=operation)
        limit = self.get_bucket_limit(bucket)
        if remaining < cost:
            raise InsufficientQuotaError(
                f"Insufficient daily YouTube API quota in '{bucket}' bucket for '{operation}'. "
                f"Requires {cost} units, but only {remaining} of {limit} units remain."
            )

    def record_spend(
        self, operation: str, project_id: Optional[str] = None, target_date: Optional[str] = None
    ) -> QuotaUsageRecord:
        """Record consumption of quota units after an API response."""
        cost = OPERATION_COSTS.get(operation, 1)
        bucket = self.get_operation_bucket(operation)
        limit = self.get_bucket_limit(bucket)
        dt = target_date or self._get_current_pt_date()

        record = QuotaUsageRecord(
            id=f"qta-{uuid.uuid4().hex[:8]}",
            operation=operation,
            units_consumed=cost,
            daily_budget=limit,
            consumed_date=dt,
            project_id=project_id,
            timestamp=datetime.now(timezone.utc),
        )
        self.repository.save_quota_usage_record(record)
        return record
