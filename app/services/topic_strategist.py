"""TopicStrategist scoring candidate topics using configurable YAML dimension weights."""

from pathlib import Path
from typing import Dict, List, Optional, Union
import yaml

from app.domain.models import Channel, TopicCandidate, TopicScoreBreakdown
from app.services.duplicate_detector import DuplicateDetector

# Authoritative default dimension weights (sum to 1.0)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "demand": 0.20,
    "freshness": 0.15,
    "competition": 0.15,
    "channel_fit": 0.15,
    "originality": 0.10,
    "evidence_quality": 0.10,
    "production_feasibility": 0.10,
    "historical_fit": 0.05,
}

CONFIG_FILE_PATH = Path("config/topic_weights.yaml")


class TopicStrategist:
    """Evaluates and ranks video topic candidates based on configurable 8-dimension scoring weights."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        weights_config_path: Optional[Path] = None,
        weights_override: Optional[Dict[str, float]] = None,
        duplicate_threshold: float = 0.80,
    ):
        self.weights = DEFAULT_WEIGHTS.copy()
        self.duplicate_threshold = duplicate_threshold

        path_to_load = weights_config_path or config_path or CONFIG_FILE_PATH
        if path_to_load and path_to_load.exists():
            self._load_yaml_config(path_to_load)

        if weights_override:
            self.weights = weights_override.copy()

        self.duplicate_detector = DuplicateDetector(similarity_threshold=self.duplicate_threshold)

    def _load_yaml_config(self, config_path: Path) -> None:
        """Load weights and threshold from YAML config file."""
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data and isinstance(data, dict):
                    loaded_weights = data.get("weights")
                    if loaded_weights and isinstance(loaded_weights, dict):
                        total = sum(loaded_weights.values())
                        if total > 0:
                            self.weights = {k: v / total for k, v in loaded_weights.items()}
                    if "duplicate_threshold" in data:
                        self.duplicate_threshold = float(data["duplicate_threshold"])
        except Exception:
            self.weights = DEFAULT_WEIGHTS.copy()

    def compute_composite_score(self, breakdown: Union[TopicScoreBreakdown, Dict[str, Optional[float]]]) -> float:
        """Calculate weighted composite score dynamically renormalized over available non-None dimensions (0.0 - 10.0 scale)."""
        if isinstance(breakdown, TopicScoreBreakdown):
            score_map = {
                "demand": breakdown.demand,
                "freshness": breakdown.freshness,
                "competition": breakdown.competition,
                "channel_fit": breakdown.channel_fit,
                "originality": breakdown.originality,
                "evidence_quality": breakdown.evidence_quality,
                "production_feasibility": breakdown.production_feasibility,
                "historical_fit": breakdown.historical_fit,
            }
        else:
            score_map = breakdown

        weighted_sum = 0.0
        total_weight = 0.0

        for dim, weight in self.weights.items():
            if dim in score_map and score_map[dim] is not None:
                weighted_sum += float(score_map[dim]) * weight
                total_weight += weight

        if total_weight == 0:
            return 0.0

        composite = weighted_sum / total_weight
        return round(min(max(composite, 0.0), 10.0), 2)

    def evaluate_candidate(
        self,
        topic_id: str,
        channel: Channel,
        keyword: str,
        raw_scores: Union[TopicScoreBreakdown, Dict[str, Optional[float]]],
        rationale: Optional[str] = None,
        score_reasons: Optional[Dict[str, str]] = None,
        estimated_cpm: Optional[float] = None,
        recent_channel_topics: Optional[List[str]] = None,
    ) -> TopicCandidate:
        """Evaluate and create a validated TopicCandidate after checking for duplicate topics."""
        recent = recent_channel_topics or []

        # 1. Duplicate check
        is_dup, score, matched = self.duplicate_detector.check_duplicate(keyword, recent)
        if is_dup:
            raise ValueError(
                f"Duplicate topic detected: candidate '{keyword}' conflicts with recent topic '{matched}' (similarity score: {score:.2f} >= {self.duplicate_threshold})"
            )

        # 2. Calculate composite score
        composite = self.compute_composite_score(raw_scores)
        if isinstance(raw_scores, dict):
            breakdown_with_composite = TopicScoreBreakdown(
                demand=float(raw_scores["demand"]),
                freshness=float(raw_scores["freshness"]),
                competition=float(raw_scores["competition"]),
                channel_fit=float(raw_scores["channel_fit"]),
                originality=float(raw_scores["originality"]),
                evidence_quality=float(raw_scores["evidence_quality"]),
                production_feasibility=float(raw_scores["production_feasibility"]),
                historical_fit=raw_scores.get("historical_fit"),
                composite_score=composite,
                score_reasons=score_reasons or {},
            )
            authority = float(raw_scores["channel_fit"])
        else:
            breakdown_with_composite = TopicScoreBreakdown(
                demand=raw_scores.demand,
                freshness=raw_scores.freshness,
                competition=raw_scores.competition,
                channel_fit=raw_scores.channel_fit,
                originality=raw_scores.originality,
                evidence_quality=raw_scores.evidence_quality,
                production_feasibility=raw_scores.production_feasibility,
                historical_fit=raw_scores.historical_fit,
                composite_score=composite,
                score_reasons=raw_scores.score_reasons or score_reasons or {},
            )
            authority = raw_scores.channel_fit

        return TopicCandidate(
            id=topic_id,
            channel_id=channel.id,
            keyword=keyword,
            opportunity_score=composite,
            authority_score=authority,
            estimated_cpm=estimated_cpm,
            rationale=rationale,
            score_breakdown=breakdown_with_composite,
        )
