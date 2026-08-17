"""Claim extractor service extracting atomic factual assertions from final narration text."""

import re
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.models import Claim, Script


class ClaimExtractionError(Exception):
    """Raised when claim extraction fails or when script contains empty voiceover."""
    pass


class ClaimExtractionOutput(BaseModel):
    """Structured LLM output for extracted factual claims."""

    claims: List[str] = Field(description="List of atomic, verifiable factual claims extracted from the narration")


class ClaimExtractor:
    """Extracts atomic factual claims from final script voiceover text for fact checking."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def get_audit_text(self, script: Script) -> str:
        """Extract the full contiguous spoken voiceover text to be audited."""
        if hasattr(script, "get_canonical_narration"):
            canonical = script.get_canonical_narration()
            if canonical.strip():
                return canonical.strip()
        if script.sections and getattr(script.sections, "voiceover_text", None) and script.sections.voiceover_text.strip():
            return script.sections.voiceover_text.strip()
        if script.scenes:
            parts = [s.narration for s in script.scenes if s.narration]
            return " ".join(p.strip() for p in parts if p.strip())
        return ""

    def extract_from_script(self, script: Script) -> List[Claim]:
        """Extract atomic factual claims from the final voiceover text of a Script."""
        audit_text = self.get_audit_text(script)
        if not audit_text:
            raise ClaimExtractionError(f"Script '{script.id}' contains empty voiceover and narration text.")

        prompt = f"""You are a Fact-Checking Auditor for short educational video content.
Extract the key, atomic factual claims made in the following video script voiceover for verification against technical sources.

VOICEOVER TEXT TO AUDIT:
"{audit_text}"

INSTRUCTIONS:
1. Extract 3 to 6 complete, distinct factual statements asserting technical facts, features, or behaviors.
2. Do not include pure greetings, rhetorical transitions, or calls to action unless they contain specific factual claims.
3. Keep each claim as a clear, complete, verifiable statement.
"""
        try:
            extraction_output = self.backend.generate_structured(prompt, ClaimExtractionOutput)
            raw_claims = extraction_output.claims
            if not raw_claims:
                raise ValueError("Model returned 0 claims")
        except Exception:
            # Deterministic fallback sentence extraction over the exact same audit text
            raw_sentences = re.split(r'(?<=[.!?])\s+', audit_text)
            raw_claims = [s.strip() for s in raw_sentences if len(s.strip().split()) >= 3]

        if not raw_claims:
            raise ClaimExtractionError(f"Failed to extract any claims from script '{script.id}'.")

        claims = []
        for idx, statement in enumerate(raw_claims):
            claim_id = f"clm-{script.id}-{idx+1:02d}"
            claims.append(Claim(id=claim_id, statement=statement.strip()))

        return claims
