"""Script retention evaluator detecting drop risks, open loop resolution, and pacing quality."""

import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.enums import RetentionCueType
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

    position_ratio: float = Field(ge=0.0, le=1.0)
    type: RetentionCueType
    reason: str


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
                    type=RetentionCueType.OPEN_LOOP,
                    reason="Crisp opening hook establishes immediate curiosity gap",
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
                    type=RetentionCueType.LOOP_CLOSE,
                    reason="Ending cleanly resolves core question and delivers payoff",
                )
            )

        open_loops.append(loop_audit)

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

        # 4. CTA Timing & Placement
        if cta_text:
            cta_words = re.findall(r"\b\w+\b", cta_text)
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
            rewrite_instructions.append("Purge hollow clickbait phrases and ground tension in actual technical mechanics.")

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
