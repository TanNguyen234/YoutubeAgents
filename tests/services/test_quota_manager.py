"""Tests for QuotaBudgetManager service and granular quota bucket enforcement.

Covers:
- QUOTA TEST A: search.list dedicated 100-call bucket (independent from general).
- QUOTA TEST B: search.list does not drain general budget; videos.list consumes general normally.
- QUOTA TEST C: videos.insert dedicated 100-call upload bucket.
- QUOTA TEST D: HTTP 401 response records request usage locally before raising typed error.
- QUOTA TEST E: Transport failure (no response) does not falsely record quota spend.
"""

from pathlib import Path
import httpx
import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import VideoLifecycleState
from app.domain.models import Channel, VideoProject
from app.services.quota_manager import InsufficientQuotaError, QuotaBudgetManager
from app.services.youtube_market_signals import (
    YouTubeMarketSignalError,
    YouTubeMarketSignalService,
)


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteRepository:
    db_file = tmp_path / "test_quota.db"
    repository = SQLiteRepository(db_file)
    chan = Channel(
        id="chan-01",
        title="AI Engineering Daily",
        handle="@AIEngineeringDaily",
        niche="Artificial Intelligence",
        target_audience="Engineers",
    )
    repository.save_channel(chan)
    p1 = VideoProject(
        id="proj-01",
        channel_id="chan-01",
        title="Test Video",
        state=VideoLifecycleState.CREATED,
    )
    repository.save_video_project(p1)
    return repository


@pytest.fixture
def quota_manager(repo: SQLiteRepository) -> QuotaBudgetManager:
    return QuotaBudgetManager(
        repository=repo,
        daily_limit=10000,
        search_daily_limit=100,
        upload_daily_limit=100,
    )


# ------------------------------------------------------------------------------
# QUOTA TEST A — Search Has Separate Bucket
# ------------------------------------------------------------------------------
def test_quota_test_a_search_has_separate_bucket(quota_manager: QuotaBudgetManager):
    """Given 99 search.list calls, 1 more is allowed. After #100, search is blocked while general quota is intact."""
    # Record 99 search calls
    for _ in range(99):
        quota_manager.record_spend("search.list")

    assert quota_manager.get_spent_units(operation="search.list") == 99
    assert quota_manager.get_remaining_units(operation="search.list") == 1
    assert quota_manager.can_spend("search.list") is True

    # Record 100th call
    quota_manager.record_spend("search.list")
    assert quota_manager.get_spent_units(operation="search.list") == 100
    assert quota_manager.get_remaining_units(operation="search.list") == 0
    assert quota_manager.can_spend("search.list") is False

    with pytest.raises(InsufficientQuotaError, match="search"):
        quota_manager.ensure_budget("search.list")

    # General quota remains completely independent
    assert quota_manager.get_spent_units() == 0
    assert quota_manager.get_remaining_units() == 10000
    assert quota_manager.can_spend("videos.list") is True


# ------------------------------------------------------------------------------
# QUOTA TEST B — Search Does Not Drain General Budget
# ------------------------------------------------------------------------------
def test_quota_test_b_search_does_not_drain_general_budget(quota_manager: QuotaBudgetManager):
    """Recording many search.list calls does not consume general quota; videos.list consumes general normally."""
    for _ in range(50):
        quota_manager.record_spend("search.list")

    # General quota spent excludes search.list calls
    assert quota_manager.get_spent_units() == 0
    assert quota_manager.get_remaining_units() == 10000

    # videos.list consumes general quota normally (1 unit)
    quota_manager.ensure_budget("videos.list")
    rec = quota_manager.record_spend("videos.list")
    assert rec.units_consumed == 1
    assert quota_manager.get_spent_units() == 1
    assert quota_manager.get_remaining_units() == 9999

    # Other general operations consume per method cost
    quota_manager.record_spend("thumbnails.set")
    assert quota_manager.get_spent_units() == 51
    assert quota_manager.get_remaining_units() == 9949


# ------------------------------------------------------------------------------
# QUOTA TEST C — Videos.Insert Has Dedicated Upload Bucket
# ------------------------------------------------------------------------------
def test_quota_test_c_videos_insert_separate_bucket(quota_manager: QuotaBudgetManager):
    """videos.insert is checked against its own dedicated 100-call upload bucket."""
    assert quota_manager.can_spend("videos.insert") is True
    assert quota_manager.get_remaining_units(operation="videos.insert") == 100

    for _ in range(100):
        quota_manager.record_spend("videos.insert", project_id="proj-01")

    assert quota_manager.get_spent_units(operation="videos.insert") == 100
    assert quota_manager.get_remaining_units(operation="videos.insert") == 0
    assert quota_manager.can_spend("videos.insert") is False

    with pytest.raises(InsufficientQuotaError, match="upload"):
        quota_manager.ensure_budget("videos.insert")

    # General quota and search quota remain intact
    assert quota_manager.get_spent_units() == 0
    assert quota_manager.can_spend("search.list") is True
    assert quota_manager.can_spend("videos.list") is True


# ------------------------------------------------------------------------------
# QUOTA TEST D — HTTP 401 Is Recorded Locally
# ------------------------------------------------------------------------------
def test_quota_test_d_http_401_is_recorded(repo: SQLiteRepository, quota_manager: QuotaBudgetManager):
    """An HTTP 401 response from YouTube must be recorded in the local quota ledger before raising."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=401,
            json={"error": {"code": 401, "message": "Request had invalid authentication credentials."}},
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_handler))
    service = YouTubeMarketSignalService(
        repository=repo,
        quota_manager=quota_manager,
        api_key="invalid-api-key",
        http_client=mock_client,
    )

    with pytest.raises(YouTubeMarketSignalError) as exc_info:
        service.fetch_market_observations(query="Test Query")

    assert "401" in str(exc_info.value)

    # Assert local quota ledger recorded exactly 1 search.list call
    history = repo.get_quota_history()
    assert len(history) == 1
    assert history[0].operation == "search.list"
    assert history[0].units_consumed == 1
    assert quota_manager.get_spent_units(operation="search.list") == 1


# ------------------------------------------------------------------------------
# QUOTA TEST E — Transport Failure Does Not Record Spend
# ------------------------------------------------------------------------------
def test_quota_test_e_transport_failure_does_not_record_spend(repo: SQLiteRepository, quota_manager: QuotaBudgetManager):
    """When a network transport exception occurs before any HTTP response, no quota spend is recorded."""
    def mock_transport_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by peer", request=request)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_transport_error))
    service = YouTubeMarketSignalService(
        repository=repo,
        quota_manager=quota_manager,
        api_key="test-api-key",
        http_client=mock_client,
    )

    with pytest.raises(YouTubeMarketSignalError) as exc_info:
        service.fetch_market_observations(query="Test Query")

    assert "transport failure" in str(exc_info.value).lower()

    # Zero quota usage recorded because no HTTP response was received
    history = repo.get_quota_history()
    assert len(history) == 0
    assert quota_manager.get_spent_units(operation="search.list") == 0
