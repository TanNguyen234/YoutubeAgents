"""Visual Quality Assurance Evaluator, Duplication Scoring, and Dead-Air Detection."""

from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from app.domain.enums import ClaimVerificationVerdict, RetentionCueType
from app.domain.models import FactCheckReport, ResearchDossier, TimedRetentionCue
from app.media.director.models import (
    ChannelCreativeProfile,
    ChartDatumOrigin,
    EvidenceBinding,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VideoQualityReport,
    VisualEvaluation,
    VisualModality,
    VisualizationDataMode,
)


def validate_evidence_binding(binding: Optional[EvidenceBinding]) -> Tuple[bool, str]:
    """Perform strict structural validation on an EvidenceBinding.

    Rejects:
    - None or missing binding
    - Missing claim_id
    - Unverified claim (claim_verified is not True)
    - Missing or empty source_url
    - Disallowed URL structure (missing http/https scheme or netloc)
    - Placeholder/internal URLs (e.g. verified-source.internal, localhost, example.com, etc.)
    - Fake/generic source refs (e.g. 'src_verified' or 'placeholder')
    - Missing or empty source_excerpt when excerpt_is_verbatim is True
    """
    if not binding:
        return False, "MISSING_BINDING: No EvidenceBinding attached to DOCUMENT_EVIDENCE shot."
    if not binding.claim_id:
        return False, "MISSING_CLAIM_ID: EvidenceBinding has no claim_id."
    if not binding.claim_verified:
        return False, "UNVERIFIED_CLAIM: EvidenceBinding claim is not verified by fact checker."
    if not binding.source_url or not binding.source_url.strip():
        return False, "MISSING_SOURCE_URL: EvidenceBinding has empty source_url."

    parsed = urlparse(binding.source_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, f"INVALID_URL_STRUCTURE: EvidenceBinding source_url '{binding.source_url}' is not a valid http/https URL."

    url_lower = binding.source_url.lower().strip()
    placeholder_patterns = [
        "verified-source.internal",
        "localhost",
        "127.0.0.1",
        "example.com",
        "placeholder",
        ".internal",
        "test.local",
    ]
    if any(pat in url_lower for pat in placeholder_patterns):
        return False, f"PLACEHOLDER_URL: EvidenceBinding uses disallowed placeholder URL '{binding.source_url}'."

    if not binding.source_ref or binding.source_ref.strip() in ("src_verified", "placeholder", "fake_ref"):
        return False, f"INVALID_SOURCE_REF: EvidenceBinding has ungrounded source_ref '{binding.source_ref}'."

    if binding.excerpt_is_verbatim and (not binding.source_excerpt or not binding.source_excerpt.strip()):
        return False, "EMPTY_VERBATIM_EXCERPT: EvidenceBinding marked excerpt_is_verbatim but source_excerpt is empty."

    return True, "VALID"


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

        # Grounding / Evidence strength validation
        evidence_strength = None
        if modality == VisualModality.DOCUMENT_EVIDENCE:
            binding = getattr(shot, "evidence_binding", None)
            is_valid, reason = validate_evidence_binding(binding)
            if is_valid:
                if binding.excerpt_is_verbatim and binding.source_excerpt:
                    evidence_strength = 1.0  # strong: verified real source + verbatim excerpt
                else:
                    evidence_strength = 0.8  # acceptable: verified real source + paraphrased claim
            else:
                evidence_strength = 0.0  # FAIL
                issues.append(f"Invalid evidence binding: {reason}")

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
        if duplication > 0.8 or not asset_exists or (modality == VisualModality.STATIC_CARD and shot.duration_seconds > 5.0) or (modality == VisualModality.DOCUMENT_EVIDENCE and evidence_strength == 0.0):
            recommendation = "REGENERATE"

        return VisualEvaluation(
            evaluation_mode="METADATA_HEURISTIC",
            visual_relevance=None,
            narration_duplication=duplication,
            information_value=info_value,
            motion_value=motion_value,
            continuity=None,
            evidence_strength=evidence_strength,
            readability=None,
            aesthetic_quality=None,
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
        timeline: Optional[ShotTimeline] = None,
        storyboard: Optional[Storyboard] = None,
        failed_attempts: int = 0,
        max_static_card_ratio: float = 0.15,
        director_fallback_occurred: bool = False,
        creative_fallback_reason: Optional[str] = None,
        fact_report: Optional[FactCheckReport] = None,
        dossier: Optional[ResearchDossier] = None,
        retention_cues: Optional[List[TimedRetentionCue]] = None,
    ) -> VideoQualityReport:
        """Build a comprehensive machine-readable quality report for the completed video production."""
        critical_failures: List[str] = []
        warnings: List[str] = []

        if timeline and timeline.shots:
            total_shots = len(timeline.shots)
            total_dur = (timeline.total_duration if timeline.total_duration > 0 else sum(s.duration for s in timeline.shots))
        elif storyboard and storyboard.shots:
            total_shots = len(storyboard.shots)
            total_dur = (storyboard.total_duration if storyboard.total_duration > 0 else sum(s.duration_seconds for s in storyboard.shots))
        else:
            total_shots = 0
            total_dur = 0.0

        avg_duration = round(total_dur / total_shots, 2) if total_shots > 0 else 0.0

        # Modality distribution and motion metrics
        modality_dist: Dict[str, int] = {}
        static_card_duration = 0.0
        static_semantic_duration = 0.0
        ken_burns_duration = 0.0
        true_motion_duration = 0.0

        if timeline and timeline.shots:
            for shot in timeline.shots:
                mod_name = shot.modality.value if hasattr(shot.modality, "value") else str(shot.modality)
                modality_dist[mod_name] = modality_dist.get(mod_name, 0) + 1
                is_anim = Path(shot.asset_path).suffix.lower() in [".mp4", ".mov", ".webm", ".mkv"] or getattr(shot, "is_animated", False)
                if is_anim or shot.modality in (VisualModality.GENERATED_VIDEO, VisualModality.STOCK_VIDEO):
                    true_motion_duration += shot.duration
                else:
                    ken_burns_duration += shot.duration
                    if shot.modality == VisualModality.STATIC_CARD:
                        static_card_duration += shot.duration
                    elif shot.modality in (
                        VisualModality.DIAGRAM,
                        VisualModality.DATA_VISUALIZATION,
                        VisualModality.COMPARISON,
                        VisualModality.DOCUMENT_EVIDENCE,
                        VisualModality.STATIC_DIAGRAM,
                        VisualModality.STATIC_CHART,
                        VisualModality.STATIC_TERMINAL,
                        VisualModality.MOTION_GRAPHICS,
                        VisualModality.CODE_ANIMATION,
                        VisualModality.UI_SIMULATION,
                    ):
                        static_semantic_duration += shot.duration
        elif storyboard and storyboard.shots:
            for spec in storyboard.shots:
                mod_name = spec.visual_modality.value if hasattr(spec.visual_modality, "value") else str(spec.visual_modality)
                modality_dist[mod_name] = modality_dist.get(mod_name, 0) + 1
                if spec.visual_modality in (VisualModality.GENERATED_VIDEO, VisualModality.STOCK_VIDEO):
                    true_motion_duration += spec.duration_seconds
                else:
                    ken_burns_duration += spec.duration_seconds
                    if spec.visual_modality == VisualModality.STATIC_CARD:
                        static_card_duration += spec.duration_seconds
                    elif spec.visual_modality in (
                        VisualModality.DIAGRAM,
                        VisualModality.DATA_VISUALIZATION,
                        VisualModality.COMPARISON,
                        VisualModality.DOCUMENT_EVIDENCE,
                        VisualModality.STATIC_DIAGRAM,
                        VisualModality.STATIC_CHART,
                        VisualModality.STATIC_TERMINAL,
                        VisualModality.MOTION_GRAPHICS,
                        VisualModality.CODE_ANIMATION,
                        VisualModality.UI_SIMULATION,
                    ):
                        static_semantic_duration += spec.duration_seconds

        static_card_ratio = round(static_card_duration / total_dur, 3) if total_dur > 0 else 0.0
        static_semantic_ratio = round(static_semantic_duration / total_dur, 3) if total_dur > 0 else 0.0
        ken_burns_only_ratio = round(ken_burns_duration / total_dur, 3) if total_dur > 0 else 0.0
        true_motion_ratio = round(true_motion_duration / total_dur, 3) if total_dur > 0 else 0.0

        # Dead air warnings
        dead_air_warnings = self.detect_visual_dead_air(timeline) if timeline else []

        # Narration duplication warnings
        duplication_warnings: List[str] = []
        if storyboard and storyboard.shots:
            for spec in storyboard.shots:
                dup_score = calculate_narration_duplication(spec.narration_segment, spec.headline_text)
                if dup_score >= 0.6:
                    duplication_warnings.append(
                        f"NARRATION_DUPLICATION: shot '{spec.shot_id}' score {dup_score:.2f} repeats spoken words in headline '{spec.headline_text}'"
                    )

        # 0. Director fallback check - critical creative failure in production
        if director_fallback_occurred:
            critical_failures.append(
                f"CREATIVE_PIPELINE_FALLBACK: AutoDirector fallback occurred ({creative_fallback_reason or 'pipeline fallback'}). Legacy slideshow output cannot be published to production."
            )

        # 1. Timeline continuity
        if timeline:
            continuity_issues = timeline.validate_continuity()
            if continuity_issues:
                critical_failures.extend(continuity_issues)

            # 2. Missing assets & visual acquisition QA checks
            seen_hashes: Dict[str, str] = {}
            for shot in timeline.shots:
                p = Path(shot.asset_path)
                if not p.exists() or p.stat().st_size == 0:
                    critical_failures.append(f"MISSING_ASSET: Shot '{shot.shot_id}' asset is missing or empty at {shot.asset_path}")
                    if shot.modality == VisualModality.DOCUMENT_EVIDENCE:
                        critical_failures.append(f"EVIDENCE_VISUAL_MISSING: Shot '{shot.shot_id}' evidence visual asset is missing.")
                else:
                    if (
                        shot.modality in (VisualModality.SCREENSHOT, VisualModality.SCREEN_CAPTURE, VisualModality.DOCUMENT_EVIDENCE)
                        and p.stat().st_size < 100
                        and (getattr(shot, "asset_acquisition_method", None) in ("web_capture", "local_browser") or "screenshot" in str(shot.asset_path).lower())
                    ):
                        critical_failures.append(
                            f"SOURCE_SCREENSHOT_UNREADABLE: Shot '{shot.shot_id}' capture file is too small to be readable ({p.stat().st_size} bytes)."
                        )

                # Check: REAL_REQUIRED modality used synthetic asset
                if shot.modality in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT, VisualModality.SCREEN_CAPTURE):
                    if getattr(shot, "asset_is_synthetic", False):
                        critical_failures.append(
                            f"REAL_REQUIRED_MODALITY_USED_SYNTHETIC_ASSET: Shot '{shot.shot_id}' requires real capture but received synthetic asset."
                        )
                    if shot.modality == VisualModality.DOCUMENT_EVIDENCE and not getattr(shot, "asset_source_url", None):
                        warnings.append(
                            f"MISSING_ASSET_PROVENANCE: Shot '{shot.shot_id}' evidence visual lacks source_url provenance."
                        )

                # Check duplicate asset overuse unless intentional callback
                if shot.asset_sha256:
                    prev_shot_id = seen_hashes.get(shot.asset_sha256)
                    if prev_shot_id:
                        # Check if intentional callback was requested in storyboard
                        spec = next((s for s in (storyboard.shots if storyboard else []) if s.shot_id == shot.shot_id), None)
                        if not getattr(spec, "intentional_callback", False):
                            warnings.append(
                                f"DUPLICATE_VISUAL_OVERUSE: Shot '{shot.shot_id}' reuses asset from '{prev_shot_id}' without intentional callback."
                            )
                    else:
                        seen_hashes[shot.asset_sha256] = shot.shot_id

        # 3. Grounding violations
        if storyboard and storyboard.shots:
            for spec in storyboard.shots:
                if spec.visual_modality == VisualModality.DOCUMENT_EVIDENCE:
                    binding = getattr(spec, "evidence_binding", None)
                    is_valid, reason = validate_evidence_binding(binding)
                    if not is_valid:
                        critical_failures.append(f"UNGROUNDED_EVIDENCE: Shot '{spec.shot_id}' {reason}")
                    else:
                        if fact_report:
                            matching_claim = next((c for c in (fact_report.claims or []) if c.id == binding.claim_id), None)
                            if not matching_claim:
                                critical_failures.append(
                                    f"UNGROUNDED_EVIDENCE: Shot '{spec.shot_id}' claim_id '{binding.claim_id}' does not exist in FactCheckReport."
                                )
                            else:
                                is_v = bool(matching_claim.verified or getattr(matching_claim, "verdict", None) == ClaimVerificationVerdict.VERIFIED)
                                if not is_v:
                                    critical_failures.append(
                                        f"UNGROUNDED_EVIDENCE: Shot '{spec.shot_id}' claim '{binding.claim_id}' is not VERIFIED in FactCheckReport."
                                    )
                        if dossier:
                            matching_source = next((s for s in (dossier.sources or []) if s.id == binding.source_ref), None)
                            if not matching_source:
                                critical_failures.append(
                                    f"UNGROUNDED_EVIDENCE: Shot '{spec.shot_id}' source_ref '{binding.source_ref}' does not resolve to ResearchDossier sources."
                                )
                            else:
                                expected_url = matching_source.url or getattr(matching_source, "final_url", None)
                                if expected_url and binding.source_url != expected_url:
                                    critical_failures.append(
                                        f"UNGROUNDED_EVIDENCE: Shot '{spec.shot_id}' source_url '{binding.source_url}' does not match resolved ResearchSource URL '{expected_url}'."
                                    )
                elif spec.visual_modality == VisualModality.DATA_VISUALIZATION:
                    data_mode = getattr(spec, "visual_data_mode", VisualizationDataMode.GROUNDED)
                    if data_mode == VisualizationDataMode.GROUNDED:
                        chart_data = getattr(spec, "chart_data", None)
                        if not chart_data:
                            critical_failures.append(f"FABRICATED_DATA: Shot '{spec.shot_id}' has grounded DATA_VISUALIZATION without verified ChartDatum points.")
                        else:
                            for cd in chart_data:
                                if getattr(cd, "origin", None) == ChartDatumOrigin.CONCEPTUAL:
                                    critical_failures.append(f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' has CONCEPTUAL origin in a GROUNDED chart.")
                                elif getattr(cd, "origin", None) == ChartDatumOrigin.EXTERNAL_SOURCE:
                                    if not getattr(cd, "source_ref", None):
                                        critical_failures.append(f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' has EXTERNAL_SOURCE origin but missing source_ref.")
                                    elif dossier:
                                        matching_source = next((s for s in (dossier.sources or []) if s.id == cd.source_ref), None)
                                        if not matching_source:
                                            critical_failures.append(
                                                f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' source_ref '{cd.source_ref}' does not resolve to ResearchDossier sources."
                                            )
                                elif getattr(cd, "origin", None) == ChartDatumOrigin.VERIFIED_CLAIM:
                                    if not getattr(cd, "claim_id", None):
                                        critical_failures.append(f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' has VERIFIED_CLAIM origin but missing claim_id.")
                                    elif fact_report:
                                        matching_claim = next((c for c in (fact_report.claims or []) if c.id == cd.claim_id), None)
                                        if not matching_claim:
                                            critical_failures.append(
                                                f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' claim_id '{cd.claim_id}' does not exist in FactCheckReport."
                                            )
                                        else:
                                            is_v = bool(matching_claim.verified or getattr(matching_claim, "verdict", None) == ClaimVerificationVerdict.VERIFIED)
                                            if not is_v:
                                                critical_failures.append(
                                                    f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' claim '{cd.claim_id}' is not VERIFIED in FactCheckReport."
                                                )
                                elif not getattr(cd, "source_ref", None) and not getattr(cd, "claim_id", None):
                                    critical_failures.append(f"UNGROUNDED_CHART_DATA: Shot '{spec.shot_id}' datum '{cd.label}' lacks both claim_id and source_ref in a GROUNDED chart.")
                    elif data_mode == VisualizationDataMode.CONCEPTUAL:
                        # Conceptual data visualizations must not display ungrounded empirical numeric data points
                        if getattr(spec, "chart_data", None):
                            for cd in spec.chart_data:
                                if getattr(cd, "value", None) is not None and getattr(cd, "source_ref", None) is None and getattr(cd, "claim_id", None) is None:
                                    warnings.append(f"CONCEPTUAL_NUMERIC_LABEL: Shot '{spec.shot_id}' is marked conceptual but contains ungrounded numeric data points.")

            # 4. Severe duplication (> 0.85)
            for spec in storyboard.shots:
                dup_score = calculate_narration_duplication(spec.narration_segment, spec.headline_text)
                if dup_score >= 0.85:
                    critical_failures.append(f"EXCESSIVE_DUPLICATION: Shot '{spec.shot_id}' duplication score {dup_score:.2f} strictly repeats spoken words.")

        # 5. Static card threshold
        if static_card_ratio > max_static_card_ratio:
            dead_air_warnings.append(
                f"WARNING: {static_card_ratio * 100:.1f}% of visual runtime is static-card based. Target for this format is <= {max_static_card_ratio * 100:.0f}%."
            )

        if static_card_ratio > 0.50:
            critical_failures.append(f"EXCESSIVE_STATIC_RATIO: {static_card_ratio * 100:.1f}% of runtime is static cards (max allowable 50%).")
        elif static_card_ratio > max_static_card_ratio:
            warnings.append(f"STATIC_CARD_OVERUSE: {static_card_ratio * 100:.1f}% of runtime is static cards (target <= {max_static_card_ratio * 100:.0f}%).")
            warnings.append(f"HIGH_STATIC_RATIO: {static_card_ratio * 100:.1f}% of runtime is static cards (target <= {max_static_card_ratio * 100:.0f}%).")

        # 6. Retention-Aware Visual QA Checks (Phase 12)
        if retention_cues and storyboard and storyboard.shots:
            shot_windows = []
            cur_s_time = 0.0
            for idx, spec in enumerate(storyboard.shots):
                s_dur = spec.duration_seconds
                s_start = cur_s_time
                s_end = s_start + s_dur
                cur_s_time = s_end
                shot_windows.append((idx, spec, s_start, s_end))

            for cue in retention_cues:
                matched_shot = None
                matched_idx = -1
                for idx, spec, s_start, s_end in shot_windows:
                    if (s_start <= cue.timestamp_seconds < s_end) or (idx == len(shot_windows) - 1 and s_start <= cue.timestamp_seconds <= s_end):
                        matched_shot = spec
                        matched_idx = idx
                        break

                if not matched_shot:
                    warnings.append(
                        f"RETENTION_CUE_MISSED: Retention cue '{cue.cue_id}' ({cue.cue_type.value}) at {cue.timestamp_seconds:.2f}s falls outside storyboard duration."
                    )
                    continue

                if cue.cue_type == RetentionCueType.PATTERN_INTERRUPT:
                    if matched_idx > 0:
                        prev_shot = storyboard.shots[matched_idx - 1]
                        if matched_shot.visual_modality == prev_shot.visual_modality:
                            warnings.append(
                                f"PATTERN_INTERRUPT_NO_MODALITY_CHANGE: Pattern interrupt shot '{matched_shot.shot_id}' at {cue.timestamp_seconds:.2f}s failed to change modality from preceding '{prev_shot.visual_modality.value}'."
                            )

                elif cue.cue_type == RetentionCueType.REHOOK:
                    has_novelty = bool(matched_shot.camera_motion or matched_shot.composition or matched_shot.importance >= 0.8)
                    if not has_novelty:
                        warnings.append(
                            f"REHOOK_WITH_NO_VISUAL_NOVELTY: Rehook shot '{matched_shot.shot_id}' at {cue.timestamp_seconds:.2f}s lacks visual framing novelty or priority."
                        )

                elif cue.cue_type in (RetentionCueType.CLIMAX, RetentionCueType.REVEAL):
                    if matched_shot.visual_modality == VisualModality.STATIC_CARD or (matched_idx > 0 and matched_shot.importance < storyboard.shots[0].importance and matched_shot.visual_modality in (VisualModality.STATIC_CARD, VisualModality.STATIC_DIAGRAM)):
                        warnings.append(
                            f"CLIMAX_VISUALLY_WEAKER_THAN_SETUP: Climax/reveal shot '{matched_shot.shot_id}' at {cue.timestamp_seconds:.2f}s has weak visual modality or priority."
                        )

        warnings.extend(dead_air_warnings)
        warnings.extend(duplication_warnings)

        if critical_failures:
            creative_status = "FAIL"
        elif warnings:
            creative_status = "PASS_WITH_WARNINGS"
        else:
            creative_status = "PASS"


        # Compute overall visual score
        score = 1.0
        score -= min(0.3, static_card_ratio * 0.5)
        score -= min(0.2, len(dead_air_warnings) * 0.05)
        score -= min(0.2, len(duplication_warnings) * 0.05)
        score -= min(0.15, failed_attempts * 0.03)
        if critical_failures:
            score -= 0.4
        overall_score = max(0.1, round(score, 2))

        return VideoQualityReport(
            total_shots=total_shots,
            average_shot_duration=avg_duration,
            static_card_ratio=static_card_ratio,
            static_semantic_ratio=static_semantic_ratio,
            ken_burns_only_ratio=ken_burns_only_ratio,
            true_motion_ratio=true_motion_ratio,
            modality_distribution=modality_dist,
            visual_dead_air_warnings=dead_air_warnings,
            narration_duplication_warnings=duplication_warnings,
            failed_asset_attempts=failed_attempts,
            overall_visual_score=overall_score,
            creative_status=creative_status,
            critical_failures=critical_failures,
            warnings=warnings,
        )

    def evaluate(self, storyboard: Storyboard, profile: Optional[ChannelCreativeProfile] = None) -> VideoQualityReport:
        """Evaluate a storyboard against creative quality standards and profile policies."""
        prof = profile or self.profile
        max_ratio = getattr(prof, "max_static_card_ratio", 0.15) if prof else 0.15
        return self.generate_quality_report(storyboard=storyboard, max_static_card_ratio=max_ratio)


# Canonical alias for quality evaluation engine
QualityEvaluator = VisualShotEvaluator
