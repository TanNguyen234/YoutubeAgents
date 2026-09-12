"""Retention planner orchestrating format-specific narrative grammars and duration-aware retention blueprints."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.domain.enums import ContentFormat, RetentionCueType
from app.domain.models import (
    HookCandidate,
    ResearchDossier,
    RetentionBlueprint,
    RetentionCue,
    TimedRetentionCue,
    VideoCreativeBrief,
)


@dataclass(frozen=True)
class FormatNarrativeGrammar:
    """Explicit storytelling stages and narrative rules for a specific content format."""

    content_format: ContentFormat
    stages: List[str]
    description: str
    script_prompt_instructions: str


FORMAT_GRAMMARS: Dict[ContentFormat, FormatNarrativeGrammar] = {
    ContentFormat.EXPLAINER: FormatNarrativeGrammar(
        content_format=ContentFormat.EXPLAINER,
        stages=["Hook", "Problem", "Mechanism", "Surprising Implication", "Payoff"],
        description="Core mechanism explanation with intuitive analogy and visual proof.",
        script_prompt_instructions=(
            "FORMAT: EXPLAINER\n"
            "- Structure: Hook -> Mental Model -> Core Mechanism -> Edge Cases -> Takeaway\n"
            "NARRATIVE GRAMMAR (EXPLAINER):\n"
            "1. Hook: Immediate paradox, misconception, or bold question.\n"
            "2. Problem: Why default mental models fail or why this matters.\n"
            "3. Mechanism: The inner working explained via observable cause-and-effect.\n"
            "4. Surprising Implication: The non-obvious consequence or hidden advantage.\n"
            "5. Payoff: Definitive breakthrough resolving the opening hook promise."
        ),
    ),
    ContentFormat.DEMO: FormatNarrativeGrammar(
        content_format=ContentFormat.DEMO,
        stages=["Desired Result", "Setup", "Action", "Visible Result", "Failure/Edge Case", "Takeaway"],
        description="Hands-on demonstration showing action, live syntax, and observable output.",
        script_prompt_instructions=(
            "FORMAT: DEMO / HANDS-ON TUTORIAL\n"
            "- Structure: Expected outcome -> show setup -> run command/code -> observe output -> troubleshooting\n"
            "NARRATIVE GRAMMAR (DEMO / TUTORIAL):\n"
            "1. Desired Result: Show the target outcome first.\n"
            "2. Setup: Minimal prerequisite configuration without fluff.\n"
            "3. Action: Execute the key command or code mutation.\n"
            "4. Visible Result: Inspect real output or console state.\n"
            "5. Failure/Edge Case: Highlight the common mistake that breaks this.\n"
            "6. Takeaway: The key principle to remember."
        ),
    ),
    ContentFormat.COMPARISON: FormatNarrativeGrammar(
        content_format=ContentFormat.COMPARISON,
        stages=["Shared Problem", "Option A", "Option B", "Decisive Dimension", "Tradeoff", "Verdict"],
        description="Head-to-head evaluation analyzing trade-offs across decisive dimensions.",
        script_prompt_instructions=(
            "FORMAT: HEAD-TO-HEAD COMPARISON\n"
            "- Structure: Contender overview -> workload benchmark -> A behavior -> B behavior -> decision matrix\n"
            "NARRATIVE GRAMMAR (COMPARISON / HEAD-TO-HEAD):\n"
            "1. Shared Problem: The common challenge both contenders attempt to solve.\n"
            "2. Option A: Strengths and core design philosophy of Contender A.\n"
            "3. Option B: Contrast with Contender B's architectural approach.\n"
            "4. Decisive Dimension: The critical workload or metric where divergence happens.\n"
            "5. Tradeoff: The cost of choosing one over the other.\n"
            "6. Verdict: Clear, conditional decision rule (choose A when X, choose B when Y)."
        ),
    ),
    ContentFormat.EXPERIMENT: FormatNarrativeGrammar(
        content_format=ContentFormat.EXPERIMENT,
        stages=["Question", "Hypothesis", "Method", "Expectation", "Result", "Interpretation"],
        description="Empirical benchmark or test measuring real-world behavior under controlled conditions.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (BENCHMARK EXPERIMENT):\n"
            "1. Question: The measurable question under test.\n"
            "2. Hypothesis: The common belief vs our counter-hypothesis.\n"
            "3. Method: The exact test harness, dataset, or workload.\n"
            "4. Expectation: What theory predicts should happen.\n"
            "5. Result: The raw empirical measurements and surprising data.\n"
            "6. Interpretation: The technical explanation for the observed discrepancy."
        ),
    ),
    ContentFormat.CASE_STUDY: FormatNarrativeGrammar(
        content_format=ContentFormat.CASE_STUDY,
        stages=["Cold Open Incident", "Context", "Failure", "Investigation", "Discovery", "Fix", "Lesson"],
        description="Forensic examination of a real production incident or engineering milestone.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (CASE STUDY / POST-MORTEM):\n"
            "1. Cold Open Incident: High-tension opening inside the critical failure.\n"
            "2. Context: The baseline architecture before catastrophe struck.\n"
            "3. Failure: The trigger event that caused the outage or bottleneck.\n"
            "4. Investigation: Eliminating misleading theories through telemetry.\n"
            "5. Discovery: The root cause uncovered in code or hardware.\n"
            "6. Fix: The surgical remedy applied to restore stability.\n"
            "7. Lesson: The enduring architectural rule learned."
        ),
    ),
    ContentFormat.BREAKDOWN: FormatNarrativeGrammar(
        content_format=ContentFormat.BREAKDOWN,
        stages=["Outcome", "Components", "Mechanism", "Hidden Constraint", "Synthesis"],
        description="Deconstruction of a complex system into interacting primitives.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (SYSTEM BREAKDOWN):\n"
            "1. Outcome: The impressive holistic capability of the system.\n"
            "2. Components: The 2 to 4 fundamental primitives that make it work.\n"
            "3. Mechanism: How data or control flows across these primitives.\n"
            "4. Hidden Constraint: The engineering bottleneck the designers had to overcome.\n"
            "5. Synthesis: How the pieces combine to produce the emergent capability."
        ),
    ),
    ContentFormat.MYTH_BUSTING: FormatNarrativeGrammar(
        content_format=ContentFormat.MYTH_BUSTING,
        stages=["Common Belief", "Contradiction", "Evidence", "Explanation", "Corrected Mental Model"],
        description="Refutation of widespread misconception using empirical evidence and first principles.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (MYTH BUSTING):\n"
            "1. Common Belief: The widely accepted dogma everyone repeats.\n"
            "2. Contradiction: The specific scenario where this advice fails catastrophically.\n"
            "3. Evidence: Hard data, code proof, or benchmarks disproving the myth.\n"
            "4. Explanation: Why the myth became popular despite being flawed.\n"
            "5. Corrected Mental Model: The nuanced reality developers should actually follow."
        ),
    ),
    ContentFormat.STORY: FormatNarrativeGrammar(
        content_format=ContentFormat.STORY,
        stages=["Cold Open", "Unanswered Question", "Setup", "Escalation", "Reversal", "Payoff", "Callback"],
        description="Narrative arc with character agency, obstacles, plot twists, and philosophical payoff.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (STORY ARC):\n"
            "1. Cold Open: In media res moment of intense conflict.\n"
            "2. Unanswered Question: The central mystery compelling the viewer forward.\n"
            "3. Setup: The protagonist, creator, or team's original ambition.\n"
            "4. Escalation: Compounding obstacles threatening failure.\n"
            "5. Reversal: An unexpected insight that turns the tide.\n"
            "6. Payoff: Resolution of the central obstacle.\n"
            "7. Callback: A subtle return to the opening scene with transformed meaning."
        ),
    ),
    ContentFormat.CHALLENGE: FormatNarrativeGrammar(
        content_format=ContentFormat.CHALLENGE,
        stages=["Goal", "Constraint", "Attempt", "Obstacle", "Adaptation", "Result"],
        description="High-stakes endeavor attempted under extreme constraints.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (CHALLENGE):\n"
            "1. Goal: The seemingly impossible target.\n"
            "2. Constraint: The strict rule or limitation making it difficult.\n"
            "3. Attempt: The initial naive strategy put into action.\n"
            "4. Obstacle: Where the naive strategy fails under stress.\n"
            "5. Adaptation: The clever pivot or technical breakthrough.\n"
            "6. Result: The final score, build verification, or verdict."
        ),
    ),
    ContentFormat.NEWS: FormatNarrativeGrammar(
        content_format=ContentFormat.NEWS,
        stages=["What Happened", "Why Now", "Impact", "Hidden Consequence", "What to Watch Next"],
        description="Urgent industry announcement analyzed for downstream technical repercussions.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (INDUSTRY NEWS / SHIFT):\n"
            "1. What Happened: The core announcement or release in 5 seconds.\n"
            "2. Why Now: The breakthrough or pressure that forced this change.\n"
            "3. Impact: Who wins and who gets disrupted immediately.\n"
            "4. Hidden Consequence: The subtle second-order effect nobody is discussing.\n"
            "5. What to Watch Next: The critical metric or milestone to monitor."
        ),
    ),
    ContentFormat.RANKING: FormatNarrativeGrammar(
        content_format=ContentFormat.RANKING,
        stages=["Criteria", "Low Ranks", "Escalating Tradeoffs", "Top Choice", "Caveat"],
        description="Tier list or comparative ranking based on rigorous technical evaluation criteria.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (RANKING / TIER LIST):\n"
            "1. Criteria: The decisive rules governing how items are judged.\n"
            "2. Low Ranks: Competitors that look good on paper but have fatal flaws.\n"
            "3. Escalating Tradeoffs: Contenders that excel in specific niches.\n"
            "4. Top Choice: The undisputed winner across standard production demands.\n"
            "5. Caveat: The one specific situation where you should NOT pick number one."
        ),
    ),
    ContentFormat.BEFORE_AFTER: FormatNarrativeGrammar(
        content_format=ContentFormat.BEFORE_AFTER,
        stages=["Old State", "Pain", "Change", "New State", "Measurable Difference"],
        description="Transformation showcase highlighting contrast between legacy and modernized states.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (BEFORE & AFTER TRANSFORMATION):\n"
            "1. Old State: The clunky, slow, or frustrating baseline.\n"
            "2. Pain: The daily friction or cost of staying on the old way.\n"
            "3. Change: The single critical migration, refactor, or adoption.\n"
            "4. New State: The sleek, responsive, modern workflow.\n"
            "5. Measurable Difference: Hard benchmark or productivity delta proving the upgrade."
        ),
    ),
    ContentFormat.PROBLEM_SOLUTION: FormatNarrativeGrammar(
        content_format=ContentFormat.PROBLEM_SOLUTION,
        stages=["Pain", "Failed/Common Solution", "Root Cause", "Solution", "Proof"],
        description="Problem-centric architecture isolating root cause before delivering verified remedy.",
        script_prompt_instructions=(
            "NARRATIVE GRAMMAR (PROBLEM & SOLUTION):\n"
            "1. Pain: The immediate symptom causing frustration.\n"
            "2. Failed/Common Solution: Why the intuitive workaround actually makes things worse.\n"
            "3. Root Cause: The hidden underlying defect causing the symptom.\n"
            "4. Solution: The correct architectural or code fix.\n"
            "5. Proof: Verification demonstrating that the problem is permanently gone."
        ),
    ),
}


class RetentionPlanner:
    """Plans format-specific and duration-scaled narrative retention blueprints."""

    @staticmethod
    def get_grammar(content_format: ContentFormat) -> FormatNarrativeGrammar:
        """Return the explicit narrative grammar for any ContentFormat."""
        return FORMAT_GRAMMARS.get(content_format, FORMAT_GRAMMARS[ContentFormat.EXPLAINER])

    def build_blueprint(
        self,
        hook: HookCandidate,
        brief: VideoCreativeBrief,
        content_format: ContentFormat,
        topic: str,
        dossier: Optional[ResearchDossier] = None,
    ) -> RetentionBlueprint:
        """Construct a structured retention blueprint matching format grammar and scaled by duration."""
        duration = max(5.0, float(brief.target_duration_seconds))
        grammar = self.get_grammar(content_format)

        clean_topic = topic.strip().rstrip(".")
        core_question = f"How does {clean_topic} resolve the tension raised by '{hook.text}'?"
        promised_payoff = hook.promise or f"Provide definitive clarity on {clean_topic}."

        cues: List[RetentionCue] = []

        # Duration-aware pacing scaling (Phase 8)
        if duration <= 20.0:
            # Ultra-short (e.g. 15s Shorts): Minimal cues, immediate payoff
            cues.append(
                RetentionCue(
                    cue_id="cue_01_open_loop",
                    cue_type=RetentionCueType.OPEN_LOOP,
                    target_position_ratio=0.15,
                    purpose=f"Establish curiosity gap for {grammar.stages[1]}",
                    anchor_text=hook.text,
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_02_payoff",
                    cue_type=RetentionCueType.REVEAL,
                    target_position_ratio=0.75,
                    purpose=f"Deliver core mechanism payoff ({grammar.stages[-1]})",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_03_close",
                    cue_type=RetentionCueType.LOOP_CLOSE,
                    target_position_ratio=0.92,
                    purpose="Close open loop and solidify takeaway",
                    linked_hook_promise=promised_payoff,
                )
            )

        elif duration <= 40.0:
            # Short (e.g. 30s Shorts): 1 open loop, 1 pattern interrupt / rehook, reveal, loop close, cta
            cues.append(
                RetentionCue(
                    cue_id="cue_01_open_loop",
                    cue_type=RetentionCueType.OPEN_LOOP,
                    target_position_ratio=0.12,
                    purpose=f"Hook conflict and open core question: {grammar.stages[1]}",
                    anchor_text=hook.text,
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_02_pattern_interrupt",
                    cue_type=RetentionCueType.PATTERN_INTERRUPT,
                    target_position_ratio=0.45,
                    purpose=f"Shift pacing from setup to observable mechanism ({grammar.stages[2]})",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_03_reveal",
                    cue_type=RetentionCueType.REVEAL,
                    target_position_ratio=0.75,
                    purpose=f"Reveal the breakthrough insight: {grammar.stages[-2] if len(grammar.stages) > 3 else grammar.stages[-1]}",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_04_loop_close",
                    cue_type=RetentionCueType.LOOP_CLOSE,
                    target_position_ratio=0.90,
                    purpose=f"Resolve opening hook promise ({grammar.stages[-1]})",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_05_cta",
                    cue_type=RetentionCueType.CTA,
                    target_position_ratio=0.96,
                    purpose="Tight, punchy call-to-action anchored to content value",
                )
            )

        elif duration <= 60.0:
            # Mid-short (e.g. 45-60s Shorts): 1 main loop, rehook, pattern interrupt, escalation, reveal, close, cta
            cues.append(
                RetentionCue(
                    cue_id="cue_01_open_loop",
                    cue_type=RetentionCueType.OPEN_LOOP,
                    target_position_ratio=0.10,
                    purpose=f"State paradox and open central question: {grammar.stages[1]}",
                    anchor_text=hook.text,
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_02_rehook",
                    cue_type=RetentionCueType.REHOOK,
                    target_position_ratio=0.30,
                    purpose=f"Deepen the stakes before technical explanation: {grammar.stages[1]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_03_pattern_interrupt",
                    cue_type=RetentionCueType.PATTERN_INTERRUPT,
                    target_position_ratio=0.50,
                    purpose=f"Modality shift to live evidence/demonstration: {grammar.stages[2]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_04_escalation",
                    cue_type=RetentionCueType.ESCALATION,
                    target_position_ratio=0.68,
                    purpose=f"Escalate to surprising implication or edge case: {grammar.stages[3] if len(grammar.stages) > 3 else grammar.stages[-1]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_05_climax",
                    cue_type=RetentionCueType.CLIMAX,
                    target_position_ratio=0.82,
                    purpose=f"Peak demonstration or benchmark payoff: {grammar.stages[-1]}",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_06_loop_close",
                    cue_type=RetentionCueType.LOOP_CLOSE,
                    target_position_ratio=0.92,
                    purpose="Explicitly close opening open loop and deliver conclusion",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_07_cta",
                    cue_type=RetentionCueType.CTA,
                    target_position_ratio=0.97,
                    purpose="Contextual next-step CTA",
                )
            )

        elif duration <= 180.0:
            # Standard video (1-3 min): Main loop, secondary loop, periodic rehooks & interrupts
            cues.append(
                RetentionCue(
                    cue_id="cue_01_primary_loop",
                    cue_type=RetentionCueType.OPEN_LOOP,
                    target_position_ratio=0.08,
                    purpose=f"Primary narrative loop: {grammar.stages[0]} -> {grammar.stages[1]}",
                    anchor_text=hook.text,
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_02_rehook",
                    cue_type=RetentionCueType.REHOOK,
                    target_position_ratio=0.25,
                    purpose=f"Raise the stakes and introduce secondary open loop: {grammar.stages[1]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_03_pattern_interrupt",
                    cue_type=RetentionCueType.PATTERN_INTERRUPT,
                    target_position_ratio=0.42,
                    purpose=f"Shift from theory to concrete evidence: {grammar.stages[2]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_04_escalation",
                    cue_type=RetentionCueType.ESCALATION,
                    target_position_ratio=0.60,
                    purpose=f"Complicate with hidden constraint or edge case: {grammar.stages[3] if len(grammar.stages) > 3 else grammar.stages[-2]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_05_secondary_interrupt",
                    cue_type=RetentionCueType.PATTERN_INTERRUPT,
                    target_position_ratio=0.75,
                    purpose="Visual break preceding the climax",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_06_climax",
                    cue_type=RetentionCueType.CLIMAX,
                    target_position_ratio=0.86,
                    purpose=f"Decisive proof and ultimate mechanism reveal: {grammar.stages[-1]}",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_07_loop_close",
                    cue_type=RetentionCueType.LOOP_CLOSE,
                    target_position_ratio=0.94,
                    purpose="Close all open narrative loops with definitive answer",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_08_cta",
                    cue_type=RetentionCueType.CTA,
                    target_position_ratio=0.98,
                    purpose="Strategic channel subscription or series continuation CTA",
                )
            )

        else:
            # Long-form (>3 min): Section-level progression with nested loops
            cues.append(
                RetentionCue(
                    cue_id="cue_01_macro_loop",
                    cue_type=RetentionCueType.OPEN_LOOP,
                    target_position_ratio=0.05,
                    purpose=f"Macro thesis open loop: {grammar.stages[0]}",
                    anchor_text=hook.text,
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_02_section_1_interrupt",
                    cue_type=RetentionCueType.PATTERN_INTERRUPT,
                    target_position_ratio=0.20,
                    purpose=f"Section 1 transition to demonstration: {grammar.stages[1]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_03_midpoint_rehook",
                    cue_type=RetentionCueType.REHOOK,
                    target_position_ratio=0.40,
                    purpose=f"Midpoint escalation and new question: {grammar.stages[2]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_04_section_2_interrupt",
                    cue_type=RetentionCueType.PATTERN_INTERRUPT,
                    target_position_ratio=0.60,
                    purpose=f"Section 2 deep dive transition: {grammar.stages[3] if len(grammar.stages) > 3 else grammar.stages[-2]}",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_05_reversal",
                    cue_type=RetentionCueType.ESCALATION,
                    target_position_ratio=0.75,
                    purpose="Counter-intuitive plot twist or stress-test failure",
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_06_climax",
                    cue_type=RetentionCueType.CLIMAX,
                    target_position_ratio=0.88,
                    purpose=f"Definitive synthesis and empirical proof: {grammar.stages[-1]}",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_07_loop_close",
                    cue_type=RetentionCueType.LOOP_CLOSE,
                    target_position_ratio=0.95,
                    purpose="Resolve macro question and callback to opening hook",
                    linked_hook_promise=promised_payoff,
                )
            )
            cues.append(
                RetentionCue(
                    cue_id="cue_08_cta",
                    cue_type=RetentionCueType.CTA,
                    target_position_ratio=0.99,
                    purpose="Comprehensive series wrap-up and authoritative CTA",
                )
            )

        return RetentionBlueprint(
            hook=hook,
            cues=cues,
            core_question=core_question,
            promised_payoff=promised_payoff,
            content_format=content_format,
            target_duration_seconds=duration,
        )


def map_retention_cues_to_timestamps(
    cues: List[RetentionCue],
    total_duration_seconds: float,
    timing_events: Optional[List[Dict[str, Any]]] = None,
    canonical_narration: Optional[str] = None,
) -> List[TimedRetentionCue]:
    """Map ratio-based retention cues to actual timestamps using real TTS audio duration and word boundaries."""
    timed_cues: List[TimedRetentionCue] = []
    if total_duration_seconds <= 0.0 or not cues:
        return timed_cues

    import re

    for cue in cues:
        matched_time = None

        # 1. Word timing / narration matching to anchor cue to real spoken text
        if cue.anchor_text and timing_events:
            anchor_clean = cue.anchor_text.strip().lower()
            anchor_words = [w for w in re.sub(r"[^\w\s]", " ", anchor_clean).split() if len(w) > 1]
            if anchor_words:
                first_word = anchor_words[0]
                for evt in timing_events:
                    w_text = (evt.get("word") or evt.get("text") or "").strip().lower()
                    w_clean = re.sub(r"[^\w\s]", "", w_text)
                    if w_clean == first_word:
                        if "start" in evt:
                            matched_time = float(evt["start"])
                        elif "offset" in evt:
                            # edge-tts offset is in 100ns (ticks)
                            matched_time = round(float(evt["offset"]) / 10_000_000.0, 3)
                        break

        # 2. Ratio-based calculation fallback
        if matched_time is None:
            matched_time = round(cue.target_position_ratio * total_duration_seconds, 3)

        clamped_time = max(0.0, min(total_duration_seconds, matched_time))
        timed_cues.append(
            TimedRetentionCue(
                cue_id=cue.cue_id,
                cue_type=cue.cue_type,
                timestamp_seconds=clamped_time,
                narration_anchor=cue.anchor_text,
            )
        )

    return timed_cues

