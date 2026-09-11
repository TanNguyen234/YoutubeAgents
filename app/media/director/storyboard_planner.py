"""Storyboard Planner transforming narrative beats into concrete, multi-modal shot specifications."""

import json
from pathlib import Path
import re
from typing import Dict, List, Optional

from app.domain.models import Script
from app.media.director.modality_router import VisualModalityRouter
from app.media.director.models import (
    ChannelCreativeProfile,
    ContentFormat,
    NarrativeBeat,
    ShotSpec,
    Storyboard,
    VisualIntent,
    VisualModality,
)


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
            # Check for numbers or probability in narration
            num_match = re.findall(r"\b\d+(?:\.\d+)?%?\b", narration)
            numbers_str = ", ".join(num_match) if num_match else "82%, 12%, 6%"
            instructions["chart_instruction"] = (
                f"Data visualization: Bar chart or probability distribution showing {entities_str}. "
                f"Data points: [{numbers_str}]. Rising animated bars with highlighted leader."
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
            instructions["evidence_instruction"] = (
                f"Verified evidence callout: Benchmark paper or official docs snapshot for {topic_title}. "
                f"Quoted assertion: '{beat.key_claim or narration}'. Highlighted verification box with source badge."
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

    def plan_storyboard(
        self,
        project_id: str,
        script: Script,
        beats: List[NarrativeBeat],
        total_audio_duration: float,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        available_modalities: Optional[List[VisualModality]] = None,
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

        for b_idx, beat in enumerate(beats):
            selected_mod = self.router.route_modality(
                beat=beat,
                content_format=content_format,
                available_modalities=available_modalities,
            )
            modality_counts[selected_mod.value] = modality_counts.get(selected_mod.value, 0) + 1

            dur = beat.duration_hint or 2.5
            instructions = self._build_modality_instructions(beat, selected_mod, script.title)
            punchline = self._extract_punchline(beat.narration)

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
                camera_motion="dynamic push-in" if beat.purpose.value == "HOOK" else "slow parallax push",
                headline_text=punchline,
                continuity_refs=[f"shot_{b_idx:02d}"] if b_idx > 0 else [],
                source_refs=beat.source_refs,
                importance=beat.importance,
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
            beats=beats,
            shots=shots,
            modality_counts=modality_counts,
        )

    def save_storyboard_artifact(self, storyboard: Storyboard, output_path: Path) -> Path:
        """Persist storyboard as a structured JSON artifact."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(storyboard.model_dump_json(indent=2), encoding="utf-8")
        return output_path
