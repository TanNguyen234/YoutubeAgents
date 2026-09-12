"""Script generator service interfacing with Antigravity reasoning backend to produce grounded scripts."""

from typing import List, Optional
from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.enums import ContentFormat
from app.domain.models import (
    Channel,
    Claim,
    HookCandidate,
    ResearchDossier,
    RetentionBlueprint,
    Scene,
    ScriptSections,
    VideoCreativeBrief,
)
from app.services.retention_planner import RetentionPlanner
from app.services.script_retention import ScriptRetentionReport


class ScriptGenerator:
    """Generates structured video script sections using Antigravity reasoning from grounded research dossiers."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def generate_script_sections(
        self,
        channel: Channel,
        keyword: str,
        dossier: ResearchDossier,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        series_continuity: Optional[dict] = None,
        brief: Optional[VideoCreativeBrief] = None,
        hook: Optional[HookCandidate] = None,
        blueprint: Optional[RetentionBlueprint] = None,
    ) -> ScriptSections:
        """Generate structured script sections strictly grounded in research using narrative retention blueprints."""
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:1500] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        continuity_section = ""
        if series_continuity:
            title = series_continuity.get("series_title") or series_continuity.get("title")
            ep_num = series_continuity.get("episode_number")
            prev_context = series_continuity.get("previous_context")
            cta = series_continuity.get("cta")
            vis_cont = series_continuity.get("visual_continuity")
            parts = []
            if title:
                parts.append(f"- Series: {title}")
            if ep_num:
                parts.append(f"- Episode Number: {ep_num}")
            if prev_context:
                parts.append(f"- Narrative Continuity Context: {prev_context}")
            if cta:
                parts.append(f"- Series Call-to-Action: {cta}")
            if vis_cont:
                parts.append(f"- Visual Continuity Style: {vis_cont}")
            if parts:
                continuity_section = "SERIES CONTINUITY CONTEXT (Preserve narrative thread & CTA, do NOT treat previous episode lore as unverified empirical claims):\n" + "\n".join(parts) + "\n\n"

        target_duration = (
            brief.target_duration_seconds
            if brief
            else (blueprint.target_duration_seconds if blueprint else 40.0)
        )

        grammar = RetentionPlanner.get_grammar(content_format)
        grammar_instructions = grammar.script_prompt_instructions

        hook_instruction = ""
        if hook:
            hook_instruction = (
                f"MANDATORY OPENING HOOK (Must use as exact hook line):\n"
                f"Text: \"{hook.text}\"\n"
                f"Viewer Promise: \"{hook.promise}\"\n\n"
            )

        blueprint_instruction = ""
        if blueprint:
            cues_summary = "\n".join(
                f"- At {int(c.target_position_ratio * 100)}% timeline ({c.cue_type.value}): {c.purpose}"
                for c in blueprint.cues
            )
            blueprint_instruction = (
                f"RETENTION BLUEPRINT PROGRESSION:\n"
                f"Core Question: {blueprint.core_question}\n"
                f"Promised Payoff: {blueprint.promised_payoff}\n"
                f"Pacing Cues:\n{cues_summary}\n\n"
            )

        tone_instruction = f"Tone: {brief.tone.value}" if brief else "Tone: CONVERSATIONAL"
        goal_instruction = f"Primary Goal: {brief.primary_goal.value}" if brief else "Primary Goal: WATCH_TIME"

        prompt = f"""You are an elite YouTube creator and scriptwriter renowned for high-retention viral tech videos for '{channel.title}'.
Audience: {channel.target_audience}
Niche: {channel.niche}
{tone_instruction} | {goal_instruction}

TOPIC SEED: {keyword}
TARGET RUNTIME: ~{target_duration:.0f} seconds

{grammar_instructions}

{hook_instruction}{blueprint_instruction}{continuity_section}VERIFIED GROUND-TRUTH RESEARCH EVIDENCE:
{sources_summary}

STRICT RETENTION & FACTUAL GROUNDING RULES:
1. OPENING HOOK: {"Use the exact hook text provided above." if hook else "Start immediately in medias res with a high-stakes paradox or contrarian question. No boring textbook greetings."}
2. PROMISE & PAYOFF ALIGNMENT: The narrative must escalate directly toward fulfilling the promised payoff. The final segment must clearly resolve the opening question.
3. PACING & DURATION: Target approximately {target_duration:.0f} seconds total runtime.
4. OBSERVABLE VISUAL ACTION:
   - In 'visual_prompt', describe WHAT must be shown (concrete observable action or visual evidence requirement).
   - DO NOT prescribe glowing UI, neon code, cyberpunk aesthetics, generic B-roll, or aesthetic styling. AutoDirector determines visual style.
5. FACTUAL GROUNDING:
   - All factual assertions MUST be 100% strictly grounded in the VERIFIED RESEARCH EVIDENCE above. Do not invent ungrounded benchmarks.

OUTPUT SCHEMA REQUIREMENTS:
- hook: The opening hook line (3-5 seconds).
- intro: Context setup establishing the stakes and problem.
- segments: Sequential scene segments, each with:
    * narration: Spoken voiceover for this beat (conversational, punchy, active voice).
    * visual_prompt: Observable visual action or concrete evidence requirement (e.g. 'Show reader thread querying database while writer appends to WAL file').
    * target_duration_seconds: Duration in seconds.
- cta: Punchy, contextual call to action placed at the very end.
- voiceover_text: Seamless full contiguous voiceover combining hook, intro, segment narrations, and cta.
- estimated_duration: Total duration (~{target_duration:.0f} seconds).
"""
        sections = self.backend.generate_structured(prompt, ScriptSections)
        if hook:
            sections.hook = hook.text
            # Re-ensure canonical voiceover includes winning hook
            sections.ensure_canonical_voiceover()

        return sections

    def rewrite_for_retention(
        self,
        channel: Channel,
        original_sections: ScriptSections,
        retention_report: ScriptRetentionReport,
        blueprint: RetentionBlueprint,
        dossier: ResearchDossier,
    ) -> ScriptSections:
        """Rewrite script sections to eliminate detected drop risks and resolve open loops while maintaining factual truth."""
        issues_summary = "\n".join(f"- Issue: {iss}" for iss in retention_report.issues)
        instructions_summary = "\n".join(f"- Rewrite instruction: {inst}" for inst in retention_report.rewrite_instructions)
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:1200] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        prompt = f"""You are an elite YouTube script doctor repairing retention hazards in a video script for '{channel.title}'.

ORIGINAL SCRIPT VOICEOVER:
{original_sections.voiceover_text}

DETECTED RETENTION HAZARDS:
{issues_summary}

REWRITE DIRECTIVES:
{instructions_summary}
- Opening Hook: Must preserve '{blueprint.hook.text}'
- Promised Payoff: Must resolve '{blueprint.promised_payoff}' in the climax/ending
- Pacing: Eliminate exposition stalls, ensure no CTA before payoff.
- Observable Visual Action: Describe what must be shown, NOT neon/cyberpunk styling.

GROUND TRUTH RESEARCH EVIDENCE:
{sources_summary}

Return the repaired ScriptSections with hook, intro, segments, cta, voiceover_text, and estimated_duration.
"""
        revised = self.backend.generate_structured(prompt, ScriptSections)
        if blueprint and blueprint.hook:
            revised.hook = blueprint.hook.text
            revised.ensure_canonical_voiceover()
        return revised

    def rewrite_script_sections(
        self,
        channel: Channel,
        original_sections: ScriptSections,
        flagged_claims: List[Claim],
        dossier: ResearchDossier,
    ) -> ScriptSections:
        """Rewrite script sections to remove or fix unverified/flagged claims while preserving viral retention structure."""
        flagged_text = "\n".join(
            f"- Unverified Claim: '{c.statement}' -> Reason: {c.notes or 'Unsupported by evidence'}"
            for c in flagged_claims
        )
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:1500] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        prompt = f"""You are an elite YouTube script doctor repairing ungrounded factual claims in a high-retention video script for '{channel.title}'.

ORIGINAL SCRIPT VOICEOVER:
{original_sections.voiceover_text}

FLAGGED CLAIMS REQUIRING CORRECTION OR REMOVAL:
{flagged_text}

GROUND TRUTH RESEARCH EVIDENCE:
{sources_summary}

REWRITE RULES:
1. Replace or delete every single flagged claim with accurate facts directly verifiable from the Research Evidence.
2. PRESERVE the intense pacing, tension hook, and conversational rhythm (NO textbook slop, NO generic lecture voice).
3. Ensure visual_prompt describes observable actions or evidence, NOT aesthetic styling or glowing UI slop.
4. Return the repaired ScriptSections structure with hook, intro, segments, cta, voiceover_text, and estimated_duration.
"""
        return self.backend.generate_structured(prompt, ScriptSections)


