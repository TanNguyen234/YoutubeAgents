"""YouTube Data API v3 Market Signal Service.

Fetches bounded public video observations, calculates deterministic market metrics
(velocity, freshness, competition proxies), and produces auditable MarketSignalSnapshot records.
"""

from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
import httpx

from app.db.repository import SQLiteRepository
from app.domain.models import Channel, MarketSignalSnapshot, MarketVideoObservation
from app.services.quota_manager import InsufficientQuotaError, QuotaBudgetManager
from app.services.youtube_oauth import YouTubeOAuthManager

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
MIN_VALID_VIDEO_SAMPLE = 5
MINIMUM_AGE_FLOOR_DAYS = 0.5


class YouTubeMarketSignalError(RuntimeError):
    """Raised when market signal collection encounters an unrecoverable failure or missing credentials."""
    pass


def parse_iso8601_duration(duration_str: str) -> int:
    """Parse ISO 8601 video duration (e.g. PT4M13S, PT1H2M10S) to seconds."""
    if not duration_str or not duration_str.startswith("PT"):
        return 0
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration_str)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def compute_percentile(data: List[float], percentile: float) -> float:
    """Calculate percentile using linear interpolation between closest ranks."""
    if not data:
        return 0.0
    s = sorted(data)
    if len(s) == 1:
        return float(s[0])
    idx = (len(s) - 1) * (percentile / 100.0)
    lower = int(idx)
    upper = min(lower + 1, len(s) - 1)
    weight = idx - lower
    return float(s[lower] * (1.0 - weight) + s[upper] * weight)


class YouTubeMarketSignalService:
    """Collects real observable market evidence from YouTube Data API v3."""

    def __init__(
        self,
        repository: Optional[SQLiteRepository] = None,
        quota_manager: Optional[QuotaBudgetManager] = None,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        api_key: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
        cache_ttl_hours: float = 24.0,
        formula_version: str = "v1.0",
    ):
        self.repository = repository
        self.quota_manager = quota_manager or (QuotaBudgetManager(repository) if repository else None)
        self.oauth_manager = oauth_manager or YouTubeOAuthManager()
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        self._custom_client = http_client
        self.cache_ttl_hours = cache_ttl_hours
        self.formula_version = formula_version

    def _get_auth_params_and_headers(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Return (params, headers) carrying either valid OAuth bearer token or API key.

        Raises:
            YouTubeMarketSignalError: If neither valid OAuth token nor API key is available.
        """
        headers: Dict[str, str] = {}
        params: Dict[str, str] = {}

        if self.oauth_manager and self.oauth_manager.has_valid_token():
            try:
                token = self.oauth_manager.get_access_token()
                headers["Authorization"] = f"Bearer {token}"
                return params, headers
            except Exception:
                pass

        if self.api_key:
            params["key"] = self.api_key
            return params, headers

        raise YouTubeMarketSignalError(
            "YouTube API credentials unavailable: neither valid OAuth token nor YOUTUBE_API_KEY found."
        )

    def calculate_derived_metrics(
        self,
        observations: List[MarketVideoObservation],
        collected_at: datetime,
        estimated_result_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Compute deterministic market metrics from a sample of observed market videos."""
        sample_size = len(observations)
        if sample_size == 0:
            return {
                "sample_size": 0,
                "recent_video_count_7d": 0,
                "recent_video_count_30d": 0,
                "recent_share_30d": 0.0,
                "median_views": 0.0,
                "p75_views": 0.0,
                "median_age_days": 0.0,
                "median_views_per_day": 0.0,
                "p75_views_per_day": 0.0,
                "unique_creator_count": 0,
                "top_creator_share": 0.0,
                "estimated_result_count": estimated_result_count,
            }

        views_list: List[float] = [float(v.view_count) for v in observations]
        ages_list: List[float] = []
        vpd_list: List[float] = []

        c7d = 0
        c30d = 0

        for v in observations:
            # Ensure collected_at and published_at are timezone-aware UTC
            pub = v.published_at if v.published_at.tzinfo else v.published_at.replace(tzinfo=timezone.utc)
            col = collected_at if collected_at.tzinfo else collected_at.replace(tzinfo=timezone.utc)
            age_raw = (col - pub).total_seconds() / 86400.0
            age_days = max(age_raw, MINIMUM_AGE_FLOOR_DAYS)
            ages_list.append(round(age_days, 2))

            vpd = v.view_count / age_days
            vpd_list.append(vpd)

            if age_days <= 7.0:
                c7d += 1
            if age_days <= 30.0:
                c30d += 1

        recent_share_30d = round(c30d / sample_size, 4)
        median_views = float(statistics.median(views_list))
        p75_views = round(compute_percentile(views_list, 75.0), 2)
        median_age_days = round(float(statistics.median(ages_list)), 2)
        median_vpd = round(float(statistics.median(vpd_list)), 2)
        p75_vpd = round(compute_percentile(vpd_list, 75.0), 2)

        creator_ids = [v.channel_id for v in observations if v.channel_id]
        unique_creator_count = len(set(creator_ids))
        if creator_ids:
            counts = Counter(creator_ids)
            top_creator_share = round(counts.most_common(1)[0][1] / sample_size, 4)
        else:
            top_creator_share = 0.0

        return {
            "sample_size": sample_size,
            "recent_video_count_7d": c7d,
            "recent_video_count_30d": c30d,
            "recent_share_30d": recent_share_30d,
            "median_views": median_views,
            "p75_views": p75_views,
            "median_age_days": median_age_days,
            "median_views_per_day": median_vpd,
            "p75_views_per_day": p75_vpd,
            "unique_creator_count": unique_creator_count,
            "top_creator_share": top_creator_share,
            "estimated_result_count": estimated_result_count,
        }

    def fetch_market_observations(
        self,
        query: str,
        max_results: int = 10,
        region_code: Optional[str] = None,
        relevance_language: Optional[str] = None,
    ) -> Tuple[List[MarketVideoObservation], Optional[int]]:
        """Query YouTube Data API v3 search.list and videos.list to gather real video stats.

        Returns:
            (observations, estimated_result_count)
        """
        # Step 1: Pre-flight quota check for search.list (100 units)
        if self.quota_manager:
            self.quota_manager.ensure_budget("search.list")

        auth_params, auth_headers = self._get_auth_params_and_headers()

        search_params: Dict[str, Any] = {
            **auth_params,
            "part": "snippet",
            "type": "video",
            "q": query,
            "maxResults": min(max(1, max_results), 50),
        }
        if region_code:
            search_params["regionCode"] = region_code
        if relevance_language:
            search_params["relevanceLanguage"] = relevance_language

        client = self._custom_client or httpx.Client(timeout=30.0)
        close_client = self._custom_client is None

        try:
            try:
                resp = client.get(
                    f"{YOUTUBE_API_BASE}/search",
                    params=search_params,
                    headers=auth_headers,
                )
            except (httpx.RequestError, httpx.TransportError) as exc:
                raise YouTubeMarketSignalError(
                    f"YouTube search.list transport failure: {exc}"
                ) from exc

            # HTTP response received, including 4xx/5xx -> record request quota usage
            if self.quota_manager:
                self.quota_manager.record_spend("search.list")

            if resp.status_code != 200:
                raise YouTubeMarketSignalError(
                    f"YouTube search.list failed ({resp.status_code}): {resp.text}"
                )

            search_data = resp.json()
            items = search_data.get("items", [])
            page_info = search_data.get("pageInfo", {})
            estimated_total = page_info.get("totalResults")

            video_ids = [
                it["id"]["videoId"]
                for it in items
                if isinstance(it.get("id"), dict) and it["id"].get("videoId")
            ]

            if not video_ids:
                return [], estimated_total

            # Step 2: Quota check and fetch for videos.list (1 unit)
            if self.quota_manager:
                self.quota_manager.ensure_budget("videos.list")

            videos_params: Dict[str, Any] = {
                **auth_params,
                "part": "statistics,contentDetails,snippet",
                "id": ",".join(video_ids),
            }

            try:
                v_resp = client.get(
                    f"{YOUTUBE_API_BASE}/videos",
                    params=videos_params,
                    headers=auth_headers,
                )
            except (httpx.RequestError, httpx.TransportError) as exc:
                raise YouTubeMarketSignalError(
                    f"YouTube videos.list transport failure: {exc}"
                ) from exc

            # HTTP response received, including 4xx/5xx -> record request quota usage
            if self.quota_manager:
                self.quota_manager.record_spend("videos.list")

            if v_resp.status_code != 200:
                raise YouTubeMarketSignalError(
                    f"YouTube videos.list failed ({v_resp.status_code}): {v_resp.text}"
                )

            v_data = v_resp.json()
            v_items = v_data.get("items", [])

            observations: List[MarketVideoObservation] = []
            for item in v_items:
                v_id = item.get("id")
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                content = item.get("contentDetails", {})

                pub_str = snippet.get("publishedAt")
                if pub_str:
                    try:
                        pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except Exception:
                        pub_dt = datetime.now(timezone.utc)
                else:
                    pub_dt = datetime.now(timezone.utc)

                duration_sec = parse_iso8601_duration(content.get("duration", ""))
                view_cnt = int(stats.get("viewCount", 0))
                like_cnt = int(stats["likeCount"]) if "likeCount" in stats else None
                comment_cnt = int(stats["commentCount"]) if "commentCount" in stats else None

                observations.append(
                    MarketVideoObservation(
                        video_id=v_id,
                        title=snippet.get("title", ""),
                        channel_id=snippet.get("channelId", ""),
                        channel_title=snippet.get("channelTitle", ""),
                        published_at=pub_dt,
                        view_count=view_cnt,
                        duration_seconds=duration_sec,
                        like_count=like_cnt,
                        comment_count=comment_cnt,
                    )
                )

            return observations, estimated_total
        finally:
            if close_client:
                client.close()

    def get_market_signal_snapshot(
        self,
        channel: Channel,
        query: str,
        batch_id: Optional[str] = None,
        max_results: int = 10,
        force_refresh: bool = False,
    ) -> MarketSignalSnapshot:
        """Collect or retrieve cached MarketSignalSnapshot for a topic query."""
        bid = batch_id or f"msb-{uuid4().hex[:8]}"

        # Check repository cache
        if self.repository and not force_refresh:
            cached = self.repository.get_latest_market_signal(
                channel_id=channel.id,
                query=query,
                ttl_hours=self.cache_ttl_hours,
            )
            if cached:
                return cached

        # Fetch live observations
        collected_at = datetime.now(timezone.utc)
        region = getattr(channel, "region_code", None)
        lang = getattr(channel, "default_language", None)

        observations, est_results = self.fetch_market_observations(
            query=query,
            max_results=max_results,
            region_code=region,
            relevance_language=lang,
        )

        metrics = self.calculate_derived_metrics(
            observations=observations,
            collected_at=collected_at,
            estimated_result_count=est_results,
        )

        sample_video_ids = [o.video_id for o in observations]
        confidence = "HIGH" if len(observations) >= MIN_VALID_VIDEO_SAMPLE else "INSUFFICIENT_SIGNAL"

        snapshot = MarketSignalSnapshot(
            id=f"mss-{uuid4().hex[:8]}",
            batch_id=bid,
            channel_id=channel.id,
            query=query,
            source="YOUTUBE_DATA_API_V3",
            collected_at=collected_at,
            sample_video_ids=sample_video_ids,
            sample_size=metrics["sample_size"],
            recent_video_count_7d=metrics["recent_video_count_7d"],
            recent_video_count_30d=metrics["recent_video_count_30d"],
            recent_share_30d=metrics["recent_share_30d"],
            median_views=metrics["median_views"],
            p75_views=metrics["p75_views"],
            median_age_days=metrics["median_age_days"],
            median_views_per_day=metrics["median_views_per_day"],
            p75_views_per_day=metrics["p75_views_per_day"],
            unique_creator_count=metrics["unique_creator_count"],
            top_creator_share=metrics["top_creator_share"],
            estimated_result_count=metrics["estimated_result_count"],
            formula_version=self.formula_version,
            confidence=confidence,
            raw_metrics=metrics,
            derived_scores={},
            created_at=datetime.now(timezone.utc),
        )

        if self.repository:
            self.repository.save_market_signal_snapshot(snapshot)

        return snapshot
