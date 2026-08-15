"""Research agent gathering real web evidence, computing SHA-256 provenance hashes, and persisting evidence snapshots."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from uuid import uuid4
import httpx

from app.domain.models import Claim, ResearchDossier, ResearchSource


class ResearchFetchError(RuntimeError):
    """Raised when fetching a research source fails via HTTP/network."""

    def __init__(self, url: str, message: str, status_code: Optional[int] = None):
        super().__init__(f"Failed to fetch source '{url}': {message} (Status: {status_code})")
        self.url = url
        self.status_code = status_code


class ResearchAgent:
    """Collects verifiable research sources and extracts evidence with cryptographic provenance."""

    def __init__(
        self,
        evidence_storage_dir: Optional[Path] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.evidence_dir = Path(evidence_storage_dir or "data/evidence")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.client = http_client or httpx.Client(
            timeout=20.0,
            headers={"User-Agent": "YouTubeAutopilot-ResearchAgent/0.1.0"},
            follow_redirects=True,
        )

    @staticmethod
    def compute_sha256(content: str) -> str:
        """Compute SHA-256 hash of UTF-8 content string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def fetch_source_from_url(
        self,
        source_id: str,
        url: str,
        title: Optional[str] = None,
        authors: Optional[List[str]] = None,
        license_type: str = "UNKNOWN",
    ) -> ResearchSource:
        """Fetch live web content from URL, compute SHA-256 provenance, and persist raw snapshot.

        Raises:
            ResearchFetchError: If the URL cannot be fetched or returns a non-200 status.
        """
        try:
            response = self.client.get(url)
            http_status = response.status_code
            final_url = str(response.url)
            response.raise_for_status()
            raw_text = response.text
        except httpx.HTTPStatusError as e:
            raise ResearchFetchError(
                url=url,
                message=f"HTTP error {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e
        except Exception as e:
            raise ResearchFetchError(
                url=url,
                message=f"Network error: {str(e)}",
                status_code=None,
            ) from e

        content_hash = self.compute_sha256(raw_text)

        # Persist raw evidence snapshot
        snapshot_filename = f"{content_hash}.txt"
        snapshot_path = self.evidence_dir / snapshot_filename
        try:
            snapshot_path.write_text(raw_text, encoding="utf-8")
            saved_path_str = str(snapshot_path)
        except Exception:
            saved_path_str = None

        doc_title = title or f"Document from {url}"

        return ResearchSource(
            id=source_id,
            url=url,
            final_url=final_url,
            http_status=http_status,
            title=doc_title,
            authors=authors or [],
            content_sha256=content_hash,
            content_snapshot=raw_text[:10000],  # Keep up to 10k chars in memory for fact checking
            content_snapshot_path=saved_path_str,
            license_type=license_type,
            fetched_at=datetime.now(timezone.utc),
        )

    def build_dossier_from_urls(
        self,
        topic_id: str,
        urls: List[str],
        summary: str = "",
        dossier_id: Optional[str] = None,
    ) -> ResearchDossier:
        """Fetch all URLs in the plan and compile a verified ResearchDossier."""
        sources: List[ResearchSource] = []
        for idx, url in enumerate(urls):
            src_id = f"src-{topic_id}-{idx+1}"
            source = self.fetch_source_from_url(source_id=src_id, url=url)
            sources.append(source)

        return ResearchDossier(
            id=dossier_id or f"dossier-{uuid4().hex[:8]}",
            topic_id=topic_id,
            sources=sources,
            claims=[],
            summary=summary or f"Compiled research dossier with {len(sources)} verified source(s).",
            created_at=datetime.now(timezone.utc),
        )
