"""Topic evaluator service generating 8-dimension scores using Antigravity reasoning."""

from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, Field, model_validator

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.models import Channel, TopicScoreBreakdown


class TopicEvaluationOutput(BaseModel):
    """Structured LLM output for multi-dimensional topic scoring."""

    demand: float = Field(ge=0.0, le=10.0, description="Audience demand score (0-10)")
    freshness: float = Field(ge=0.0, le=10.0, description="Timeliness / trend momentum (0-10)")
    competition: float = Field(ge=0.0, le=10.0, description="Market saturation / opportunity (0-10)")
    channel_fit: float = Field(ge=0.0, le=10.0, description="Niche persona alignment (0-10)")
    originality: float = Field(ge=0.0, le=10.0, description="Novelty / unique angle (0-10)")
    evidence_quality: float = Field(ge=0.0, le=10.0, description="Availability of verifiable citations (0-10)")
    production_feasibility: float = Field(ge=0.0, le=10.0, description="Ease of asset sourcing / rendering (0-10)")
    historical_fit: Optional[float] = Field(default=None, ge=0.0, le=10.0, description="Channel historical correlation (0-10)")
    rationale: str = Field(description="Detailed synthesis and reasoning")
    score_reasons: Dict[str, str] = Field(default_factory=dict, description="Brief justification per scored dimension")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "production_feasibility" not in data and "feasibility" in data:
                data["production_feasibility"] = data["feasibility"]
            if "channel_fit" not in data and "fit" in data:
                data["channel_fit"] = data["fit"]
        return data


class TopicEvaluator:
    """Evaluates candidate topics across 8 strategic dimensions using Antigravity reasoning."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def evaluate_topic_with_reasoning(
        self,
        channel: Channel,
        keyword: str,
        evidence_summary: Optional[str] = None,
    ) -> Tuple[Dict[str, Optional[float]], str, Dict[str, str]]:
        """Prompt Antigravity reasoning engine to score a candidate topic across 8 dimensions."""
        prompt = f"""You are an elite YouTube Content Strategist evaluating video topic viability for the channel '{channel.title}'.
Channel Niche: {channel.niche}
Target Audience: {channel.target_audience}
Default Language: {channel.default_language}

TOPIC TO EVALUATE: "{keyword}"
REAL RESEARCH EVIDENCE & CONTEXT:
{evidence_summary or 'No research evidence provided.'}

EVALUATION TASK:
Evaluate the topic on the active dimensions on a scale from 0.0 to 10.0 based on the evidence provided:
1. demand: Audience search appetite and general interest (0.0 to 10.0).
2. freshness: Trend relevance and timeliness (0.0 to 10.0).
3. competition: Market saturation (10.0 = low competition / high blue-ocean opportunity).
4. channel_fit: Persona and brand alignment for {channel.title} (0.0 to 10.0).
5. originality: Uniqueness of perspective (0.0 to 10.0).
6. evidence_quality: Quality and verifiability of technical citations in the evidence (0.0 to 10.0).
7. production_feasibility: Feasibility of producing engaging motion graphics / visuals (0.0 to 10.0).
8. historical_fit: Set to null unless actual historical channel analytics data is provided.

Provide:
- A numerical score (0.0 - 10.0) for dimensions 1 through 7 (and null for historical_fit).
- A concise rationale summarizing the topic opportunity.
- score_reasons: A dictionary explaining the rationale for each dimension score.
"""
        eval_output = self.backend.generate_structured(prompt, TopicEvaluationOutput)
        if isinstance(eval_output, dict):
            eval_output = TopicEvaluationOutput.model_validate(eval_output)

        scores: Dict[str, Optional[float]] = {
            "demand": eval_output.demand,
            "freshness": eval_output.freshness,
            "competition": eval_output.competition,
            "channel_fit": eval_output.channel_fit,
            "originality": eval_output.originality,
            "evidence_quality": eval_output.evidence_quality,
            "production_feasibility": eval_output.production_feasibility,
            "historical_fit": eval_output.historical_fit,
        }
        return scores, eval_output.rationale, eval_output.score_reasons
