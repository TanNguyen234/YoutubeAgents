"""Beat Decomposer service transforming continuous script scenes into granular narrative beats."""

import math
import re
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.backend import ReasoningBackend
from app.domain.models import Scene, Script
from app.media.director.models import (
    BeatPurpose,
    ContentFormat,
    NarrativeBeat,
    VisualIntent,
    VisualModality,
)


class DecomposedBeatsPayload(BaseModel):
    """Structured LLM payload for decomposed narrative beats."""
    beats: List[NarrativeBeat] = Field(default_factory=list)


class BeatDecomposer:
    """Decomposes script scenes into fine-grained narrative beats for dynamic visual storytelling."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend

    def decompose_scene_deterministic(
        self,
        scene: Scene,
        scene_index: int,
        total_scenes: int,
        scene_duration: float,
        topic_title: str = "",
    ) -> List[NarrativeBeat]:
        """Deterministic semantic beat decomposition breaking long scene narration into 2-5 fast-paced beats."""
        raw_text = (scene.narration or "").strip()
        if not raw_text:
            return [
                NarrativeBeat(
                    beat_id=f"b_{scene_index:02d}_01",
                    scene_index=scene_index,
                    narration="Technical explanation.",
                    duration_hint=max(1.5, scene_duration),
                    purpose=BeatPurpose.HOOK if scene_index == 0 else BeatPurpose.EXPLAIN,
                    visual_intent=VisualIntent.SHOW_MECHANISM,
                )
            ]

        # Split text into logical semantic clauses
        # Split on sentence boundaries and subordinating conjunctions / punctuation
        parts = re.split(r"(?<=[.!?])\s+|(?<=[,;])\s+(?=(?:because|while|whereas|instead|however|by|which|as|with|so|thus)\b)|\s*—\s*|\s* - \s*", raw_text, flags=re.IGNORECASE)
        clauses = [p.strip() for p in parts if p.strip()]

        # If splitting produced too few clauses for a long scene (> 5s), further split by word chunks
        if len(clauses) <= 1 and len(raw_text.split()) > 10:
            words = raw_text.split()
            chunk_size = max(6, len(words) // max(2, min(4, int(scene_duration // 2.5))))
            clauses = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]

        total_words = sum(max(1, len(c.split())) for c in clauses)
        beats: List[NarrativeBeat] = []

        cur_time = 0.0
        for b_idx, clause in enumerate(clauses):
            clause_words = max(1, len(clause.split()))
            beat_dur = max(1.2, round(scene_duration * (clause_words / total_words), 2))
            beat_id = f"b_{scene_index:02d}_{b_idx + 1:02d}"

            # Purpose inference
            if scene_index == 0 and b_idx == 0:
                purpose = BeatPurpose.HOOK
            elif scene_index == total_scenes - 1 and b_idx == len(clauses) - 1:
                purpose = BeatPurpose.CTA if any(w in clause.lower() for w in ["subscribe", "next", "follow", "comment", "like"]) else BeatPurpose.PAYOFF
            elif any(w in clause.lower() for w in ["vs", "compare", "contrast", "difference", "unlike", "whereas"]):
                purpose = BeatPurpose.COMPARE
            elif any(w in clause.lower() for w in ["percent", "%", "benchmark", "times faster", "improvement", "numbers", "stats", "million", "billion"]):
                purpose = BeatPurpose.PROVE
            elif any(w in clause.lower() for w in ["example", "demo", "type", "run", "code", "terminal", "command", "user selects"]):
                purpose = BeatPurpose.DEMONSTRATE
            else:
                purpose = BeatPurpose.EXPLAIN

            # Visual intent inference
            lowered = clause.lower()
            if any(w in lowered for w in ["predict", "probability", "token", "chart", "%", "distribution", "score", "revenue", "grow", "exploded"]):
                intent = VisualIntent.SHOW_DATA
            elif any(w in lowered for w in ["terminal", "code", "syntax", "git", "command", "query", "sql", "function", "api"]):
                intent = VisualIntent.SHOW_CODE
            elif any(w in lowered for w in ["interface", "screen", "button", "user types", "click", "input", "dropdown"]):
                intent = VisualIntent.SHOW_INTERFACE
            elif any(w in lowered for w in ["architecture", "kernel", "container", "layer", "flow", "lock", "wal", "process", "memory", "mechanism"]):
                intent = VisualIntent.SHOW_MECHANISM
            elif any(w in lowered for w in ["difference", "vs", "compare", "unlike", "better than", "overhead"]):
                intent = VisualIntent.SHOW_DIFFERENCE
            elif any(w in lowered for w in ["benchmark", "paper", "proved", "measured", "result", "improved by"]):
                intent = VisualIntent.SHOW_EVIDENCE
            elif any(w in lowered for w in ["server", "datacenter", "hardware", "gpu", "rack", "fiber", "world", "company"]):
                intent = VisualIntent.ESTABLISH_CONTEXT
            else:
                intent = VisualIntent.SHOW_PROCESS

            # Preferred modalities matching intent
            requires_evidence = intent in (VisualIntent.SHOW_EVIDENCE, VisualIntent.SHOW_DATA) or "%" in clause or bool(re.search(r"\b\d+(\.\d+)?\b", clause))
            preferred_mods = []
            if intent == VisualIntent.SHOW_DATA:
                preferred_mods = [VisualModality.DATA_VISUALIZATION, VisualModality.MOTION_GRAPHICS]
            elif intent == VisualIntent.SHOW_CODE:
                preferred_mods = [VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION]
            elif intent == VisualIntent.SHOW_INTERFACE:
                preferred_mods = [VisualModality.UI_SIMULATION, VisualModality.SCREENSHOT]
            elif intent == VisualIntent.SHOW_MECHANISM:
                preferred_mods = [VisualModality.DIAGRAM, VisualModality.MOTION_GRAPHICS]
            elif intent == VisualIntent.SHOW_DIFFERENCE:
                preferred_mods = [VisualModality.COMPARISON, VisualModality.DIAGRAM]
            elif intent == VisualIntent.SHOW_EVIDENCE:
                preferred_mods = [VisualModality.DOCUMENT_EVIDENCE, VisualModality.DATA_VISUALIZATION]
            elif intent == VisualIntent.ESTABLISH_CONTEXT:
                preferred_mods = [VisualModality.STOCK_VIDEO, VisualModality.GENERATED_VIDEO]
            else:
                preferred_mods = [VisualModality.MOTION_GRAPHICS, VisualModality.DIAGRAM]

            # Key entities extraction (simple capitalization / tech keywords)
            entities = [
                word.strip(".,!?;:")
                for word in clause.split()
                if (word[0].isupper() and len(word) > 1 and word.lower() not in {"the", "this", "that", "when", "with", "what", "how"})
                or word.lower() in {"sqlite", "wal", "git", "docker", "llm", "gpu", "nvidia", "linux", "cpu", "ram", "python", "ai"}
            ]

            beat = NarrativeBeat(
                beat_id=beat_id,
                scene_index=scene_index,
                narration=clause,
                start_hint=round(cur_time, 2),
                duration_hint=beat_dur,
                purpose=purpose,
                key_claim=clause if requires_evidence else None,
                key_entities=list(dict.fromkeys(entities))[:4],
                visual_intent=intent,
                importance=0.8 if purpose in (BeatPurpose.HOOK, BeatPurpose.PROVE, BeatPurpose.PAYOFF) else 0.5,
                requires_evidence=requires_evidence,
                preferred_modalities=preferred_mods,
                avoid_modalities=[VisualModality.STATIC_CARD],
            )
            beats.append(beat)
            cur_time += beat_dur

        return beats

    def decompose_script(
        self,
        script: Script,
        total_audio_duration: float,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
    ) -> List[NarrativeBeat]:
        """Decompose an entire Script into a sequenced list of narrative beats fitting total audio duration."""
        if not script or not script.scenes:
            return []

        scenes = script.scenes
        total_scenes = len(scenes)

        # Distribute total duration across scenes proportionally by word count
        scene_words = [max(1, len(s.narration.split())) for s in scenes]
        total_words = sum(scene_words)
        scene_durations = [
            round(total_audio_duration * (w / total_words), 3) for w in scene_words
        ]

        all_beats: List[NarrativeBeat] = []

        # Check if LLM backend can be used for advanced semantic decomposition
        if self.backend and hasattr(self.backend, "generate_structured"):
            try:
                prompt = f"""You are a master technical video director.
Break down this video script into punchy, dynamic narrative visual beats.
Rule 1: ONE script scene MUST become 2 to 4 visual beats (average duration ~1.5 to 3.5 seconds).
Rule 2: Narration describes. Visuals demonstrate, prove, compare, simulate, or contextualize.
Rule 3: NEVER suggest a static slide repeating the narration words.
Rule 4: Look for UI simulation, token animation, diagrams, code, charts, and evidence opportunities.

Topic: {script.title}
Format: {content_format.value}
Total Duration: {total_audio_duration:.1f}s

Script Scenes:
"""
                for idx, sc in enumerate(scenes):
                    prompt += f"Scene {idx} ({scene_durations[idx]:.1f}s): {sc.narration}\n"

                result: DecomposedBeatsPayload = self.backend.generate_structured(
                    prompt, DecomposedBeatsPayload
                )
                if result and result.beats and len(result.beats) >= len(scenes):
                    all_beats = result.beats
            except Exception:
                all_beats = []

        # Fallback to deterministic semantic decomposition
        if not all_beats:
            for idx, scene in enumerate(scenes):
                scene_beats = self.decompose_scene_deterministic(
                    scene=scene,
                    scene_index=idx,
                    total_scenes=total_scenes,
                    scene_duration=scene_durations[idx],
                    topic_title=script.title,
                )
                all_beats.extend(scene_beats)

        # Rescale duration hints so their sum exactly matches total_audio_duration
        if all_beats:
            raw_sum = sum(b.duration_hint or 2.0 for b in all_beats)
            scale = total_audio_duration / raw_sum if raw_sum > 0 else 1.0
            cur = 0.0
            for b in all_beats:
                dur = round((b.duration_hint or 2.0) * scale, 3)
                b.start_hint = round(cur, 3)
                b.duration_hint = dur
                cur += dur

        return all_beats
