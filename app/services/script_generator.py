"""Script generator service interfacing with Antigravity reasoning backend to produce grounded scripts."""

from typing import List, Optional
from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.enums import ContentFormat
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
        content_format: ContentFormat = ContentFormat.EXPLAINER,
    ) -> ScriptSections:
        """Generate structured script sections strictly grounded in the provided research dossier using high-retention storytelling."""
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:1500] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        format_guidelines = {
            ContentFormat.EXPLAINER: (
                "FORMAT: EXPLAINER\n"
                "- Focus on the core mechanism with intuitive analogy and visual proof.\n"
                "- Pacing: Hook -> Stakes -> Inner Working -> Breakthrough Payoff."
            ),
            ContentFormat.DEMO: (
                "FORMAT: DEMO / HANDS-ON TUTORIAL\n"
                "- Focus on concrete action: show setup -> run command -> inspect result -> observe pass/fail.\n"
                "- Pacing: Action-oriented, live syntax, no theoretical fluff."
            ),
            ContentFormat.COMPARISON: (
                "FORMAT: HEAD-TO-HEAD COMPARISON (A vs B)\n"
                "- Focus on comparison dimensions: define contrast -> A behavior -> B behavior -> empirical verdict.\n"
                "- Pacing: Balanced, fact-grounded, clear side-by-side trade-offs."
            ),
            ContentFormat.CASE_STUDY: (
                "FORMAT: PRODUCTION CASE STUDY / POST-MORTEM\n"
                "- Focus on real events: context -> critical outage/problem -> forensic discovery -> permanent fix.\n"
                "- Pacing: Narrative tension, forensic logs, hard lessons."
            ),
            ContentFormat.EXPERIMENT: (
                "FORMAT: BENCHMARK EXPERIMENT\n"
                "- Focus on empirical measurement: hypothesis -> methodology -> metrics -> verdict.\n"
                "- Pacing: Scientific rigor, grounded numbers, transparent analysis."
            ),
            ContentFormat.NEWS: (
                "FORMAT: INDUSTRY NEWS / BREAKTHROUGH\n"
                "- Focus on urgent shift: what happened -> why it matters today -> downstream consequences.\n"
                "- Pacing: Fast, high urgency, direct relevance."
            ),
        }.get(content_format, "FORMAT: EXPLAINER\n- Core mechanism with clear visual payoff.")

        prompt = f"""You are an elite YouTube creator and scriptwriter renowned for high-retention viral tech videos (in the style of Fireship and Veritasium) for '{channel.title}'.
Audience: {channel.target_audience}
Niche: {channel.niche}

TOPIC SEED: {keyword}

{format_guidelines}

VERIFIED GROUND-TRUTH RESEARCH EVIDENCE:
{sources_summary}

STRICT ANTI-AI-SLOP RETENTION RULES:
1. NEVER start with boring textbook greetings ("In this video", "Welcome back", "Today we will explore", "SQLite is a database...").
2. START IN MEDIAS RES (First 3 seconds): Use a high-stakes paradox, common misconception, or dramatic contrarian hook that stops the scroll immediately.
3. STORYTELLING CADENCE (30-42 seconds total):
   - Hook (0-4s): Shocking claim, paradox, or riddle.
   - Stakes & Conflict (4-12s): What goes wrong without this? (e.g. server crashes, millions of dollars lost, locks freeze everything).
   - Mechanism / Action (12-28s): Explain or demonstrate the core solution.
   - Payoff (28-36s): The triumphant breakthrough / measurable win.
   - Call to Action (36-40s): Short, punchy tease for the next episode.
4. TONE & PACING:
   - Punchy conversational rhythm, short sentences, active verbs.
   - Natural spoken cadence (no robotic passive voice).
5. FACTUAL GROUNDING:
   - All factual assertions MUST be 100% strictly grounded in the VERIFIED RESEARCH EVIDENCE above. Do not invent ungrounded benchmarks.

OUTPUT SCHEMA REQUIREMENTS:
- hook: The explosive 3-second opening hook.
- intro: Quick 4-second conflict setup.
- segments: 3 to 4 sequential scene segments, each with:
    * narration: Spoken voiceover for this beat (conversational, punchy).
    * visual_prompt: Cinematic, dynamic visual description for GFlow/graphics (avoid static slides; specify motion, glowing UI, code metaphors).
    * target_duration_seconds: Duration in seconds (5.0 to 12.0s per segment).
- cta: Punchy 1-sentence call to action.
- voiceover_text: Full seamless contiguous voiceover combining hook, intro, segment narrations, and cta.
- estimated_duration: Total duration (30.0 to 42.0 seconds).
"""
        return self.backend.generate_structured(prompt, ScriptSections)

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
3. Return the repaired ScriptSections structure with hook, intro, segments, cta, voiceover_text, and estimated_duration.
"""
        return self.backend.generate_structured(prompt, ScriptSections)

