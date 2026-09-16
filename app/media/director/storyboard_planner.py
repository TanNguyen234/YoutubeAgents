"""Storyboard Planner transforming narrative beats into concrete, multi-modal shot specifications."""

import json
from pathlib import Path
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from app.domain.enums import ClaimVerificationVerdict, RetentionCueType
from app.domain.models import Claim, FactCheckReport, ResearchDossier, ResearchSource, Script, TimedRetentionCue
from app.media.director.modality_router import VisualModalityRouter
from app.media.director.models import (
    ChannelCreativeProfile,
    ChartDatum,
    ChartDatumOrigin,
    ComparisonColumn,
    ContentFormat,
    EvidenceBinding,
    NarrativeBeat,
    ShotSpec,
    Storyboard,
    VisualIntent,
    VisualModality,
    VisualizationDataMode,
)
from app.media.director.quality_evaluator import validate_evidence_binding


class StoryboardPlanner:
    """Plans detailed shot specifications, modality assignments, and storyboard artifacts."""

    def __init__(
        self,
        router: Optional[VisualModalityRouter] = None,
        profile: Optional[ChannelCreativeProfile] = None,
    ):
        self.profile = profile or ChannelCreativeProfile()
        self.router = router or VisualModalityRouter(profile=self.profile)

    def _extract_punchline(self, text: str) -> str:
        """Extract a short 2-5 word keyword punchline for graphics without repeating full narration."""
        # Find key phrases or numbers
        number_match = re.search(r"(\+?\d+(?:\.\d+)?%?|\$\d+(?:\.\d+)?[BMK]?)", text)
        words = text.split()
        if number_match and len(words) > 3:
            num = number_match.group(1)
            # Pair with nearby noun
            return f"KEY METRIC // {num}"
        if len(words) <= 4:
            return text.upper()
        # Extract first key noun phrase or punchy 3 words
        return " // ".join(w.upper() for w in words[:3])

    def _build_modality_instructions(
        self,
        beat: NarrativeBeat,
        modality: VisualModality,
        topic_title: str,
    ) -> Dict[str, Optional[str]]:
        """Generate tailored, modality-specific synthesis instructions."""
        instructions = {
            "screen_instruction": None,
            "diagram_instruction": None,
            "code_instruction": None,
            "chart_instruction": None,
            "evidence_instruction": None,
            "motion_graphic_instruction": None,
            "generation_prompt": None,
        }

        narration = beat.narration
        entities_str = ", ".join(beat.key_entities) if beat.key_entities else topic_title

        if modality == VisualModality.DIAGRAM:
            instructions["diagram_instruction"] = (
                f"Architecture / Flow diagram: Show relationship and process flow for {entities_str}. "
                f"Core mechanism: {beat.key_claim or narration}. Clean dark theme, high-contrast connected nodes."
            )
        elif modality == VisualModality.DATA_VISUALIZATION:
            if beat.chart_data:
                chart_points_str = ", ".join(f"{d.label}: {d.value}{d.unit or ''}" for d in beat.chart_data)
                instructions["chart_instruction"] = (
                    f"Data visualization: Bar chart showing {entities_str}. "
                    f"Grounded data: [{chart_points_str}]."
                )
            else:
                num_match = re.findall(r"\b\d+(?:\.\d+)?%?\b", narration)
                if num_match:
                    numbers_str = ", ".join(num_match)
                    instructions["chart_instruction"] = (
                        f"Data visualization: Bar chart or probability distribution showing {entities_str}. "
                        f"Data points: [{numbers_str}]."
                    )
                else:
                    instructions["chart_instruction"] = (
                        f"Next-token distribution: Conceptual probability bars for {entities_str}. "
                        f"No numeric labels."
                    )
        elif modality == VisualModality.CODE_ANIMATION:
            instructions["code_instruction"] = (
                f"Syntax-highlighted terminal or code editor: Demonstrate {entities_str}. "
                f"Command or snippet executing: {narration[:80]}. Clean monospace typography."
            )
        elif modality == VisualModality.UI_SIMULATION:
            instructions["screen_instruction"] = (
                f"User interface mockup: User interaction simulating {entities_str}. "
                f"Action: {narration[:100]}. Real UI controls, input field, dropdown or response state."
            )
        elif modality == VisualModality.DOCUMENT_EVIDENCE:
            claim_text = beat.key_claim or beat.narration
            instructions["evidence_instruction"] = (
                f"Verified evidence callout: Benchmark paper or official docs snapshot for {topic_title}. "
                f"Assertion: {claim_text}. Highlighted verification box with source badge."
            )
        elif modality == VisualModality.COMPARISON:
            instructions["motion_graphic_instruction"] = (
                f"Split-screen comparison: Left vs Right architectural contrast for {entities_str}. "
                f"Contrasting: {narration}. Clear badges, pros/cons or speed delta."
            )
        elif modality in (VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE):
            # Adhere strictly to anti-slop rules: no glowing brains, no generic neon
            clean_subject = (entities_str or topic_title).strip()
            instructions["generation_prompt"] = (
                f"Editorial tech visual, photorealistic, documentary style: {clean_subject} in real-world application. "
                f"Physical context: {beat.visual_intent.value.lower()}. "
                f"Clean cinematic lighting, 9:16 vertical framing, natural textures, strictly no floating text, no generic cyberpunk, no robot tropes."
            )
        else:
            instructions["motion_graphic_instruction"] = (
                f"Clean motion graphics: Visualizing {entities_str}. Intent: {beat.visual_intent.value}. Pacing: fast."
            )

        return instructions

    def _resolve_evidence_binding(
        self,
        beat: NarrativeBeat,
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
    ) -> Optional[EvidenceBinding]:
        """Resolve evidence binding from verified fact-check claims and research dossier sources.

        Never treats incoming beat.evidence_binding as authoritative. Factual provenance
        must be grounded against verified claims and real ResearchSource objects in dossier.
        """
        claims: List[Claim] = []
        if fact_report and fact_report.claims:
            claims.extend(
                c for c in fact_report.claims
                if bool(c.verified or getattr(c, "verdict", None) == ClaimVerificationVerdict.VERIFIED)
            )
        if dossier and dossier.claims:
            for c in dossier.claims:
                is_v = bool(c.verified or getattr(c, "verdict", None) == ClaimVerificationVerdict.VERIFIED)
                if is_v and not any(ec.id == c.id for ec in claims):
                    claims.append(c)

        if not claims:
            return None

        target_text = (beat.key_claim or beat.narration or "").lower()
        target_tokens = set(re.findall(r"\w+", target_text))

        best_claim: Optional[Claim] = None
        best_overlap = 0.0

        for claim in claims:
            stmt = claim.statement.lower()
            stmt_tokens = set(re.findall(r"\w+", stmt))
            if not stmt_tokens:
                continue
            overlap = len(target_tokens & stmt_tokens) / len(stmt_tokens)
            if (stmt in target_text or overlap >= 0.35) and overlap > best_overlap:
                best_claim = claim
                best_overlap = overlap

        if not best_claim:
            return None

        sources = dossier.sources if dossier else []
        matched_source: Optional[ResearchSource] = None
        if best_claim.source_id and sources:
            matched_source = next((s for s in sources if s.id == best_claim.source_id), None)
        if not matched_source and best_claim.cited_url and sources:
            matched_source = next((s for s in sources if s.url == best_claim.cited_url or getattr(s, "final_url", None) == best_claim.cited_url), None)

        if not matched_source:
            # Source resolution must be real: cannot resolve to actual ResearchSource in dossier
            return None

        source_url = matched_source.url or getattr(matched_source, "final_url", None)
        if not source_url or not source_url.strip():
            return None

        parsed = urlparse(source_url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None

        url_lower = source_url.lower().strip()
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
            return None

        source_ref = matched_source.id
        if not source_ref or source_ref.strip() in ("src_verified", "placeholder", "fake_ref"):
            return None

        source_title = matched_source.title or (best_claim.cited_url or "Source Reference")
        source_excerpt = best_claim.cited_excerpt
        excerpt_is_verbatim = bool(source_excerpt and source_excerpt.strip())

        binding = EvidenceBinding(
            claim_id=best_claim.id,
            source_ref=source_ref,
            source_title=source_title,
            source_url=source_url,
            claim_text=best_claim.statement,
            source_excerpt=source_excerpt,
            excerpt_is_verbatim=excerpt_is_verbatim,
            claim_verified=True,
            quote_or_excerpt=source_excerpt or best_claim.statement,
        )
        is_valid, _ = validate_evidence_binding(binding)
        return binding if is_valid else None


    def plan_storyboard(
        self,
        project_id: str,
        script: Script,
        beats: List[NarrativeBeat],
        total_audio_duration: float,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        available_modalities: Optional[List[VisualModality]] = None,
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
        retention_cues: Optional[List[TimedRetentionCue]] = None,
    ) -> Storyboard:
        """Construct an authoritative Storyboard mapping all beats to granular ShotSpecs."""
        shots: List[ShotSpec] = []
        modality_counts: Dict[str, int] = {}

        if not beats:
            return Storyboard(
                project_id=project_id,
                total_duration=total_audio_duration,
                content_format=content_format,
                beats=[],
                shots=[],
                modality_counts={},
            )

        cur_beat_time = 0.0
        for b_idx, beat in enumerate(beats):
            beat_dur = beat.duration_hint or 2.5
            beat_start = beat.start_hint if beat.start_hint is not None else cur_beat_time
            beat_end = beat_start + beat_dur
            cur_beat_time = beat_end

            # Match retention cues for this beat window
            matching_cues = []
            if retention_cues:
                for rc in retention_cues:
                    if (beat_start <= rc.timestamp_seconds < beat_end) or (b_idx == len(beats) - 1 and rc.timestamp_seconds >= beat_start):
                        matching_cues.append(rc)
                    elif rc.narration_anchor and rc.narration_anchor.lower() in beat.narration.lower():
                        if rc not in matching_cues:
                            matching_cues.append(rc)

            selected_mod = self.router.route_modality(
                beat=beat,
                content_format=content_format,
                available_modalities=available_modalities,
            )

            # Apply Retention Cue influence before final grounding validation
            composition_override: Optional[str] = None
            camera_motion_override: Optional[str] = None

            for cue in matching_cues:
                if cue.cue_type == RetentionCueType.PATTERN_INTERRUPT:
                    prev_mod = shots[-1].visual_modality if shots else None
                    if prev_mod:
                        # Semantic modality shift
                        if prev_mod == VisualModality.DIAGRAM:
                            selected_mod = VisualModality.CODE_ANIMATION
                            beat.visual_intent = VisualIntent.SHOW_CODE
                        elif prev_mod in (VisualModality.MOTION_GRAPHICS, VisualModality.KINETIC_TYPOGRAPHY):
                            binding = self._resolve_evidence_binding(beat, dossier, fact_report)
                            if binding:
                                selected_mod = VisualModality.DOCUMENT_EVIDENCE
                                beat.evidence_binding = binding
                                beat.visual_intent = VisualIntent.SHOW_EVIDENCE
                            else:
                                selected_mod = VisualModality.UI_SIMULATION
                                beat.visual_intent = VisualIntent.SHOW_INTERFACE
                        elif prev_mod in (VisualModality.GENERATED_IMAGE, VisualModality.GENERATED_VIDEO):
                            selected_mod = VisualModality.UI_SIMULATION
                            beat.visual_intent = VisualIntent.SHOW_INTERFACE
                        elif prev_mod == VisualModality.CODE_ANIMATION:
                            selected_mod = VisualModality.DIAGRAM
                            beat.visual_intent = VisualIntent.SHOW_MECHANISM
                        elif prev_mod == selected_mod:
                            for alt in (VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION, VisualModality.DIAGRAM, VisualModality.MOTION_GRAPHICS):
                                if alt != prev_mod:
                                    selected_mod = alt
                                    break
                    beat.importance = max(beat.importance, 0.85)
                    camera_motion_override = "PATTERN_INTERRUPT_SNAP"

                elif cue.cue_type == RetentionCueType.REHOOK:
                    composition_override = "DYNAMIC_CLOSE_UP"
                    camera_motion_override = "RAPID_PUSH_IN"
                    beat.importance = max(beat.importance, 0.85)
                    if any(k in beat.narration.lower() for k in ("see", "look", "watch", "result", "bench")):
                        beat.visual_intent = VisualIntent.SHOW_RESULT

                elif cue.cue_type in (RetentionCueType.REVEAL, RetentionCueType.CLIMAX):
                    beat.importance = 1.0
                    camera_motion_override = "HERO_DOLLY_IN"
                    composition_override = "HERO_CENTERED"
                    if selected_mod == VisualModality.STATIC_CARD:
                        selected_mod = VisualModality.DIAGRAM
                    beat.visual_intent = VisualIntent.SHOW_RESULT

                elif cue.cue_type == RetentionCueType.LOOP_CLOSE:
                    composition_override = "RESOLVING_WIDE"
                    beat.visual_intent = VisualIntent.SHOW_RESULT

            # Grounding enforcement & reroute checks:
            # 1. DATA_VISUALIZATION requires either explicit chart_data, extractable numbers, or conceptual token context
            has_numbers = bool(re.search(r"\d+(?:\.\d+)?", beat.narration)) or bool(beat.chart_data)
            is_token_concept = any(k in beat.narration.lower() for k in ("token", "next token", "probability"))
            if selected_mod == VisualModality.DATA_VISUALIZATION and not (has_numbers or is_token_concept):
                selected_mod = VisualModality.DIAGRAM

            # 2. DOCUMENT_EVIDENCE requires valid source binding or verifiable source_refs
            if selected_mod == VisualModality.DOCUMENT_EVIDENCE:
                resolved_binding = self._resolve_evidence_binding(beat, dossier, fact_report)
                if resolved_binding:
                    beat.evidence_binding = resolved_binding
                else:
                    beat.evidence_binding = None
                    selected_mod = VisualModality.DIAGRAM

            # 3. COMPARISON requires structured comparison points or extractable entities
            comparison_left: Optional[ComparisonColumn] = None
            comparison_right: Optional[ComparisonColumn] = None
            if selected_mod == VisualModality.COMPARISON:
                if len(beat.key_entities) >= 2:
                    comparison_left = ComparisonColumn(
                        label=beat.key_entities[0],
                        points=[f"{beat.key_entities[0]} approach", beat.narration[:40]],
                    )
                    comparison_right = ComparisonColumn(
                        label=beat.key_entities[1],
                        points=[f"{beat.key_entities[1]} approach", beat.key_claim[:40] if beat.key_claim else "Alternative"],
                    )
                elif " vs " in beat.narration.lower() or " versus " in beat.narration.lower():
                    parts = re.split(r"\s+vs\.?\s+|\s+versus\s+", beat.narration, flags=re.IGNORECASE)
                    if len(parts) >= 2:
                        comparison_left = ComparisonColumn(label=parts[0].strip()[:24], points=[parts[0].strip()[:35]])
                        comparison_right = ComparisonColumn(label=parts[1].strip()[:24], points=[parts[1].strip()[:35]])

                if not comparison_left or not comparison_right:
                    # Incomplete comparison data: reroute to DIAGRAM
                    selected_mod = VisualModality.DIAGRAM

            modality_counts[selected_mod.value] = modality_counts.get(selected_mod.value, 0) + 1

            dur = beat.duration_hint or 2.5
            instructions = self._build_modality_instructions(beat, selected_mod, script.title)
            punchline = self._extract_punchline(beat.narration)

            # Build grounded chart data if available
            shot_chart_data = beat.chart_data
            if not shot_chart_data and has_numbers and not is_token_concept:
                metric_pattern = r"\b(\d+(?:\.\d+)?)\s*(%|percent|ms|s|seconds|gb|mb|tb|k|m|b|\$|x faster|times faster)\b"
                metric_matches = re.findall(metric_pattern, beat.narration, flags=re.IGNORECASE)
                if metric_matches:
                    all_claims = []
                    if fact_report and fact_report.claims:
                        all_claims.extend(fact_report.claims)
                    if dossier and dossier.claims:
                        all_claims.extend([c for c in dossier.claims if c not in all_claims])

                    grounded_datums: List[ChartDatum] = []
                    for i, (num_val, unit_val) in enumerate(metric_matches[:4]):
                        cleaned_unit = unit_val.strip()
                        # Detect semantic label context from surrounding text or key entities
                        context_label = f"Metric ({cleaned_unit})"
                        if beat.key_entities and i < len(beat.key_entities):
                            context_label = f"{beat.key_entities[i]} ({cleaned_unit})"
                        else:
                            escaped_num = re.escape(num_val)
                            ctx_m = re.search(r"(\b[A-Za-z0-9_-]+\b)\s+(?:was|is|at|reached|achieved|by)?\s*" + escaped_num, beat.narration, flags=re.IGNORECASE)
                            if ctx_m:
                                context_label = f"{ctx_m.group(1).title()} ({cleaned_unit})"
                            else:
                                context_label = f"Measurement {i+1} ({cleaned_unit})"

                        # Grounding verification: Only chart if number matches a verified claim or external source
                        matched_claim = None
                        for clm in all_claims:
                            is_clm_verified = bool(clm.verified or getattr(clm, "verdict", None) == ClaimVerificationVerdict.VERIFIED)
                            if is_clm_verified and (num_val in clm.statement or (clm.cited_excerpt and num_val in clm.cited_excerpt)):
                                matched_claim = clm
                                break

                        if matched_claim:
                            resolved_src_ref = matched_claim.source_id or (matched_claim.cited_url if matched_claim.cited_url else None)
                            if dossier and dossier.sources:
                                real_s = None
                                if matched_claim.source_id:
                                    real_s = next((s for s in dossier.sources if s.id == matched_claim.source_id), None)
                                if not real_s and matched_claim.cited_url:
                                    real_s = next((s for s in dossier.sources if s.url == matched_claim.cited_url or getattr(s, "final_url", None) == matched_claim.cited_url), None)
                                if real_s:
                                    resolved_src_ref = real_s.id
                            grounded_datums.append(
                                ChartDatum(
                                    label=context_label,
                                    value=float(num_val),
                                    unit=cleaned_unit,
                                    origin=ChartDatumOrigin.VERIFIED_CLAIM,
                                    claim_id=matched_claim.id,
                                    source_ref=resolved_src_ref,
                                )
                            )
                        elif beat.source_refs:
                            matched_src = None
                            if dossier and dossier.sources:
                                matched_src = next((s for s in dossier.sources if s.id == beat.source_refs[0]), None)
                            elif not dossier:
                                if not any(p in beat.source_refs[0].lower() for p in ["placeholder", "fake_ref"]):
                                    matched_src = type("StubSource", (), {"id": beat.source_refs[0]})()
                            if matched_src:
                                grounded_datums.append(
                                    ChartDatum(
                                        label=context_label,
                                        value=float(num_val),
                                        unit=cleaned_unit,
                                        origin=ChartDatumOrigin.EXTERNAL_SOURCE,
                                        source_ref=matched_src.id,
                                    )
                                )

                    if grounded_datums:
                        shot_chart_data = grounded_datums
                    elif selected_mod == VisualModality.DATA_VISUALIZATION:
                        selected_mod = VisualModality.DIAGRAM
                elif selected_mod == VisualModality.DATA_VISUALIZATION:
                    selected_mod = VisualModality.DIAGRAM


            shot = ShotSpec(
                shot_id=f"shot_{b_idx + 1:02d}",
                beat_id=beat.beat_id,
                scene_index=beat.scene_index,
                narration_segment=beat.narration,
                purpose=beat.purpose.value.lower(),
                duration_seconds=round(dur, 3),
                subject=", ".join(beat.key_entities) if beat.key_entities else script.title,
                action=beat.visual_intent.value.lower().replace("_", " "),
                visual_modality=selected_mod,
                visual_intent=beat.visual_intent,
                camera_motion=camera_motion_override or ("dynamic push-in" if beat.purpose.value == "HOOK" else "slow parallax push"),
                composition=composition_override,
                headline_text=punchline,
                continuity_refs=[f"shot_{b_idx:02d}"] if b_idx > 0 else [],
                source_refs=beat.source_refs,
                importance=beat.importance,
                chart_data=shot_chart_data,
                comparison_left=comparison_left,
                comparison_right=comparison_right,
                evidence_binding=beat.evidence_binding,
                **instructions,
            )
            shots.append(shot)

        # Scale shot durations so sum matches total_audio_duration exactly
        total_shot_dur = sum(s.duration_seconds for s in shots)
        if total_shot_dur > 0 and abs(total_shot_dur - total_audio_duration) > 0.05:
            scale = total_audio_duration / total_shot_dur
            for s in shots:
                s.duration_seconds = round(s.duration_seconds * scale, 3)

        return Storyboard(
            project_id=project_id,
            total_duration=total_audio_duration,
            content_format=content_format,
            profile_name=self.profile.name if self.profile else "Editorial Tech Shorts",
            beats=beats,
            shots=shots,
            modality_counts=modality_counts,
        )

    def save_storyboard_artifact(self, storyboard: Storyboard, output_path: Path) -> Path:
        """Persist storyboard as a structured JSON artifact."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")
        return output_path
