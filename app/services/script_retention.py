"""Format-aware narrative retention evaluation and pacing audits for YouTube scripts."""

import re
from typing import Any, Dict, List, Optional, Tuple

from app.domain.enums import (
    ClaimVerificationVerdict,
    ConcreteAnchorType,
    ContentFormat,
    PsychologicalMechanism,
    RetentionCueType,
)
from app.domain.models import RetentionBlueprint, Script, ScriptSections
from app.domain.retention import (
    ConcreteAnchorAudit,
    DropRisk,
    OpenLoopAudit,
    RetentionMoment,
    ScriptRetentionReport,
    map_retention_moments_to_timestamps,
)


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

    @classmethod
    def _validate_grounding(
        cls,
        scene_text: str,
        dossier: Optional[Any] = None,
        fact_report: Optional[Any] = None,
    ) -> Tuple[bool, List[str], List[str]]:
        """Validate whether an anchor is grounded against verified claims or dossier sources."""
        scene_tokens = set(cls._tokenize(scene_text))
        matched_claim_ids: List[str] = []
        matched_source_refs: List[str] = []

        # 1. FactCheckReport claims
        if fact_report and hasattr(fact_report, "claims"):
            for c in fact_report.claims:
                is_ver = getattr(c, "verified", False) or getattr(c, "verdict", None) in (
                    ClaimVerificationVerdict.VERIFIED,
                    "VERIFIED",
                )
                if not is_ver:
                    continue
                c_stmt = getattr(c, "statement", "")
                c_tokens = set(cls._tokenize(c_stmt))
                overlap = scene_tokens.intersection(c_tokens)
                if len(overlap) >= 2 or (c_stmt.lower() in scene_text.lower() and len(c_tokens) > 0):
                    cid = getattr(c, "id", f"claim-{len(matched_claim_ids)}")
                    if cid not in matched_claim_ids:
                        matched_claim_ids.append(cid)
                    cited_url = getattr(c, "cited_url", None)
                    source_id = getattr(c, "source_id", None)
                    if cited_url and cited_url not in matched_source_refs:
                        matched_source_refs.append(cited_url)
                    elif source_id and source_id not in matched_source_refs:
                        matched_source_refs.append(source_id)

        # 2. ResearchDossier claims & sources
        if dossier:
            for c in getattr(dossier, "claims", []):
                is_ver = getattr(c, "verified", False) or getattr(c, "verdict", None) in (
                    ClaimVerificationVerdict.VERIFIED,
                    "VERIFIED",
                )
                if is_ver:
                    c_stmt = getattr(c, "statement", "")
                    c_tokens = set(cls._tokenize(c_stmt))
                    overlap = scene_tokens.intersection(c_tokens)
                    if len(overlap) >= 2 or (c_stmt.lower() in scene_text.lower() and len(c_tokens) > 0):
                        cid = getattr(c, "id", f"dossier-claim-{len(matched_claim_ids)}")
                        if cid not in matched_claim_ids:
                            matched_claim_ids.append(cid)
                        cited_url = getattr(c, "cited_url", None)
                        source_id = getattr(c, "source_id", None)
                        if cited_url and cited_url not in matched_source_refs:
                            matched_source_refs.append(cited_url)
                        elif source_id and source_id not in matched_source_refs:
                            matched_source_refs.append(source_id)

            for s in getattr(dossier, "sources", []):
                s_url = getattr(s, "url", "")
                s_title = getattr(s, "title", "")
                s_snapshot = getattr(s, "content_snapshot", "")
                s_tokens = set(cls._tokenize(f"{s_title} {s_snapshot[:300]}"))
                overlap = scene_tokens.intersection(s_tokens)
                if len(overlap) >= 3 or (s_title and s_title.lower() in scene_text.lower() and len(s_tokens) > 0):
                    if s_url and s_url not in matched_source_refs:
                        matched_source_refs.append(s_url)

        grounded = bool(matched_claim_ids or matched_source_refs)
        return grounded, matched_claim_ids, matched_source_refs

    def evaluate(
        self,
        script: Script,
        blueprint: Optional[RetentionBlueprint] = None,
        dossier: Optional[Any] = None,
        fact_report: Optional[Any] = None,
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
                    psychological_mechanism=PsychologicalMechanism.CURIOSITY_GAP,
                    narration_anchor=hook_text,
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
            payoff_anchor = scenes[-1].narration.strip() if scenes else (cta_text or hook_text)
            strongest_moments.append(
                RetentionMoment(
                    position_ratio=0.88,
                    timestamp_seconds=None,
                    type=RetentionCueType.LOOP_CLOSE,
                    reason="Ending cleanly resolves core question and delivers payoff",
                    psychological_mechanism=PsychologicalMechanism.PAYOFF,
                    narration_anchor=payoff_anchor,
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

            if re.search(r"\b(think of (it|this|\w+)?\s*like|imagine (a|an)?|analogy|similar to|like (a|an)\b|metaphor|acts like|works like|mental model)\b", s_lower):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REVEAL,
                        reason=f"Intuitive analogy in Scene {idx+1} bridges abstract theory into concrete understanding",
                        psychological_mechanism=PsychologicalMechanism.NOVELTY,
                        narration_anchor=s.narration.strip(),
                    )
                )
            elif any(k in s_lower for k in ["for example", "for instance", "in practice", "observed in production"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REVEAL,
                        reason=f"Concrete real-world example in Scene {idx+1} grounds technical mechanics",
                        psychological_mechanism=PsychologicalMechanism.CONTRAST,
                        narration_anchor=s.narration.strip(),
                    )
                )
            elif any(k in s_lower for k in ["compared to", "in contrast", "versus", "unlike", "trade-off", "tradeoff"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.PATTERN_INTERRUPT,
                        reason=f"Decisive comparison in Scene {idx+1} creates sharp technical contrast",
                        psychological_mechanism=PsychologicalMechanism.CONTRAST,
                        narration_anchor=s.narration.strip(),
                    )
                )
            elif any(k in s_lower for k in ["bottleneck", "flaw", "fails", "crashing", "danger", "deadlock"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.ESCALATION,
                        reason=f"Failure mode escalation in Scene {idx+1} raises technical stakes",
                        psychological_mechanism=PsychologicalMechanism.STAKES,
                        narration_anchor=s.narration.strip(),
                    )
                )
            elif any(k in s_lower for k in ["surprisingly", "counterintuitive", "actually", "in reality", "unexpected"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REVEAL,
                        reason=f"Surprising insight in Scene {idx+1} delivers prediction error",
                        psychological_mechanism=PsychologicalMechanism.PREDICTION_ERROR,
                        narration_anchor=s.narration.strip(),
                    )
                )
            elif any(k in s_lower for k in ["next", "what happens when", "here is the catch", "stay tuned"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.REHOOK,
                        reason=f"Pacing rehook in Scene {idx+1} creates forward anticipation",
                        psychological_mechanism=PsychologicalMechanism.ANTICIPATION,
                        narration_anchor=s.narration.strip(),
                    )
                )
            elif any(k in s_lower for k in ["remember", "as we saw", "earlier", "circling back"]):
                strongest_moments.append(
                    RetentionMoment(
                        position_ratio=s_ratio,
                        timestamp_seconds=None,
                        type=RetentionCueType.LOOP_CLOSE,
                        reason=f"Thematic callback in Scene {idx+1} reinforces core concepts",
                        psychological_mechanism=PsychologicalMechanism.CALLBACK,
                        narration_anchor=s.narration.strip(),
                    )
                )

        # 3. Narrative Progression & Exposition Pacing
        progression_score = 0.90

        for idx, s in enumerate(scenes):
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

        concrete_anchors: List[ConcreteAnchorAudit] = []
        anchor_patterns = [
            (ConcreteAnchorType.ANALOGY, r"\b(think of (it|this|\w+)?\s*like|imagine (a|an)?|analogy|similar to|like (a|an)\b|metaphor|acts like|works like|mental model)\b"),
            (ConcreteAnchorType.MINI_CASE, r"\b(post-mortem|incident|outage|production bug|failure event|when engineers at)\b"),
            (ConcreteAnchorType.DEMONSTRATION, r"\b(run this|terminal|watch what happens|inspecting the output|benchmark shows|output console|code snippet|demonstration)\b"),
            (ConcreteAnchorType.COMPARISON, r"\b(compared to|versus|in contrast|unlike|trade-off|tradeoff|difference between|diverges from)\b"),
            (ConcreteAnchorType.REAL_EXAMPLE, r"\b(for example|for instance|in practice|real-world|such as|take the case|case of|observed in production)\b"),
        ]

        for s_idx, s in enumerate(scenes):
            s_narration = s.narration or ""
            s_lower = s_narration.lower()
            s_vis = (getattr(s, "visual_prompt", "") or "").lower()
            combined = f"{s_lower} {s_vis}"

            for a_type, pat in anchor_patterns:
                m = re.search(pat, combined)
                if not m:
                    continue

                sentences = re.split(r"(?<=[.!?])\s+", s_narration)
                anchor_snippet = s_narration
                for sent in sentences:
                    if re.search(pat, sent.lower()):
                        anchor_snippet = sent.strip()
                        break

                grounded = False
                matched_claim_ids: List[str] = []
                matched_source_refs: List[str] = []

                if a_type == ConcreteAnchorType.ANALOGY:
                    # Conceptual explanatory analogy does not require external source
                    grounded = True
                elif a_type == ConcreteAnchorType.COMPARISON:
                    # If purely conceptual: illustrative -> grounded = True
                    # If empirical metrics: must map to verified evidence
                    is_empirical = bool(re.search(r"\b(\d+x|\d+%\s*faster|\d+ms|latency|throughput|benchmark)\b", s_lower))
                    if not is_empirical:
                        grounded = True
                    else:
                        grounded, matched_claim_ids, matched_source_refs = self._validate_grounding(
                            scene_text=s_narration,
                            dossier=dossier,
                            fact_report=fact_report,
                        )
                elif a_type == ConcreteAnchorType.DEMONSTRATION:
                    # If claiming empirical result: must map to verified evidence
                    claims_empirical = bool(re.search(r"\b(benchmark shows|\d+x|\d+%\s*(faster|reduction|improvement)|speedup)\b", s_lower))
                    if not claims_empirical:
                        grounded = True
                    else:
                        grounded, matched_claim_ids, matched_source_refs = self._validate_grounding(
                            scene_text=s_narration,
                            dossier=dossier,
                            fact_report=fact_report,
                        )
                elif a_type in (ConcreteAnchorType.REAL_EXAMPLE, ConcreteAnchorType.MINI_CASE):
                    # Must map to verified Claim and/or ResearchSource
                    grounded, matched_claim_ids, matched_source_refs = self._validate_grounding(
                        scene_text=s_narration,
                        dossier=dossier,
                        fact_report=fact_report,
                    )

                concrete_anchors.append(
                    ConcreteAnchorAudit(
                        anchor_type=a_type,
                        scene_index=s_idx,
                        text=anchor_snippet,
                        grounded=grounded,
                        claim_ids=matched_claim_ids,
                        source_refs=matched_source_refs,
                    )
                )

        grounded_anchors = [a for a in concrete_anchors if a.grounded]

        if is_explanation_heavy and total_duration >= 30.0 and not grounded_anchors:
            progression_score -= 0.15
            issues.append(
                "MISSING_CONCRETE_ANCHOR: Explanation-heavy script lacks grounded concrete anchors (grounded example, analogy, comparison, mini-case, or demonstration)."
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
            concrete_anchors=concrete_anchors,
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
