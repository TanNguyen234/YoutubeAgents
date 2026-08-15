"""Script generator service interfacing with Antigravity reasoning backend to produce grounded scripts."""

from typing import List, Optional
from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.models import (
    Channel,
    Claim,
    ResearchDossier,
    Scene,
    ScriptSections,
)


class ScriptGenerator:
    """Generates structured video script sections using Antigravity reasoning from grounded research dossiers."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def generate_script_sections(
        self,
        channel: Channel,
        keyword: str,
        dossier: ResearchDossier,
    ) -> ScriptSections:
        """Generate structured script sections strictly grounded in the provided research dossier."""
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:1500] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        prompt = f"""You are a professional YouTube Shorts scriptwriter and technical educator for the channel '{channel.title}'.
Target Audience: {channel.target_audience}
Niche: {channel.niche}

TOPIC: {keyword}

VERIFIED RESEARCH EVIDENCE:
{sources_summary}

INSTRUCTIONS:
1. Write a high-retention, concise 30-45 second video script explaining '{keyword}'.
2. Ground all factual assertions STRICTLY in the provided research evidence above. Do NOT introduce ungrounded metrics or speculative claims.
3. Provide:
   - hook: Attention-grabbing question or problem statement (first 3-5 seconds).
   - intro: Quick contextual problem setup.
   - segments: 2 to 4 scenes with spoken narration, duration in seconds, and visual prompts for background footage.
   - cta: Clean, concise call to action for the channel.
   - voiceover_text: Full contiguous spoken narration text combining hook, intro, segments narration, and cta.
   - estimated_duration: Total estimated runtime in seconds (30.0 to 45.0).
"""
        return self.backend.generate_structured(prompt, ScriptSections)

    def rewrite_script_sections(
        self,
        channel: Channel,
        original_sections: ScriptSections,
        flagged_claims: List[Claim],
        dossier: ResearchDossier,
    ) -> ScriptSections:
        """Rewrite script sections to remove or fix unverified/flagged claims."""
        flagged_text = "\n".join(
            f"- Unverified Claim: '{c.statement}' -> Reason: {c.notes or 'Unsupported by evidence'}"
            for c in flagged_claims
        )
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:1500] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        prompt = f"""You are an expert script editor repairing unverified factual claims in a YouTube video script for '{channel.title}'.

ORIGINAL SCRIPT VOICEOVER:
{original_sections.voiceover_text}

FLAGGED CLAIMS REQUIRING REMOVAL OR REPAIR:
{flagged_text}

GROUND TRUTH RESEARCH EVIDENCE:
{sources_summary}

REWRITE INSTRUCTIONS:
1. Rewrite the script so that all flagged or ungrounded claims are replaced with accurate facts from the Research Evidence or cleanly omitted.
2. Maintain high retention, natural speech cadence, and educational value.
3. Return the revised ScriptSections structure with hook, intro, segments, cta, voiceover_text, and estimated_duration.
"""
        return self.backend.generate_structured(prompt, ScriptSections)
