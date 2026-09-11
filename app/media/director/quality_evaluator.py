"""Visual Quality Assurance Evaluator, Duplication Scoring, and Dead-Air Detection."""

import re
from typing import Dict, List, Optional, Set

from app.media.director.models import (
    ChannelCreativeProfile,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VideoQualityReport,
    VisualEvaluation,
    VisualModality,
)


def _tokenize(text: str) -> List[str]:
    """Tokenize and normalize text into lowercase alphanumeric words."""
    if not text:
        return []
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in clean.split() if len(w) > 1]


def calculate_narration_duplication(narration: str, visual_text: Optional[str]) -> float:
    """Calculate the duplication score between narration and on-screen text.

    Returns:
        float: 0.0 (visuals demonstrate/contextualize) to 1.0 (visual is an animated subtitle slide repeating narration).
    """
    if not visual_text or not visual_text.strip():
        return 0.0

    n_tokens = _tokenize(narration)
    v_tokens = _tokenize(visual_text)

    if not v_tokens or not n_tokens:
        return 0.0

    v_set: Set[str] = set(v_tokens)
    n_set: Set[str] = set(n_tokens)

    # Intersection of content words
    intersection = v_set.intersection(n_set)

    # If the visual text is very short (e.g., 1-2 keyword tokens or a stat), treat it as supporting metadata
    if len(v_tokens) <= 3 and not (" ".join(v_tokens) in " ".join(n_tokens) and len(v_tokens) == len(n_tokens)):
        return round(len(intersection) / max(len(v_set), 1) * 0.25, 3)

    # Proportion of visual tokens that were copied from the spoken narration
    v_contained_ratio = len(intersection) / len(v_set)

    # Check if consecutive phrase in visual matches narration
    v_phrase = " ".join(v_tokens)
    n_phrase = " ".join(n_tokens)

    if v_phrase in n_phrase and len(v_tokens) >= 4:
        # Verbatim sentence or clause repeating spoken words
        return round(min(1.0, 0.7 + 0.3 * v_contained_ratio), 3)

    return round(min(1.0, v_contained_ratio), 3)


class VisualShotEvaluator:
    """Evaluates shot quality, narration duplication, and timeline health."""

    def __init__(self, profile: Optional[ChannelCreativeProfile] = None):
        self.profile = profile or ChannelCreativeProfile()

    def evaluate_shot(self, shot: ShotSpec, asset_exists: bool = True) -> VisualEvaluation:
        """Score an individual shot specification and verify quality standards."""
        issues: List[str] = []

        # 1. Narration duplication penalty
        duplication = calculate_narration_duplication(
            narration=shot.narration_segment,
            visual_text=shot.headline_text,
        )
        if duplication > 0.6:
            issues.append(
                f"High narration duplication ({duplication:.2f}): headline '{shot.headline_text}' merely repeats narration."
            )

        # 2. Modality fitness
        modality = shot.visual_modality
        info_value = 0.8
        motion_value = 0.7

        if modality in (VisualModality.DIAGRAM, VisualModality.CODE_ANIMATION, VisualModality.DATA_VISUALIZATION):
            info_value = 0.95
            motion_value = 0.8
        elif modality == VisualModality.STATIC_CARD:
            info_value = 0.3
            motion_value = 0.1
            issues.append("Low-priority STATIC_CARD modality used.")

        # 3. Static duration limit check
        if modality == VisualModality.STATIC_CARD and shot.duration_seconds > 4.0:
            issues.append(f"STATIC_CARD held for {shot.duration_seconds:.1f}s exceeding 4.0s maximum.")

        # 4. Asset existence check
        if not asset_exists:
            issues.append("Rendered asset file is missing.")

        # Composite score
        overall = max(
            0.0,
            round(
                info_value * 0.35
                + motion_value * 0.25
                + (1.0 - duplication) * 0.25
                + (0.15 if asset_exists else 0.0),
                3,
            ),
        )

        recommendation = "ACCEPT"
        if duplication > 0.8 or not asset_exists or (modality == VisualModality.STATIC_CARD and shot.duration_seconds > 5.0):
            recommendation = "REGENERATE"

        return VisualEvaluation(
            visual_relevance=0.85,
            narration_duplication=duplication,
            information_value=info_value,
            motion_value=motion_value,
            continuity=0.8,
            evidence_strength=0.9 if modality == VisualModality.DOCUMENT_EVIDENCE else 0.5,
            readability=0.9,
            aesthetic_quality=0.85,
            overall_score=overall,
            issues=issues,
            recommendation=recommendation,
        )

    def detect_visual_dead_air(
        self,
        timeline: ShotTimeline,
        max_static_duration: float = 5.0,
    ) -> List[str]:
        """Detect shots where static or unmoving visual content is held excessively long."""
        warnings: List[str] = []
        for shot in timeline.shots:
            # Static cards or static screenshots held too long create "dead air"
            if shot.modality == VisualModality.STATIC_CARD and shot.duration > max_static_duration:
                warnings.append(
                    f"VISUAL_DEAD_AIR: shot '{shot.shot_id}' static card unchanged for {shot.duration:.1f}s (threshold {max_static_duration:.1f}s)"
                )
            elif shot.duration > 7.0 and shot.modality != VisualModality.GENERATED_VIDEO:
                warnings.append(
                    f"VISUAL_DEAD_AIR: shot '{shot.shot_id}' held for {shot.duration:.1f}s without shot progression (threshold 7.0s)"
                )
        return warnings

    def generate_quality_report(
        self,
        timeline: ShotTimeline,
        storyboard: Storyboard,
        failed_attempts: int = 0,
        max_static_card_ratio: float = 0.15,
    ) -> VideoQualityReport:
        """Build a comprehensive machine-readable quality report for the completed video production."""
        total_shots = len(timeline.shots)
        total_dur = timeline.total_duration if timeline.total_duration > 0 else sum(s.duration for s in timeline.shots)
        avg_duration = round(total_dur / total_shots, 2) if total_shots > 0 else 0.0

        # Modality distribution
        modality_dist: Dict[str, int] = {}
        static_card_duration = 0.0

        for shot in timeline.shots:
            mod_name = shot.modality.value if hasattr(shot.modality, "value") else str(shot.modality)
            modality_dist[mod_name] = modality_dist.get(mod_name, 0) + 1
            if shot.modality == VisualModality.STATIC_CARD:
                static_card_duration += shot.duration

        static_card_ratio = round(static_card_duration / total_dur, 3) if total_dur > 0 else 0.0

        # Dead air warnings
        dead_air_warnings = self.detect_visual_dead_air(timeline)

        # Narration duplication warnings
        duplication_warnings: List[str] = []
        for spec in storyboard.shots:
            dup_score = calculate_narration_duplication(spec.narration_segment, spec.headline_text)
            if dup_score >= 0.6:
                duplication_warnings.append(
                    f"NARRATION_DUPLICATION: shot '{spec.shot_id}' score {dup_score:.2f} repeats spoken words in headline '{spec.headline_text}'"
                )

        if static_card_ratio > max_static_card_ratio:
            dead_air_warnings.append(
                f"WARNING: {static_card_ratio * 100:.1f}% of visual runtime is static-card based. Target for this format is <= {max_static_card_ratio * 100:.0f}%."
            )

        # Compute overall visual score
        score = 1.0
        score -= min(0.3, static_card_ratio * 0.5)
        score -= min(0.2, len(dead_air_warnings) * 0.05)
        score -= min(0.2, len(duplication_warnings) * 0.05)
        score -= min(0.15, failed_attempts * 0.03)
        overall_score = max(0.1, round(score, 2))

        return VideoQualityReport(
            total_shots=total_shots,
            average_shot_duration=avg_duration,
            static_card_ratio=static_card_ratio,
            modality_distribution=modality_dist,
            visual_dead_air_warnings=dead_air_warnings,
            narration_duplication_warnings=duplication_warnings,
            failed_asset_attempts=failed_attempts,
            overall_visual_score=overall_score,
        )
