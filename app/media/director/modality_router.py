"""Visual Modality Router prioritizing semantic, explanatory visual mediums over generic slides."""

from typing import List, Optional
from app.media.director.models import (
    BeatPurpose,
    ChannelCreativeProfile,
    ContentFormat,
    NarrativeBeat,
    VisualIntent,
    VisualModality,
)


class VisualModalityRouter:
    """Selects the highest-value visual modality for each narrative beat based on intent, topic, and format."""

    def __init__(self, profile: Optional[ChannelCreativeProfile] = None):
        self.profile = profile or ChannelCreativeProfile()

    def route_modality(
        self,
        beat: NarrativeBeat,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        available_modalities: Optional[List[VisualModality]] = None,
    ) -> VisualModality:
        """Decide the optimal visual modality for a narrative beat."""
        allowed = set(available_modalities or list(VisualModality))
        if self.profile.avoid_modalities:
            # Demote avoided modalities unless explicitly requested
            for av in self.profile.avoid_modalities:
                if av in allowed and len(allowed) > 1:
                    allowed.discard(av)

        # 1. Direct Preferred Modality check from the beat
        for pref in beat.preferred_modalities:
            if pref in allowed:
                return pref

        # 2. Evidence / empirical verification
        if beat.requires_evidence:
            if VisualModality.DOCUMENT_EVIDENCE in allowed:
                return VisualModality.DOCUMENT_EVIDENCE
            if VisualModality.DATA_VISUALIZATION in allowed:
                return VisualModality.DATA_VISUALIZATION

        # 3. Format-specific routing
        if content_format == ContentFormat.DEMO:
            for mod in [VisualModality.UI_SIMULATION, VisualModality.CODE_ANIMATION, VisualModality.MOTION_GRAPHICS]:
                if mod in allowed:
                    return mod
        elif content_format == ContentFormat.COMPARISON or beat.purpose == BeatPurpose.COMPARE:
            for mod in [VisualModality.COMPARISON, VisualModality.DIAGRAM, VisualModality.DATA_VISUALIZATION]:
                if mod in allowed:
                    return mod
        elif content_format == ContentFormat.CASE_STUDY:
            for mod in [VisualModality.DATA_VISUALIZATION, VisualModality.DOCUMENT_EVIDENCE, VisualModality.TIMELINE]:
                if mod in allowed:
                    return mod

        # 4. Semantic VisualIntent routing
        intent_mapping = {
            VisualIntent.SHOW_DATA: [VisualModality.DATA_VISUALIZATION, VisualModality.MOTION_GRAPHICS],
            VisualIntent.SHOW_CODE: [VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION],
            VisualIntent.SHOW_INTERFACE: [VisualModality.UI_SIMULATION, VisualModality.SCREENSHOT],
            VisualIntent.SHOW_MECHANISM: [VisualModality.DIAGRAM, VisualModality.MOTION_GRAPHICS],
            VisualIntent.SHOW_DIFFERENCE: [VisualModality.COMPARISON, VisualModality.DIAGRAM],
            VisualIntent.SHOW_EVIDENCE: [VisualModality.DOCUMENT_EVIDENCE, VisualModality.DATA_VISUALIZATION],
            VisualIntent.SHOW_TIMELINE: [VisualModality.TIMELINE, VisualModality.MOTION_GRAPHICS],
            VisualIntent.SHOW_SCALE: [VisualModality.DATA_VISUALIZATION, VisualModality.MOTION_GRAPHICS],
            VisualIntent.SHOW_PROCESS: [VisualModality.DIAGRAM, VisualModality.MOTION_GRAPHICS],
            VisualIntent.ESTABLISH_CONTEXT: [VisualModality.STOCK_VIDEO, VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE],
            VisualIntent.CREATE_EMOTION: [VisualModality.GENERATED_VIDEO, VisualModality.STOCK_VIDEO],
        }

        candidates = intent_mapping.get(beat.visual_intent, [VisualModality.MOTION_GRAPHICS, VisualModality.DIAGRAM])
        for cand in candidates:
            if cand in allowed:
                return cand

        # 5. Channel Profile Preferred Modalities
        for pref in self.profile.preferred_modalities:
            if pref in allowed:
                return pref

        # 6. Fallback hierarchy: Diagram > Motion Graphics > Generated Video > Static Card
        for fallback in [
            VisualModality.DIAGRAM,
            VisualModality.MOTION_GRAPHICS,
            VisualModality.DATA_VISUALIZATION,
            VisualModality.GENERATED_IMAGE,
            VisualModality.STATIC_CARD,
        ]:
            if fallback in allowed:
                return fallback

        return VisualModality.STATIC_CARD
