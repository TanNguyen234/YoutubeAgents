import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.domain.enums import (
    ConcreteAnchorType,
    ContentFormat,
    PsychologicalMechanism,
    RetentionCueType,
)
from app.domain.models import RetentionBlueprint, Script, ScriptSections


class DropRisk(BaseModel):
    """Identified viewer drop-off hazard with severity and suggested remedy."""

    start_ratio: float = Field(ge=0.0, le=1.0, description="Normalized starting position of risk")
    end_ratio: float = Field(ge=0.0, le=1.0, description="Normalized ending position of risk")
    reason: str = Field(description="Diagnostic reason for drop risk")
    severity: str = Field(description="HIGH, MEDIUM, or LOW")
    rewrite_hint: Optional[str] = Field(default=None, description="Actionable hint for script rewrite")


class OpenLoopAudit(BaseModel):
    """Audit record for a narrative open loop (curiosity gap or unanswered question)."""

    question: str = Field(description="The open loop question or curiosity gap")
    opened_at_ratio: float = Field(ge=0.0, le=1.0, description="Where loop was initiated")
    closed_at_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Where loop was resolved")
    resolved: bool = Field(default=False, description="Whether open loop reached payoff")


class RetentionMoment(BaseModel):
    """Strong positive retention milestone detected in script."""

    position_ratio: float = Field(ge=0.0, le=1.0, description="Normalized script position ratio")
    timestamp_seconds: Optional[float] = Field(default=None, ge=0.0, description="Mapped timestamp in seconds from real audio")
    type: RetentionCueType
    reason: str
    psychological_mechanism: str = Field(
        default="CURIOSITY_GAP",
        description="Descriptive psychological cue label (e.g. CURIOSITY_GAP, ANTICIPATION, CONTRAST, STAKES, NOVELTY, PREDICTION_ERROR, PAYOFF, CALLBACK)",
    )



class ScriptRetentionReport(BaseModel):
    """Heuristic quality report assessing narrative retention and pacing.

    NOTE: These scores are internal heuristics, NOT real or predicted YouTube analytics.
    """

    passed: bool = Field(description="Whether script satisfies retention quality gate")
    hook_quality_score: float = Field(ge=0.0, le=1.0, description="Hook retention heuristic score")
    progression_score: float = Field(ge=0.0, le=1.0, description="Narrative progression heuristic score")
    payoff_alignment_score: float = Field(ge=0.0, le=1.0, description="Promise vs payoff alignment heuristic score")
    drop_risks: List[DropRisk] = Field(default_factory=list)
    open_loops: List[OpenLoopAudit] = Field(default_factory=list)
    strongest_moments: List[RetentionMoment] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    rewrite_instructions: List[str] = Field(default_factory=list)

    def populate_timestamps(
        self,
        total_duration_seconds: float,
        timing_events: Optional[List[Dict[str, Any]]] = None,
        canonical_narration: Optional[str] = None,
    ) -> "ScriptRetentionReport":
        """Populate actual timestamps on retention moments after real TTS."""
        self.strongest_moments = map_retention_moments_to_timestamps(
            moments=self.strongest_moments,
            total_duration_seconds=total_duration_seconds,
            timing_events=timing_events,
            canonical_narration=canonical_narration,
        )
        return self


class ScriptRetentionEvaluator:
    """Evaluates script text for hook strength, narrative progression, drop risks, and loop resolution."""

    STOP_WORDS = {
        "the", "and", "that", "this", "with", "from", "your", "have", "has", "had",
        "are", "was", "were", "been", "being", "for", "not", "but", "also", "make",
        "can", "will", "would", "should", "could", "all", "any", "some", "our", "out",
        "how", "what", "when", "where", "who", "which", "why", "into", "more", "most",
        "about", "over", "under", "such", "there", "then", "just", "very",
    }

    @classmethod
    def _tokenize(cls, text: str) -> List[str]:
        words = re.findall(r"\b\w+\b", text.lower())
        return [w for w in words if len(w) > 2 and w not in cls.STOP_WORDS]

    def evaluate(
        self,
        script: Script,
        blueprint: Optional[RetentionBlueprint] = None,
    ) -> ScriptRetentionReport:
        """Run heuristic retention QA across the script."""
        scenes = script.scenes or []
        hook_text = (script.hook or "").strip()
        cta_text = ""
        if script.sections and script.sections.cta:
            cta_text = script.sections.cta.strip()
        elif scenes and "cta" in scenes[-1].narration.lower():
            cta_text = scenes[-1].narration.strip()

        drop_risks: List[DropRisk] = []
        open_loops: List[OpenLoopAudit] = []
        strongest_moments: List[RetentionMoment] = []
        issues: List[str] = []
        rewrite_instructions: List[str] = []

        total_scenes = max(1, len(scenes))
        total_duration = max(5.0, script.estimated_duration_seconds or sum(s.target_duration_seconds for s in scenes) or 40.0)

        # 1. Hook Quality Assessment
        hook_words = re.findall(r"\b\w+\b", hook_text)
        hook_word_count = len(hook_words)
        hook_score = 0.90

        # Generic intro check
        generic_patterns = [
            r"\bin this video\b",
            r"\btoday we('ll| will)\b",
            r"\bwelcome back\b",
            r"\blet's talk about\b",
            r"\bhave you ever wondered\b",
            r"\bso today\b",
        ]
        for pat in generic_patterns:
            if re.search(pat, hook_text.lower()):
                hook_score -= 0.40
                issues.append("GENERIC_INTRO: Hook uses boring textbook greeting.")
                drop_risks.append(
                    DropRisk(
                        start_ratio=0.0,
                        end_ratio=0.10,
                        reason="Generic greeting causes immediate 3-second viewer drop-off",
                        severity="HIGH",
                        rewrite_hint="Replace generic opener with in-medias-res conflict or paradox.",
                    )
                )
                rewrite_instructions.append("Cut generic opener. Start directly with the core conflict or paradox.")
                break

        # Hook length check
        max_hook_words = 22 if total_duration <= 60.0 else 30
        if hook_word_count > max_hook_words:
            hook_score -= 0.25
            issues.append(f"HOOK_TOO_LONG: Opening hook is {hook_word_count} words (max {max_hook_words}).")
            drop_risks.append(
                DropRisk(
                    start_ratio=0.0,
                    end_ratio=0.15,
                    reason=f"Hook is too verbose ({hook_word_count} words), viewer will swipe away",
                    severity="MEDIUM",
                    rewrite_hint="Trim hook to punchy 8-16 words delivered in under 4 seconds.",
                )
            )
            rewrite_instructions.append("Tighten the opening hook into one crisp, high-urgency sentence.")
        elif hook_word_count < 5:
            hook_score -= 0.30
            issues.append("HOOK_TOO_SHORT: Opening hook lacks substance.")
            drop_risks.append(
                DropRisk(
                    start_ratio=0.0,
                    end_ratio=0.10,
                    reason="Hook is too brief to establish a meaningful question",
                    severity="LOW",
                )
            )

        if hook_score >= 0.75:
            strongest_moments.append(
                RetentionMoment(
                    position_ratio=0.05,
                    timestamp_seconds=None,
                    type=RetentionCueType.OPEN_LOOP,
                    reason="Crisp opening hook establishes immediate curiosity gap",
                    psychological_mechanism="CURIOSITY_GAP",
                )
            )

        # 2. Open Loop Tracking & Payoff Alignment
        payoff_score = 0.90
        promised_payoff = blueprint.promised_payoff if blueprint else ""
        hook_promise = blueprint.hook.promise if blueprint else ""

        # Identify central question from blueprint or hook
        core_q = blueprint.core_question if blueprint else f"What is the resolution to {hook_text[:40]}?"
        loop_audit = OpenLoopAudit(
            question=core_q,
            opened_at_ratio=0.05,
            closed_at_ratio=None,
            resolved=False,
        )

        # Search for resolution in the final scenes (last 35% of video)
        end_start_idx = max(0, int(total_scenes * 0.65))
        ending_text = " ".join(s.narration for s in scenes[end_start_idx:])
        ending_tokens = set(self._tokenize(ending_text))

        promise_tokens = set(self._tokenize(promised_payoff or hook_promise or hook_text))
        overlap = ending_tokens.intersection(promise_tokens)

        # Does the ending actually resolve the promise?
        if promise_tokens and len(overlap) == 0:
            payoff_score -= 0.50
            issues.append("HOOK_PROMISE_NOT_RESOLVED: Ending scenes do not resolve opening hook promise.")
            drop_risks.append(
                DropRisk(
                    start_ratio=0.75,
                    end_ratio=1.0,
                    reason="Viewer feels betrayed: opening promise is never resolved in payoff",
                    severity="HIGH",
                    rewrite_hint="Rewrite final scene to explicitly deliver the promised insight.",
                )
            )
            rewrite_instructions.append(
                f"Ensure the final scene explicitly delivers on the hook promise: '{promised_payoff or hook_promise}'."
            )
        else:
            loop_audit.resolved = True
            loop_audit.closed_at_ratio = 0.90
            strongest_moments.append(
                RetentionMoment(
                    position_ratio=0.88,
                    timestamp_seconds=None,
                    type=RetentionCueType.LOOP_CLOSE,
                    reason="Ending cleanly resolves core question and delivers payoff",
                    psychological_mechanism="PAYOFF",
                )
            )

        open_loops.append(loop_audit)

        # Check for genuine mid-script retention moments (anchors, contrast, escalation, prediction error)
        for idx, s in enumerate(scenes):
            s_lower = s.narration.lower()
            s_ratio = round((idx + 0.5) / total_scenes, 2)
            if s_ratio <= 0.15 or s_ratio >= 0.85:
                continue

            if any(abs(m.position_ratio - s_ratio) < 0.08 for m in strongest_moments):
                continue

            if any(k in s_lower for k in ["think of it like", "imagine a", "analogy", "mental model"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REVEAL,
                        reason=f"Intuitive analogy in Scene {idx+1} bridges abstract theory into concrete understanding",
                        psychological_mechanism=PsychologicalMechanism.NOVELTY.value,
                    )
                )
            elif any(k in s_lower for k in ["for example", "for instance", "in practice", "observed in production"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REVEAL,
                        reason=f"Concrete real-world example in Scene {idx+1} grounds technical mechanics",
                        psychological_mechanism=PsychologicalMechanism.CONTRAST.value,
                    )
                )
            elif any(k in s_lower for k in ["compared to", "in contrast", "versus", "unlike", "trade-off", "tradeoff"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.PATTERN_INTERRUPT,
                        reason=f"Decisive comparison in Scene {idx+1} creates sharp technical contrast",
                        psychological_mechanism=PsychologicalMechanism.CONTRAST.value,
                    )
                )
            elif any(k in s_lower for k in ["bottleneck", "flaw", "fails", "crashing", "danger", "deadlock"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.ESCALATION,
                        reason=f"Failure mode escalation in Scene {idx+1} raises technical stakes",
                        psychological_mechanism=PsychologicalMechanism.STAKES.value,
                    )
                )
            elif any(k in s_lower for k in ["surprisingly", "counterintuitive", "actually", "in reality", "unexpected"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REVEAL,
                        reason=f"Surprising insight in Scene {idx+1} delivers prediction error",
                        psychological_mechanism=PsychologicalMechanism.PREDICTION_ERROR.value,
                    )
                )
            elif any(k in s_lower for k in ["next", "what happens when", "here is the catch", "stay tuned"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REHOOK,
                        reason=f"Pacing rehook in Scene {idx+1} creates forward anticipation",
                        psychological_mechanism=PsychologicalMechanism.ANTICIPATION.value,
                    )
                )
            elif any(k in s_lower for k in ["remember", "as we saw", "earlier", "circling back"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.LOOP_CLOSE,
                        reason=f"Thematic callback in Scene {idx+1} reinforces core concepts",
                        psychological_mechanism=PsychologicalMechanism.CALLBACK.value,
                    )
                )

        # 3. Narrative Progression & Exposition Pacing
        progression_score = 0.90
        cur_ratio = 0.0

        for idx, s in enumerate(scenes):
            scene_ratio = (idx + 0.5) / total_scenes
            words = re.findall(r"\b\w+\b", s.narration)
            w_count = len(words)

            # Check for overly dense, abstract exposition
            is_dense = w_count > 38 and not any(k in s.narration.lower() for k in ["for example", "watch", "notice", "here", "because", "instead", "then"])
            if is_dense:
                progression_score -= 0.15
                issues.append(f"LONG_EXPOSITION_BLOCK: Scene {idx+1} has {w_count} words of dense narration.")
                drop_risks.append(
                    DropRisk(
                        start_ratio=round(idx / total_scenes, 2),
                        end_ratio=round((idx + 1) / total_scenes, 2),
                        reason="Monotonous monologue without visual contrast causes retention cliff",
                        severity="MEDIUM",
                        rewrite_hint="Break complex exposition into a punchy cause-and-effect demonstration.",
                    )
                )

            # Check consecutive scenes for idea repetition
            if idx > 0:
                prev_tokens = set(self._tokenize(scenes[idx - 1].narration))
                curr_tokens = set(self._tokenize(s.narration))
                if prev_tokens and curr_tokens:
                    jaccard = len(prev_tokens.intersection(curr_tokens)) / len(prev_tokens.union(curr_tokens))
                    if jaccard > 0.65:
                        progression_score -= 0.20
                        issues.append(f"REPEATED_IDEA_WITHOUT_ESCALATION: Scenes {idx} and {idx+1} repeat the same concept.")
                        drop_risks.append(
                            DropRisk(
                                start_ratio=round((idx - 1) / total_scenes, 2),
                                end_ratio=round((idx + 1) / total_scenes, 2),
                                reason="Narrative stall: repeating identical point instead of escalating",
                                severity="MEDIUM",
                                rewrite_hint="Escalate to consequences or mechanisms rather than reiterating.",
                            )
                        )

        # 3b. Concrete Anchor Requirement for Explanation-Heavy Formats
        content_format = getattr(script, "content_format", ContentFormat.EXPLAINER)
        if blueprint and getattr(blueprint, "content_format", None):
            content_format = blueprint.content_format

        is_explanation_heavy = content_format in (
            ContentFormat.EXPLAINER,
            ContentFormat.BREAKDOWN,
        )
        anchor_patterns = {
            ConcreteAnchorType.REAL_EXAMPLE: r"\b(for example|for instance|in practice|real-world|such as|take the case|case of|observed in production)\b",
            ConcreteAnchorType.ANALOGY: r"\b(think of it like|imagine a|analogy|similar to|like a|metaphor|acts like|works like|mental model)\b",
            ConcreteAnchorType.COMPARISON: r"\b(compared to|versus|in contrast|unlike|trade-off|tradeoff|difference between|diverges from)\b",
            ConcreteAnchorType.MINI_CASE: r"\b(post-mortem|incident|outage|production bug|failure event|when engineers at)\b",
            ConcreteAnchorType.DEMONSTRATION: r"\b(run this|terminal|watch what happens|inspecting the output|benchmark shows|output console|code snippet|demonstration)\b",
        }
        all_narration = " ".join(s.narration for s in scenes).lower()
        all_visuals = " ".join(getattr(s, "visual_prompt", "") for s in scenes).lower()
        combined_text = f"{all_narration} {all_visuals}"

        found_anchors = []
        for a_type, pat in anchor_patterns.items():
            if re.search(pat, combined_text):
                found_anchors.append(a_type)

        if is_explanation_heavy and total_duration >= 30.0 and not found_anchors:
            progression_score -= 0.15
            issues.append(
                "MISSING_CONCRETE_ANCHOR: Explanation-heavy script lacks concrete anchors (example, analogy, comparison, mini-case, or demonstration)."
            )
            drop_risks.append(
                DropRisk(
                    start_ratio=0.20,
                    end_ratio=0.75,
                    reason="Pure abstract exposition without concrete examples, analogies, or demos causes retention cliff",
                    severity="MEDIUM",
                    rewrite_hint="Anchor abstract concepts with a concrete analogy, real-world comparison, or practical demonstration.",
                )
            )
            rewrite_instructions.append("Introduce at least one grounded analogy, real-world example, or practical comparison.")

        # 4. CTA Timing & Value-Linked Placement
        if cta_text:
            cta_words = re.findall(r"\b\w+\b", cta_text)
            cta_lower = cta_text.lower().strip()

            # Check if CTA is placed before the climax / payoff
            if total_scenes > 2:
                for idx, s in enumerate(scenes[:-1]):
                    if re.search(r"\b(subscribe|like and subscribe|follow for more|comment below)\b", s.narration.lower()):
                        issues.append(f"CTA_BEFORE_PAYOFF: Premature call to action detected in Scene {idx+1}.")
                        drop_risks.append(
                            DropRisk(
                                start_ratio=round(idx / total_scenes, 2),
                                end_ratio=round((idx + 1) / total_scenes, 2),
                                reason="Premature call to action before delivering payoff drives instant abandonment",
                                severity="HIGH",
                                rewrite_hint="Move all calls-to-action to the absolute final second.",
                            )
                        )
                        rewrite_instructions.append("Remove premature CTA from middle scenes; position CTA only at the end.")
                        break

            # Check for abrupt single-word CTA
            if len(cta_words) < 2:
                issues.append("ABRUPT_CTA: Call to action is abrupt or detached from narrative.")
                drop_risks.append(
                    DropRisk(
                        start_ratio=0.95,
                        end_ratio=1.0,
                        reason="Abrupt one-word CTA feels robotic",
                        severity="LOW",
                        rewrite_hint="Frame CTA as natural teaser for the next episode or deeper exploration.",
                    )
                )

            # Value-Linked CTA Check (Reject detached CTAs such as "Like and subscribe.")
            detached_cta_patterns = [
                r"^(please\s+)?(like and subscribe|subscribe for more|subscribe to (the |our |my )?channel|don't forget to like and subscribe|leave a like and subscribe|hit subscribe|smash that like button|subscribe)\.?$",
            ]
            is_detached_syntax = any(re.match(p, cta_lower) for p in detached_cta_patterns)

            value_link_keywords = [
                "next", "breakdown", "trade-off", "tradeoff", "bottleneck", "part", "episode",
                "dive", "explore", "solve", "learn", "avoid", "benchmark", "because", "question",
                "solution", "checkpoint", "series", "look at",
            ]
            has_value_keyword = any(k in cta_lower for k in value_link_keywords)
            cta_tokens = set(self._tokenize(cta_text))
            has_domain_overlap = bool(cta_tokens.intersection(promise_tokens or ending_tokens))

            if is_detached_syntax or (not has_value_keyword and not has_domain_overlap and len(cta_words) <= 7):
                progression_score -= 0.15
                issues.append(
                    "CTA_NOT_LINKED_TO_VALUE: Call to action is detached from delivered value and lacks value-driven payoff, next question, or series continuation."
                )
                drop_risks.append(
                    DropRisk(
                        start_ratio=0.92,
                        end_ratio=1.0,
                        reason="Detached CTA ('Like and subscribe') provides no viewer incentive; must flow from delivered value",
                        severity="MEDIUM",
                        rewrite_hint="Link CTA directly to the next unresolved question, delivered payoff, or series breakdown.",
                    )
                )
                rewrite_instructions.append("Rewrite CTA to flow directly from the delivered payoff or tease the next breakdown.")

        # 5. Clickbait Sludge Check (excessive fake dramatic phrases)
        sludge_patterns = [
            r"\byou won't believe\b",
            r"\bwait until the end\b",
            r"\bshocking truth\b",
            r"\bthis changes everything\b",
            r"\bmind blown\b",
        ]
        full_text = script.get_canonical_narration().lower()
        sludge_count = sum(len(re.findall(p, full_text)) for p in sludge_patterns)
        if sludge_count >= 2:
            hook_score -= 0.30
            progression_score -= 0.20
            issues.append("CLICKBAIT_SLUDGE: Multiple cheap dramatic tease tropes detected.")
            drop_risks.append(
                DropRisk(
                    start_ratio=0.0,
                    end_ratio=0.50,
                    reason="Excessive fake dramatic phrases damage credibility and viewer trust",
                    severity="HIGH",
                    rewrite_hint="Replace dramatic teases with concrete technical facts.",
                )
            )
        # Expose exactly the strongest top 3 moments when at least 3 exist.
        # If fewer than 3 genuinely strong moments exist, do NOT fabricate them; return fewer and report the weakness.
        strongest_moments.sort(key=lambda m: m.position_ratio)
        if len(strongest_moments) >= 3:
            if len(strongest_moments) > 3:
                first_m = strongest_moments[0]
                last_m = strongest_moments[-1]
                mid_candidates = strongest_moments[1:-1]
                best_mid = min(mid_candidates, key=lambda m: abs(m.position_ratio - 0.5))
                strongest_moments = [first_m, best_mid, last_m]
        else:
            issues.append(
                f"FEW_RETENTION_MOMENTS: Script contains only {len(strongest_moments)} verified retention moment(s) (target is at least 3)."
            )

        # Normalize final scores
        final_hook_score = max(0.0, min(1.0, round(hook_score, 2)))
        final_prog_score = max(0.0, min(1.0, round(progression_score, 2)))
        final_payoff_score = max(0.0, min(1.0, round(payoff_score, 2)))

        has_high_drop_risk = any(r.severity == "HIGH" for r in drop_risks)
        passed = (
            final_hook_score >= 0.65
            and final_prog_score >= 0.60
            and final_payoff_score >= 0.65
            and not has_high_drop_risk
        )

        return ScriptRetentionReport(
            passed=passed,
            hook_quality_score=final_hook_score,
            progression_score=final_prog_score,
            payoff_alignment_score=final_payoff_score,
            drop_risks=drop_risks,
            open_loops=open_loops,
            strongest_moments=strongest_moments,
            issues=issues,
            rewrite_instructions=rewrite_instructions,
        )

    @staticmethod
    def map_moment_timestamps(
        report: ScriptRetentionReport,
        total_duration_seconds: float,
        timing_events: Optional[List[Dict[str, Any]]] = None,
        canonical_narration: Optional[str] = None,
    ) -> ScriptRetentionReport:
        """Map retention moments to actual timestamps after real TTS."""
        return report.populate_timestamps(
            total_duration_seconds=total_duration_seconds,
            timing_events=timing_events,
            canonical_narration=canonical_narration,
        )


def map_retention_moments_to_timestamps(
    moments: List[RetentionMoment],
    total_duration_seconds: float,
    timing_events: Optional[List[Dict[str, Any]]] = None,
    canonical_narration: Optional[str] = None,
) -> List[RetentionMoment]:
    """Map retention moments to actual timestamps after real TTS audio duration and word boundaries."""
    if total_duration_seconds <= 0.0 or not moments:
        return moments

    updated_moments: List[RetentionMoment] = []
    for m in moments:
        matched_time = None

        # Try to match key phrase/words from moment reason against timing words
        if timing_events:
            reason_words = [
                w
                for w in re.findall(r"\b\w+\b", m.reason.lower())
                if len(w) > 3 and w not in ScriptRetentionEvaluator.STOP_WORDS
            ]
            for rw in reason_words:
                for evt in timing_events:
                    w_text = (evt.get("word") or evt.get("text") or "").strip().lower()
                    w_clean = re.sub(r"[^\w\s]", "", w_text)
                    if w_clean == rw:
                        if "start" in evt:
                            matched_time = float(evt["start"])
                        elif "offset" in evt:
                            matched_time = round(float(evt["offset"]) / 10_000_000.0, 3)
                        break
                if matched_time is not None:
                    break

        # Ratio-based calculation fallback
        if matched_time is None:
            matched_time = round(m.position_ratio * total_duration_seconds, 3)

        clamped_time = max(0.0, min(total_duration_seconds, matched_time))
        updated_moments.append(
            RetentionMoment(
                position_ratio=m.position_ratio,
                timestamp_seconds=clamped_time,
                type=m.type,
                reason=m.reason,
                psychological_mechanism=m.psychological_mechanism,
            )
        )
    return updated_moments
