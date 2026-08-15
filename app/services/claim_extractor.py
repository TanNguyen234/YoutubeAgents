"""Claim extractor service identifying verifiable factual statements from script voiceover text."""

from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.models import Claim, Script


class ClaimExtractionOutput(BaseModel):
    """Structured extraction of factual assertions from text."""

    claims: List[str] = Field(
        default_factory=list,
        description="Discrete, atomic, verifiable factual claims extracted from the narration text",
    )


class ClaimExtractor:
    """Extracts atomic factual claims from final script voiceover text using Antigravity reasoning."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def extract_from_script(self, script: Script) -> List[Claim]:
        """Extract atomic factual claims from script voiceover text."""
        # Use voiceover text if available or concatenate scene narrations
        if script.sections and script.sections.voiceover_text:
            text_to_audit = script.sections.voiceover_text
        else:
            text_to_audit = f"{script.hook} " + " ".join(scene.narration for scene in script.scenes)

        prompt = f"""You are a precise factual analysis editor.
Analyze the video script narration below and extract 2 to 4 core ATOMIC FACTUAL CLAIMS that require evidence verification.

SCRIPT NARRATION:
"{text_to_audit}"

EXTRACTION RULES:
1. Extract only objective factual assertions (e.g. software architecture, technical mechanisms, statistics, historical facts).
2. DO NOT extract rhetorical questions ("Have you ever wondered?"), opinions, greetings, or call-to-actions ("Subscribe!").
3. Keep each claim atomic, precise, and declarative.
"""

        try:
            output = self.backend.generate_structured(prompt, ClaimExtractionOutput)
            claims = []
            for idx, stmt in enumerate(output.claims):
                if stmt.strip():
                    claims.append(
                        Claim(
                            id=f"clm-{script.id}-{idx+1:02d}",
                            statement=stmt.strip(),
                        )
                    )
            return claims
        except Exception:
            # Fallback: create claim from scene narrations if reasoning fails
            fallback_claims = []
            for idx, scene in enumerate(script.scenes):
                if len(scene.narration.split()) >= 5:
                    fallback_claims.append(
                        Claim(
                            id=f"clm-{script.id}-{idx+1:02d}",
                            statement=scene.narration.strip(),
                        )
                    )
            return fallback_claims
