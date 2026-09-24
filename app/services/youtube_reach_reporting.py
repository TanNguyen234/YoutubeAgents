"""YouTube Reporting API Reach Ingestion Service.

Authoritatively ingests channel thumbnail reach metrics (impressions and reach CTR)
from YouTube Reporting API using report type 'channel_reach_basic_a1'.
Enforces asynchronous report job lifecycle, backfill replacement semantics,
and strict data provenance.
"""

import csv
from datetime import datetime, timezone
import hashlib
import io
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4
import httpx

from app.db.repository import SQLiteRepository
from app.domain.enums import (
    AnalyticsSource,
    PackagingAttributionStatus,
    PublicationStatus,
    ReachSyncStatus,
)
from app.domain.models import (
    ReachObservation,
    ReachSyncResult,
    ReportingJobState,
    ReportingReportReceipt,
)
from app.services.youtube_oauth import (
    YouTubeOAuthError,
    YouTubeOAuthManager,
    YOUTUBE_ANALYTICS_READONLY_SCOPE,
)

logger = logging.getLogger("youtube_reach_reporting")

REACH_REPORT_TYPE_ID = "channel_reach_basic_a1"
REACH_JOB_NAME = "youtubeagents-channel-reach-basic"
REPORTING_API_BASE_URL = "https://youtubereporting.googleapis.com/v1"


class YouTubeReportingError(RuntimeError):
    """Base exception for YouTube Reporting API errors."""
    pass


class ReportingAuthScopeError(YouTubeReportingError):
    """Raised when YouTube Reporting API rejects request due to missing auth or scope."""
    pass


class ReportingHttpError(YouTubeReportingError):
    """Raised when YouTube Reporting API returns a non-200 HTTP response."""
    def __init__(self, status_code: int, message: str, response_body: str = ""):
        super().__init__(f"YouTube Reporting API error ({status_code}): {message}")
        self.status_code = status_code
        self.response_body = response_body


class ReportingNetworkError(YouTubeReportingError):
    """Raised when network transport or connection fails contacting YouTube Reporting API."""
    pass


class ReportingParseError(YouTubeReportingError):
    """Raised when report CSV formatting or schema validation fails."""
    pass


class ReportingReachUnsupportedError(YouTubeReportingError):
    """Raised when channel does not support the required reach basic report type."""
    pass


class YouTubeReachReportingService:
    """Service to manage YouTube Reporting API reach jobs, retrieve reports, and ingest reach observations."""

    def __init__(
        self,
        repository: SQLiteRepository,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        http_client: Optional[httpx.Client] = None,
        api_base_url: str = REPORTING_API_BASE_URL,
    ):
        self.repo = repository
        self.oauth_manager = oauth_manager
        self._custom_http_client = http_client
        self.api_base_url = api_base_url.rstrip("/")

    def _get_client(self) -> httpx.Client:
        if self._custom_http_client is not None:
            return self._custom_http_client
        return httpx.Client(timeout=30.0)

    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.oauth_manager:
            raise ReportingAuthScopeError(
                "No YouTubeOAuthManager configured. Re-run 'python scripts/setup_youtube_auth.py' to authenticate."
            )
        try:
            token = self.oauth_manager.get_access_token()
        except YouTubeOAuthError as e:
            raise ReportingAuthScopeError(
                f"Failed to obtain valid YouTube OAuth token: {e}. "
                "Please authenticate by running 'python scripts/setup_youtube_auth.py'."
            ) from e
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _check_and_raise_error(self, response: httpx.Response, action_desc: str) -> None:
        if response.status_code in (200, 201):
            return

        body_text = response.text
        if response.status_code == 403:
            lowered = body_text.lower()
            if any(term in lowered for term in ["scope", "insufficientpermissions", "forbidden", "access_token_scope_insufficient"]):
                raise ReportingAuthScopeError(
                    f"YouTube Reporting API rejected {action_desc} with 403 Forbidden: "
                    f"Requires scope '{YOUTUBE_ANALYTICS_READONLY_SCOPE}'. "
                    f"Ensure YouTube Reporting API is enabled and re-authenticate."
                )
        raise ReportingHttpError(
            status_code=response.status_code,
            message=f"Failed during {action_desc}: {response.reason_phrase}",
            response_body=body_text,
        )

    def list_report_types(self) -> List[Dict[str, Any]]:
        """List available YouTube Reporting API report types for the authenticated account."""
        url = f"{self.api_base_url}/reportTypes"
        headers = self._get_auth_headers()
        client = self._get_client()

        try:
            resp = client.get(url, headers=headers)
        except httpx.RequestError as e:
            raise ReportingNetworkError(f"Network error listing report types: {e}") from e

        self._check_and_raise_error(resp, "list_report_types")
        data = resp.json()
        return data.get("reportTypes", [])

    def list_jobs(self) -> List[Dict[str, Any]]:
        """List existing YouTube Reporting API jobs."""
        url = f"{self.api_base_url}/jobs"
        headers = self._get_auth_headers()
        client = self._get_client()

        try:
            resp = client.get(url, headers=headers)
        except httpx.RequestError as e:
            raise ReportingNetworkError(f"Network error listing reporting jobs: {e}") from e

        self._check_and_raise_error(resp, "list_jobs")
        data = resp.json()
        return data.get("jobs", [])

    def ensure_reach_reporting_job(self, channel_id: Optional[str] = None) -> ReportingJobState:
        """Idempotently ensure a channel_reach_basic_a1 reporting job exists remotely and locally."""
        # 1. Check existing remote jobs to avoid creating duplicates (Section 4 & 5)
        existing_remote_jobs = self.list_jobs()
        for r_job in existing_remote_jobs:
            if r_job.get("reportTypeId") == REACH_REPORT_TYPE_ID:
                # Matching remote job found: reuse it
                job_id = r_job["id"]
                remote_name = r_job.get("name")
                now = datetime.now(timezone.utc)
                local_state = self.repo.get_reporting_job_by_id(job_id)
                if local_state:
                    return local_state

                new_state = ReportingJobState(
                    job_id=job_id,
                    report_type_id=REACH_REPORT_TYPE_ID,
                    remote_name=remote_name,
                    channel_id=channel_id,
                    last_processed_create_time=None,
                    created_at=now,
                    updated_at=now,
                )
                self.repo.save_reporting_job(new_state)
                return new_state

        # 2. No matching remote job. Verify report type availability before attempting creation
        available_types = self.list_report_types()
        type_ids = [t.get("id") for t in available_types]
        if REACH_REPORT_TYPE_ID not in type_ids:
            raise ReportingReachUnsupportedError(
                f"YouTube Reporting API does not support required report type '{REACH_REPORT_TYPE_ID}' for this channel."
            )

        # 3. Create job remotely exactly once (Section 41)
        url = f"{self.api_base_url}/jobs"
        headers = self._get_auth_headers()
        client = self._get_client()
        body = {
            "reportTypeId": REACH_REPORT_TYPE_ID,
            "name": REACH_JOB_NAME,
        }

        try:
            resp = client.post(url, headers=headers, json=body)
        except httpx.RequestError as e:
            raise ReportingNetworkError(f"Network error creating reach reporting job: {e}") from e

        self._check_and_raise_error(resp, "create_reporting_job")
        created = resp.json()
        job_id = created["id"]
        remote_name = created.get("name", REACH_JOB_NAME)
        now = datetime.now(timezone.utc)

        state = ReportingJobState(
            job_id=job_id,
            report_type_id=REACH_REPORT_TYPE_ID,
            remote_name=remote_name,
            channel_id=channel_id,
            last_processed_create_time=None,
            created_at=now,
            updated_at=now,
        )
        self.repo.save_reporting_job(state)
        return state

    def list_reports(
        self,
        job_id: str,
        created_after: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """List downloadable reports for a specific job, optionally filtered by createdAfter."""
        url = f"{self.api_base_url}/jobs/{job_id}/reports"
        params: Dict[str, str] = {}
        if created_after:
            # Ensure ISO format ending with Z
            if created_after.tzinfo is None:
                iso_ts = created_after.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
            else:
                iso_ts = created_after.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            params["createdAfter"] = iso_ts

        headers = self._get_auth_headers()
        client = self._get_client()

        try:
            resp = client.get(url, headers=headers, params=params)
        except httpx.RequestError as e:
            raise ReportingNetworkError(f"Network error listing reports for job '{job_id}': {e}") from e

        self._check_and_raise_error(resp, f"list_reports for job {job_id}")
        data = resp.json()
        reports = data.get("reports", [])

        # Stable deterministic processing order: startTime ASC, createTime ASC (Section 42)
        def _sort_key(r: Dict[str, Any]) -> tuple:
            return (r.get("startTime", ""), r.get("createTime", ""))

        return sorted(reports, key=_sort_key)

    def download_report(self, download_url: str) -> bytes:
        """Download raw report CSV bytes from authenticated downloadUrl."""
        headers = self._get_auth_headers()
        client = self._get_client()

        try:
            resp = client.get(download_url, headers=headers)
        except httpx.RequestError as e:
            raise ReportingNetworkError(f"Network error downloading report from '{download_url}': {e}") from e

        self._check_and_raise_error(resp, "download_report")
        return resp.content

    def parse_reach_csv(self, csv_bytes: bytes) -> List[Dict[str, Any]]:
        """Parse raw CSV bytes, validating required columns independent of column ordering (Section 13)."""
        try:
            text = csv_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ReportingParseError(f"Failed to decode CSV bytes as UTF-8: {e}") from e

        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            return []

        # Required headers check
        required_columns = {
            "date",
            "channel_id",
            "video_id",
            "video_thumbnail_impressions",
            "video_thumbnail_impressions_ctr",
        }
        actual_columns = set(c.strip() for c in reader.fieldnames if c)
        missing = required_columns - actual_columns
        if missing:
            raise ReportingParseError(
                f"Reach report CSV missing required column(s): {sorted(list(missing))}. "
                f"Found headers: {sorted(list(actual_columns))}"
            )

        parsed_rows: List[Dict[str, Any]] = []
        for line_no, row in enumerate(reader, start=2):
            raw_date = (row.get("date") or "").strip()
            channel_id = (row.get("channel_id") or "").strip()
            video_id = (row.get("video_id") or "").strip()

            raw_impressions = (row.get("video_thumbnail_impressions") or "").strip()
            try:
                impressions = int(raw_impressions)
                if impressions < 0:
                    raise ValueError("Negative impressions")
            except (ValueError, TypeError) as e:
                raise ReportingParseError(
                    f"Invalid video_thumbnail_impressions '{raw_impressions}' on row {line_no}: must be non-negative integer."
                ) from e

            raw_ctr = (row.get("video_thumbnail_impressions_ctr") or "").strip()
            ctr_val: Optional[float] = None
            if raw_ctr:
                try:
                    ctr_val = float(raw_ctr)
                except ValueError as e:
                    raise ReportingParseError(
                        f"Invalid video_thumbnail_impressions_ctr '{raw_ctr}' on row {line_no}: must be numeric or blank."
                    ) from e

            parsed_rows.append({
                "date": raw_date,
                "channel_id": channel_id,
                "video_id": video_id,
                "video_thumbnail_impressions": impressions,
                "video_thumbnail_impressions_ctr": ctr_val,
            })

        return parsed_rows

    def sync_reach(self, channel_id: Optional[str] = None) -> ReachSyncResult:
        """Execute end-to-end Reach synchronization.
        
        Discovers/reuses reach job, queries downloadable reports incrementally,
        downloads CSVs, parses and validates rows, persists ReachObservations
        with backfill semantics, records report receipts, and updates checkpoint.
        """
        try:
            job_state = self.ensure_reach_reporting_job(channel_id=channel_id)
        except ReportingAuthScopeError as e:
            logger.warning(f"Reach sync blocked by missing scope: {e}")
            return ReachSyncResult(
                status=ReachSyncStatus.BLOCKED,
                job_id="",
                message=f"ANALYTICS_SCOPE_REQUIRED: {e}",
            )
        except ReportingReachUnsupportedError as e:
            logger.warning(f"Reach sync unsupported: {e}")
            return ReachSyncResult(
                status=ReachSyncStatus.FAILED,
                job_id="",
                message=str(e),
            )
        except Exception as e:
            logger.error(f"Failed to ensure reporting job: {e}")
            return ReachSyncResult(
                status=ReachSyncStatus.FAILED,
                job_id="",
                message=str(e),
            )

        # Retrieve available reports created after the last processed checkpoint
        checkpoint = job_state.last_processed_create_time
        try:
            reports = self.list_reports(job_id=job_state.job_id, created_after=checkpoint)
        except Exception as e:
            logger.error(f"Failed listing reports for job {job_state.job_id}: {e}")
            return ReachSyncResult(
                status=ReachSyncStatus.FAILED,
                job_id=job_state.job_id,
                message=str(e),
            )

        if not reports:
            return ReachSyncResult(
                status=ReachSyncStatus.NO_REPORT_YET,
                job_id=job_state.job_id,
                reports_processed=0,
                observations_created=0,
                observations_updated=0,
                message="No new downloadable reports available yet.",
            )

        reports_processed = 0
        observations_created = 0
        observations_updated = 0
        unknown_video_rows = 0
        latest_create_time: Optional[datetime] = checkpoint

        for report in reports:
            report_id = report.get("id")
            if not report_id:
                continue

            # Idempotency check: Skip already-processed report ID (Section 11)
            existing_receipt = self.repo.get_report_receipt(report_id)
            if existing_receipt:
                continue

            download_url = report.get("downloadUrl")
            if not download_url:
                continue

            # Download CSV
            csv_bytes = self.download_report(download_url)
            content_sha256 = hashlib.sha256(csv_bytes).hexdigest()

            # Parse all rows (atomic validation: parsing failure aborts without saving receipt)
            rows = self.parse_reach_csv(csv_bytes)

            start_time = datetime.fromisoformat(report["startTime"].replace("Z", "+00:00"))
            end_time = datetime.fromisoformat(report["endTime"].replace("Z", "+00:00"))
            create_time = datetime.fromisoformat(report["createTime"].replace("Z", "+00:00"))

            # Process observations
            for row in rows:
                yt_video_id = row["video_id"]
                # Map to known real/scheduled publication job only (Section 17, 18, 48)
                pub_job = self.repo.get_publication_job_by_youtube_video_id(yt_video_id)
                if not pub_job:
                    unknown_video_rows += 1
                    continue

                # Prepare trusted ReachObservation
                obs = ReachObservation(
                    id=f"reach-{uuid4().hex[:10]}",
                    project_id=pub_job.project_id,
                    publication_job_id=pub_job.id,
                    youtube_video_id=yt_video_id,
                    report_date=row["date"],
                    thumbnail_impressions=row["video_thumbnail_impressions"],
                    thumbnail_impressions_ctr=row["video_thumbnail_impressions_ctr"],
                    source_report_id=report_id,
                    source_report_create_time=create_time,
                    source=AnalyticsSource.YOUTUBE_REPORTING_API,
                    collected_at=datetime.now(timezone.utc),
                )

                # Persist with backfill replacement semantics (Section 10, 44-46)
                # Check whether record exists first to distinguish created vs updated
                existing_obs = self.repo.get_reach_observations_by_publication_job(pub_job.id)
                matching = [o for o in existing_obs if o.report_date == obs.report_date]
                was_saved = self.repo.save_reach_observation(obs)
                if was_saved:
                    if matching:
                        observations_updated += 1
                    else:
                        observations_created += 1

            # Persist report receipt for audit (Section 9, 43, 83)
            receipt = ReportingReportReceipt(
                report_id=report_id,
                job_id=job_state.job_id,
                report_type_id=job_state.report_type_id,
                start_time=start_time,
                end_time=end_time,
                create_time=create_time,
                content_sha256=content_sha256,
                processed_at=datetime.now(timezone.utc),
            )
            self.repo.save_report_receipt(receipt)
            reports_processed += 1

            # Update checkpoint candidate
            if latest_create_time is None or create_time > latest_create_time:
                latest_create_time = create_time

        # Update reporting job checkpoint only after successful batch (Section 82)
        if latest_create_time and (checkpoint is None or latest_create_time > checkpoint):
            job_state.last_processed_create_time = latest_create_time
            job_state.updated_at = datetime.now(timezone.utc)
            self.repo.save_reporting_job(job_state)

        final_status = ReachSyncStatus.SYNCED
        if reports_processed > 0 and observations_created == 0 and observations_updated == 0:
            final_status = ReachSyncStatus.PROCESSED_EMPTY_REPORT

        return ReachSyncResult(
            status=final_status,
            job_id=job_state.job_id,
            reports_processed=reports_processed,
            observations_created=observations_created,
            observations_updated=observations_updated,
            observations_persisted=observations_created + observations_updated,
            unknown_video_rows_count=unknown_video_rows,
            last_create_time=latest_create_time,
            message=f"Sync complete. Processed {reports_processed} report(s).",
        )
