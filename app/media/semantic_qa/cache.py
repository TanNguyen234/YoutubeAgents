"""Semantic QA cache for deterministic evaluation reuse based on stable input fingerprints."""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.media.acquisition.models import VisualAcquisitionRequest, VisualAssetCandidate
from app.media.director.models import ShotSpec
from app.media.semantic_qa.models import VisualSemanticAssessment

logger = logging.getLogger(__name__)

VISUAL_SEMANTIC_QA_POLICY_VERSION = "semantic-qa-v1"


def get_project_semantic_cache_path(project_id: str, base_dir: Path | str = Path("output/projects")) -> Path:
    """Return canonical path to project semantic QA cache sidecar: output/projects/<project_id>/manifests/semantic_qa_cache.json."""
    return Path(base_dir) / project_id / "manifests" / "semantic_qa_cache.json"


def compute_semantic_input_hash(
    candidate_sha256: str,
    subject: str,
    action: Optional[str],
    environment: Optional[str],
    visual_intent: str,
    visual_modality: str,
    narration_segment: str,
    headline_text: Optional[str] = None,
    evidence_claim: Optional[str] = None,
    evidence_excerpt: Optional[str] = None,
    policy_version: str = VISUAL_SEMANTIC_QA_POLICY_VERSION,
    backend_id: str = "antigravity_cli",
    model_id: Optional[str] = None,
) -> str:
    """Compute a deterministic SHA-256 fingerprint for a semantic visual evaluation request.

    Excludes volatile fields: file paths, timestamps, random request IDs.
    """
    payload = {
        "candidate_sha256": candidate_sha256,
        "subject": (subject or "").strip().lower(),
        "action": (action or "").strip().lower() if action else None,
        "environment": (environment or "").strip().lower() if environment else None,
        "visual_intent": str(visual_intent).strip(),
        "visual_modality": str(visual_modality).strip(),
        "narration_segment": (narration_segment or "").strip(),
        "headline_text": (headline_text or "").strip() if headline_text else None,
        "evidence_claim": (evidence_claim or "").strip() if evidence_claim else None,
        "evidence_excerpt": (evidence_excerpt or "").strip() if evidence_excerpt else None,
        "policy_version": policy_version.strip(),
        "backend_id": backend_id.strip(),
        "model_id": (model_id or "").strip() if model_id else None,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SemanticQACache:
    """Persistent JSON sidecar cache for visual semantic assessments."""

    def __init__(self, cache_file_path: Optional[Path] = None):
        self.cache_file_path = cache_file_path
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.cache_file_path and self.cache_file_path.exists():
            try:
                with open(self.cache_file_path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception as e:
                logger.warning("Failed to load semantic QA cache from %s: %s", self.cache_file_path, e)
                self._cache = {}

    def _save(self) -> None:
        if self.cache_file_path:
            try:
                self.cache_file_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self.cache_file_path.with_name(f"{self.cache_file_path.name}.tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, indent=2)
                os.replace(tmp_path, self.cache_file_path)
            except Exception as e:
                logger.warning("Failed to save semantic QA cache to %s: %s", self.cache_file_path, e)

    def get(self, input_hash: str) -> Optional[VisualSemanticAssessment]:
        """Retrieve cached assessment by semantic input hash."""
        data = self._cache.get(input_hash)
        if not data:
            return None
        try:
            return VisualSemanticAssessment.model_validate(data)
        except Exception as e:
            logger.warning("Corrupted cache entry for hash %s: %s", input_hash, e)
            return None

    def set(self, assessment: VisualSemanticAssessment) -> None:
        """Store assessment in memory and persist to sidecar JSON."""
        self._cache[assessment.semantic_input_hash] = assessment.model_dump(mode="json")
        self._save()
