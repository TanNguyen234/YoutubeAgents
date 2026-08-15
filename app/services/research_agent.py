"""Research agent gathering real evidence, computing SHA-256 provenance hashes, and assembling ResearchDossier."""

import hashlib
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4
import httpx

from app.domain.models import Channel, Claim, ResearchDossier, ResearchSource


class ResearchAgent:
    """Collects verifiable research sources and extracts evidence with cryptographic provenance."""

    def __init__(self, http_client: Optional[httpx.Client] = None):
        self.client = http_client or httpx.Client(
            timeout=15.0,
            headers={"User-Agent": "YouTubeAutopilot-ResearchAgent/0.1.0"},
            follow_redirects=True,
        )

    @staticmethod
    def compute_sha256(content: str) -> str:
        """Compute SHA-256 hash of UTF-8 content string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def fetch_source_content(self, url: str) -> str:
        """Fetch real web content from a given URL."""
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def create_source_from_url(
        self,
        source_id: str,
        url: str,
        title: str,
        authors: Optional[List[str]] = None,
        license_type: str = "UNKNOWN",
        raw_text: Optional[str] = None,
    ) -> ResearchSource:
        """Create a ResearchSource with real SHA-256 cryptographic provenance."""
        if raw_text is None:
            raw_text = self.fetch_source_content(url)

        content_hash = self.compute_sha256(raw_text)
        return ResearchSource(
            id=source_id,
            url=url,
            title=title,
            authors=authors or [],
            content_sha256=content_hash,
            license_type=license_type,
            fetched_at=datetime.now(timezone.utc),
        )

    def compile_dossier(
        self,
        topic_id: str,
        sources: List[ResearchSource],
        claims: List[Claim],
        summary: str,
        dossier_id: Optional[str] = None,
    ) -> ResearchDossier:
        """Assemble verified sources and claims into a ResearchDossier."""
        return ResearchDossier(
            id=dossier_id or f"dossier-{uuid4().hex[:8]}",
            topic_id=topic_id,
            sources=sources,
            claims=claims,
            summary=summary,
            created_at=datetime.now(timezone.utc),
        )
