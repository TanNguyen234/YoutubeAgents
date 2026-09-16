"""Licensed stock media provider abstraction and implementation."""

import hashlib
import os
from pathlib import Path
import re
from typing import Dict, List, Optional, Protocol, Tuple

from app.media.acquisition.models import VisualAssetCandidate, VisualSourceType


class StockMediaProvider(Protocol):
    """Protocol for licensed stock media providers."""

    @property
    def provider_name(self) -> str:
        ...

    def is_available(self) -> bool:
        ...

    def search_video(
        self,
        subject: str,
        action: Optional[str] = None,
        environment: Optional[str] = None,
        min_duration_seconds: float = 2.0,
        max_results: int = 3,
    ) -> List[Dict[str, any]]:
        ...

    def download_candidate(
        self,
        asset_metadata: Dict[str, any],
        output_path: Path,
    ) -> Optional[VisualAssetCandidate]:
        ...


FORBIDDEN_GENERIC_QUERIES = {
    "technology",
    "tech",
    "ai",
    "computer",
    "server",
    "business",
    "businessman",
    "office",
    "abstract",
    "code",
    "cyberpunk",
    "futuristic",
    "robot",
}


def build_semantic_stock_query(
    subject: str,
    action: Optional[str] = None,
    environment: Optional[str] = None,
) -> str:
    """Derive semantic stock search query from ShotSpec, filtering out meaningless generic tech buzzwords."""
    terms = []
    # Add concrete subject
    if subject:
        clean_sub = re.sub(r"[^\w\s]", " ", subject.lower()).strip()
        sub_words = [w for w in clean_sub.split() if w not in FORBIDDEN_GENERIC_QUERIES and len(w) > 2]
        if sub_words:
            terms.append(" ".join(sub_words[:3]))

    # Add concrete action
    if action:
        clean_act = re.sub(r"[^\w\s]", " ", action.lower()).strip()
        act_words = [w for w in clean_act.split() if w not in FORBIDDEN_GENERIC_QUERIES and len(w) > 2]
        if act_words:
            terms.append(" ".join(act_words[:2]))

    # Add environment
    if environment:
        clean_env = re.sub(r"[^\w\s]", " ", environment.lower()).strip()
        env_words = [w for w in clean_env.split() if w not in FORBIDDEN_GENERIC_QUERIES and len(w) > 2]
        if env_words:
            terms.append(" ".join(env_words[:2]))

    query = " ".join(terms).strip()
    return query if query else (subject.strip() if subject else "")


class PexelsStockProvider:
    """Licensed stock video provider integrating Pexels API with strict credential checking."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("PEXELS_API_KEY", "").strip()

    @property
    def provider_name(self) -> str:
        return "pexels"

    def is_available(self) -> bool:
        """Return True only if a valid non-empty API key is present."""
        return bool(self._api_key and len(self._api_key) > 8)

    def search_video(
        self,
        subject: str,
        action: Optional[str] = None,
        environment: Optional[str] = None,
        min_duration_seconds: float = 2.0,
        max_results: int = 3,
    ) -> List[Dict[str, any]]:
        """Search Pexels stock video library using semantic query."""
        if not self.is_available():
            return []

        query = build_semantic_stock_query(subject, action, environment)
        if not query:
            return []

        # If key is available, execute real request via urllib
        import json
        from urllib.parse import quote_plus
        from urllib.request import Request, urlopen

        encoded_q = quote_plus(query)
        req_url = f"https://api.pexels.com/videos/search?query={encoded_q}&per_page={max_results}&orientation=portrait"
        headers = {"Authorization": self._api_key, "User-Agent": "YoutubeAgents/1.0"}

        try:
            req = Request(req_url, headers=headers)
            with urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    return []
                data = json.loads(resp.read().decode("utf-8"))
                videos = data.get("videos", [])
                results = []
                for v in videos:
                    dur = v.get("duration", 0)
                    if dur < min_duration_seconds:
                        continue
                    files = v.get("video_files", [])
                    # Prefer HD portrait or highest res
                    best_file = next((f for f in files if f.get("quality") == "hd"), files[0] if files else None)
                    if not best_file:
                        continue
                    results.append({
                        "id": str(v.get("id")),
                        "url": v.get("url"),
                        "download_url": best_file.get("link"),
                        "width": best_file.get("width"),
                        "height": best_file.get("height"),
                        "duration": float(dur),
                        "user": v.get("user", {}).get("name", "Pexels Creator"),
                        "license": "Pexels License (Free to use with attribution)",
                    })
                return results
        except Exception:
            return []

    def download_candidate(
        self,
        asset_metadata: Dict[str, any],
        output_path: Path,
    ) -> Optional[VisualAssetCandidate]:
        """Download remote stock candidate to local disk."""
        if not self.is_available():
            return None

        download_url = asset_metadata.get("download_url")
        if not download_url:
            return None

        from urllib.request import Request, urlopen

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            req = Request(download_url, headers={"User-Agent": "YoutubeAgents/1.0"})
            with urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return None
                output_path.write_bytes(resp.read())

            content = output_path.read_bytes()
            sha = hashlib.sha256(content).hexdigest()

            return VisualAssetCandidate(
                candidate_id=f"stock_pexels_{asset_metadata.get('id', output_path.stem)}",
                source_type=VisualSourceType.STOCK_MEDIA,
                file_path=str(output_path),
                source_url=asset_metadata.get("url"),
                license_type=asset_metadata.get("provider_license") or "PEXELS",
                attribution=f"Video by {asset_metadata.get('user', 'Creator')} via Pexels",
                content_sha256=sha,
                width=asset_metadata.get("width"),
                height=asset_metadata.get("height"),
                duration_seconds=asset_metadata.get("duration"),
                acquisition_method="pexels_stock_video",
                is_synthetic=False,
                raw_metadata={
                    "provider": "pexels",
                    "provider_asset_id": asset_metadata.get("id"),
                    "source_url": asset_metadata.get("url"),
                    "creator": asset_metadata.get("user"),
                },
            )
        except Exception:
            return None
