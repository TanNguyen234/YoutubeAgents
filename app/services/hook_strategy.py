"""Hook strategy and tournament service generating, evaluating, and selecting grounded video hooks."""

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.enums import ClaimVerificationVerdict, ContentFormat, HookAngle
from app.domain.models import (
    Channel,
    FactCheckReport,
    HookCandidate,
    HookEvaluation,
    ResearchDossier,
    VideoCreativeBrief,
)


class HookTournamentError(RuntimeError):
    """Raised when hook tournament generation or evaluation fails."""
    pass


def normalize_hook_text(text: str) -> str:
    """Normalize hook text by lowercasing and removing non-word characters and whitespace."""
    clean = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(clean.split())


class HookCandidatesPayload(BaseModel):
    """Structured LLM response containing generated hook candidates."""

    candidates: List[HookCandidate] = Field(default_factory=list)


class HookTournamentService:
    """Orchestrates hook generation across diverse psychological angles and conducts a grounded tournament."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def generate_hook_candidates(
        self,
        topic: str,
        dossier: ResearchDossier,
        brief: VideoCreativeBrief,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        channel: Optional[Channel] = None,
        candidate_count: int = 5,
    ) -> List[HookCandidate]:
        """Generate distinct grounded hook candidates covering different psychological angles."""
        sources_summary = "\n".join(
            f"- {s.title} ({s.url}): {s.content_snapshot[:600] if s.content_snapshot else 'No snapshot'}"
            for s in dossier.sources
        )

        angles_to_cover = [
            HookAngle.CURIOSITY_GAP,
            HookAngle.PAIN_POINT,
            HookAngle.CONTRARIAN,
            HookAngle.RESULT_FIRST,
            HookAngle.STAKES_FIRST,
        ][:candidate_count]

        prompt = f"""You are an expert YouTube retention strategist crafting high-CTR, high-retention video hooks for '{channel.title if channel else 'Tech Channel'}'.
Audience: {channel.target_audience if channel else 'Software engineers and developers'}
Topic: {topic}
Content Format: {content_format.value}
Target Video Duration: {brief.target_duration_seconds}s
Delivery Tone: {brief.tone.value}

GROUND TRUTH RESEARCH EVIDENCE:
{sources_summary}

TASK:
Generate exactly {len(angles_to_cover)} distinct hook candidates. Each hook must address the topic through a different angle:
{', '.join(a.value for a in angles_to_cover)}

CRITICAL HOOK RULES:
1. First 3-5 seconds only: Concise (8 to 18 words).
2. NEVER use generic openers ("In this video", "Welcome back", "Today we will explore", "Have you ever wondered", "Let's talk about").
3. Each candidate must state an explicit or implicit 'promise' describing what the video will definitively teach or reveal.
4. STRICT FACTUAL SAFETY: Do NOT invent ungrounded metrics or percentages. Any quantitative claim MUST come directly from the Research Evidence.

OUTPUT REQUIREMENTS:
Return a JSON list of HookCandidate items with:
- text: Spoken hook text
- angle: HookAngle enum value ({', '.join(a.value for a in angles_to_cover)})
- promise: What payoff the viewer is promised
- required_claim_ids: list of claim IDs if any
"""
        try:
            payload = self.backend.generate_structured(prompt, HookCandidatesPayload)
            if payload and payload.candidates:
                validated_candidates: List[HookCandidate] = []
                seen_angles = set()
                seen_texts = set()
                for c in payload.candidates:
                    norm_text = normalize_hook_text(c.text)
                    if c.angle in angles_to_cover and c.angle not in seen_angles and norm_text not in seen_texts:
                        seen_angles.add(c.angle)
                        seen_texts.add(norm_text)
                        validated_candidates.append(c)

                missing_angles = [a for a in angles_to_cover if a not in seen_angles]
                if missing_angles:
                    fallbacks = self._generate_fallback_candidates(topic, dossier, missing_angles, brief=brief)
                    for fb in fallbacks:
                        norm_fb = normalize_hook_text(fb.text)
                        if norm_fb not in seen_texts:
                            seen_texts.add(norm_fb)
                            validated_candidates.append(fb)

                if len(validated_candidates) >= len(angles_to_cover):
                    return validated_candidates[:len(angles_to_cover)]
        except Exception:
            pass

        # Deterministic grounded fallback covering distinct angles
        return self._generate_fallback_candidates(topic, dossier, angles_to_cover, brief=brief)

    def _generate_fallback_candidates(
        self,
        topic: str,
        dossier: ResearchDossier,
        angles: List[HookAngle],
        brief: Optional[VideoCreativeBrief] = None,
    ) -> List[HookCandidate]:
        """Generate deterministic grounded hook candidates when LLM backend is unavailable."""
        clean_topic = topic.strip().rstrip(".")
        candidates = []

        curiosity_text = f"There is a core mechanism in {clean_topic} that shapes how it actually runs."
        curiosity_promise = f"Unpack the underlying mechanism behind {clean_topic}."

        if brief and brief.common_failure:
            pain_text = f"When {clean_topic} runs into {brief.common_failure}, this behavior surfaces."
            pain_promise = f"Diagnose and resolve {brief.common_failure} in {clean_topic}."
        else:
            pain_text = f"Under demanding workloads, {clean_topic} can run into unexpected behavior."
            pain_promise = f"Diagnose and eliminate unexpected behavior in {clean_topic}."

        if brief and brief.common_misconception:
            contrarian_text = f"While {clean_topic} is often assumed to behave around {brief.common_misconception}, the actual mechanism is different."
            contrarian_promise = f"Clarify the real behavior of {clean_topic}."
        else:
            contrarian_text = f"{clean_topic} behaves differently once this mechanism enters the picture."
            contrarian_promise = f"Explain how this mechanism changes {clean_topic}."

        result_text = f"Here is what happens inside {clean_topic} under real execution."
        result_promise = f"Examine the observed behavior and output of {clean_topic}."

        if brief and brief.common_failure:
            stakes_text = f"How {clean_topic} manages {brief.common_failure} determines whether workloads stay stable."
            stakes_promise = f"Prevent system instability in {clean_topic}."
        else:
            stakes_text = f"This detail can change how {clean_topic} behaves under real workloads."
            stakes_promise = f"Understand how {clean_topic} behaves under real workloads."

        templates = {
            HookAngle.CURIOSITY_GAP: (curiosity_text, curiosity_promise),
            HookAngle.PAIN_POINT: (pain_text, pain_promise),
            HookAngle.CONTRARIAN: (contrarian_text, contrarian_promise),
            HookAngle.RESULT_FIRST: (result_text, result_promise),
            HookAngle.STAKES_FIRST: (stakes_text, stakes_promise),
        }

        for angle in angles:
            text, promise = templates.get(
                angle,
                (f"Here is the essential mechanism in {clean_topic}.", f"Deliver clear mastery of {clean_topic}."),
            )
            candidates.append(
                HookCandidate(
                    text=text,
                    angle=angle,
                    promise=promise,
                    required_claim_ids=[],
                )
            )

        return candidates

    def evaluate_hook(
        self,
        candidate: HookCandidate,
        hook_index: int,
        topic: str,
        dossier: ResearchDossier,
        brief: VideoCreativeBrief,
        fact_report: Optional[FactCheckReport] = None,
    ) -> HookEvaluation:
        """Evaluate a single hook candidate across brevity, clarity, curiosity, relevance, promise, and factual safety."""
        text = candidate.text.strip()
        words = re.findall(r"\b\w+\b", text)
        word_count = len(words)
        penalties: List[str] = []

        # 1. Factual Safety (Deterministic against current research evidence)
        # Note: factual_safe means "no unsupported factual content found against current evidence".
        # It does NOT imply downstream FactCheckReport claim verification has occurred.
        factual_safe = True
        evidence_corpus = " ".join(
            [s.content_snapshot or "" for s in dossier.sources]
            + [s.title for s in dossier.sources]
            + ([c.statement for c in fact_report.claims] if fact_report else [])
        ).lower()

        number_matches = re.findall(r"\b\d+(?:\.\d+)?%?|\b\d+x\b|\$\d+", text, flags=re.IGNORECASE)
        if number_matches:
            for match in number_matches:
                clean_num = match.strip("$%xX")
                if clean_num and clean_num not in evidence_corpus:
                    factual_safe = False
                    penalties.append(f"UNVERIFIED_NUMERIC_CLAIM: '{match}' not grounded in dossier evidence")
                    break

        if candidate.required_claim_ids:
            if not fact_report:
                factual_safe = False
                penalties.append("UNVERIFIED_REQUIRED_CLAIM_IDS_WITHOUT_FACT_REPORT")
            else:
                verified_ids = {
                    c.id for c in fact_report.claims
                    if getattr(c, "verdict", None) == ClaimVerificationVerdict.VERIFIED or getattr(c, "verified", False)
                }
                for cid in candidate.required_claim_ids:
                    if cid not in verified_ids:
                        factual_safe = False
                        penalties.append(f"UNVERIFIED_REQUIRED_CLAIM_ID: '{cid}'")

        # Non-numeric ungrounded hyperbole or universal factual claims
        risky_generalizations = [
            r"\balmost everyone misinterprets\b",
            r"\beveryone misinterprets\b",
            r"\bcompletely backwards\b",
            r"\bcompromise your production\b",
            r"\bcompromise production\b",
            r"\bprimary bottleneck\b",
            r"\bcore flaw\b",
            r"\balways fails\b",
            r"\bmost developers\b",
            r"\beveryone does\b",
            r"\ball developers\b",
        ]
        text_lower = text.lower()
        for pat in risky_generalizations:
            if re.search(pat, text_lower) and not re.search(pat, evidence_corpus):
                factual_safe = False
                penalties.append(f"UNGROUNDED_HYPERBOLIC_CLAIM: '{pat}' not grounded in research evidence")
                break

        # 2. Generic Opener Penalties
        text_lower = text.lower()
        generic_patterns = [
            r"\bin this video\b",
            r"\btoday we('ll| will)\b",
            r"\bwelcome back\b",
            r"\blet's talk about\b",
            r"\bhave you ever wondered\b",
            r"\bso today\b",
        ]
        for pat in generic_patterns:
            if re.search(pat, text_lower):
                penalties.append("GENERIC_OPENER")
                break

        # 3. Brevity Score (ideal 8 - 18 words)
        if word_count < 5:
            brevity_score = 0.4
            penalties.append("HOOK_TOO_SHORT")
        elif word_count <= 18:
            brevity_score = 1.0
        elif word_count <= 24:
            brevity_score = max(0.3, round(1.0 - (word_count - 18) * 0.1, 2))
            penalties.append("HOOK_SLIGHTLY_LONG")
        else:
            brevity_score = 0.2
            penalties.append("HOOK_TOO_LONG")

        # 4. Clarity Score
        passive_matches = len(re.findall(r"\b(is|was|were|been|being)\s+\w+ed\b", text_lower))
        clarity_score = max(0.4, round(1.0 - passive_matches * 0.2, 2))

        # 5. Curiosity Score
        curiosity_bonus = 0.0
        curiosity_keywords = ["why", "how", "secret", "subtle", "flaw", "bottleneck", "backwards", "happens", "mistake", "truth", "silent", "instead"]
        if any(k in text_lower for k in curiosity_keywords):
            curiosity_bonus += 0.25
        if "?" in text:
            curiosity_bonus += 0.15

        angle_base = {
            HookAngle.CURIOSITY_GAP: 0.85,
            HookAngle.CONTRARIAN: 0.85,
            HookAngle.PAIN_POINT: 0.80,
            HookAngle.STAKES_FIRST: 0.80,
            HookAngle.RESULT_FIRST: 0.75,
        }.get(candidate.angle, 0.70)
        curiosity_score = min(1.0, round(angle_base + curiosity_bonus, 2))

        # 6. Relevance Score
        topic_tokens = set(re.findall(r"\b\w{3,}\b", topic.lower()))
        hook_tokens = set(re.findall(r"\b\w{3,}\b", text_lower))
        overlap = len(topic_tokens.intersection(hook_tokens))
        relevance_score = min(1.0, 0.5 + 0.25 * overlap) if topic_tokens else 0.8

        # 7. Promise Alignment Score
        promise_text = candidate.promise.strip()
        if not promise_text or len(promise_text.split()) < 3:
            promise_alignment_score = 0.3
            penalties.append("WEAK_PROMISE")
        else:
            promise_alignment_score = 0.95

        # 8. Composite Total Score
        base_score = (
            0.25 * curiosity_score
            + 0.20 * clarity_score
            + 0.20 * brevity_score
            + 0.15 * relevance_score
            + 0.20 * promise_alignment_score
        )

        penalty_deduction = 0.0
        if "GENERIC_OPENER" in penalties:
            penalty_deduction += 0.40
        if "HOOK_TOO_LONG" in penalties:
            penalty_deduction += 0.25
        if "HOOK_TOO_SHORT" in penalties:
            penalty_deduction += 0.15
        if "WEAK_PROMISE" in penalties:
            penalty_deduction += 0.20

        if not factual_safe:
            total_score = -100.0
        else:
            total_score = max(0.0, round(base_score - penalty_deduction, 3))

        return HookEvaluation(
            hook_index=hook_index,
            brevity_score=brevity_score,
            clarity_score=clarity_score,
            curiosity_score=curiosity_score,
            relevance_score=relevance_score,
            promise_alignment_score=promise_alignment_score,
            factual_safe=factual_safe,
            penalties=penalties,
            total_score=total_score,
        )

    def run_tournament(
        self,
        candidates: List[HookCandidate],
        topic: str,
        dossier: ResearchDossier,
        brief: VideoCreativeBrief,
        fact_report: Optional[FactCheckReport] = None,
    ) -> Tuple[HookCandidate, HookEvaluation, List[HookEvaluation]]:
        """Evaluate all candidates in a tournament and select the factually safe winner with highest retention score."""
        if not candidates:
            raise HookTournamentError("Cannot run tournament with zero hook candidates.")

        # Server-side hook diversity validation
        seen_angles = set()
        seen_texts = set()
        for c in candidates:
            if c.angle in seen_angles:
                raise HookTournamentError(f"Candidate set contains duplicate hook angle: '{c.angle.value}'.")
            seen_angles.add(c.angle)

            norm = normalize_hook_text(c.text)
            if norm in seen_texts:
                raise HookTournamentError(f"Candidate set contains duplicate normalized hook text: '{c.text}'.")
            seen_texts.add(norm)

        evaluations: List[HookEvaluation] = []
        for idx, candidate in enumerate(candidates):
            evaluation = self.evaluate_hook(
                candidate=candidate,
                hook_index=idx,
                topic=topic,
                dossier=dossier,
                brief=brief,
                fact_report=fact_report,
            )
            evaluations.append(evaluation)

        safe_evals = [e for e in evaluations if e.factual_safe]
        if not safe_evals:
            raise HookTournamentError(
                "All hook candidates failed factual safety checks. Grounding requires verified evidence."
            )

        winner_eval = max(safe_evals, key=lambda e: e.total_score)
        winner_candidate = candidates[winner_eval.hook_index]

        return winner_candidate, winner_eval, evaluations
