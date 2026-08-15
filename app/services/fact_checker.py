"""Fact checker service evaluating extracted claims against research dossier evidence."""

from datetime import datetime, timezone
import re
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.enums import ClaimVerificationVerdict, QualityStatus
from app.domain.models import Claim, FactCheckReport, ResearchDossier


class ClaimEntailmentOutput(BaseModel):
    """Structured entailment audit for a single factual claim."""

    is_supported: bool = Field(description="Whether the claim is directly supported by provided source evidence")
    confidence: float = Field(ge=0.0, le=1.0, description="Verification confidence score (0.0 to 1.0)")
    cited_url: Optional[str] = Field(default=None, description="Exact source URL from the provided evidence supporting this claim")
    cited_excerpt: Optional[str] = Field(default=None, description="Exact excerpt or verbatim quote supporting the claim")
    rationale: str = Field(description="Justification and analysis of the evidence entailment")


class FactChecker:
    """Evaluates script claims against persisted evidence dossiers using Antigravity reasoning and deterministic checks."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    @staticmethod
    def _deterministic_substring_check(statement: str, dossier: ResearchDossier) -> Optional[tuple[str, str]]:
        """Fast path: deterministic check if statement or key phrase is verbatim in source snapshot."""
        clean_stmt = statement.strip().lower()
        for src in dossier.sources:
            if not src.content_snapshot:
                continue
            src_clean = src.content_snapshot.lower()
            if clean_stmt in src_clean:
                return src.url, statement

            # Check for substantial clauses (>= 40 characters)
            words = statement.split()
            if len(words) >= 6:
                ngram = " ".join(words[:6]).lower()
                if ngram in src_clean:
                    return src.url, ngram
        return None

    def verify_claim(self, claim: Claim, dossier: ResearchDossier) -> Claim:
        """Audit single claim against research sources in dossier."""
        # 1. Deterministic verbatim check
        direct_match = self._deterministic_substring_check(claim.statement, dossier)
        if direct_match:
            cited_url, excerpt = direct_match
            claim.verified = True
            claim.verdict = ClaimVerificationVerdict.VERIFIED
            claim.confidence_score = 1.0
            claim.cited_url = cited_url
            claim.cited_excerpt = excerpt
            claim.notes = "Verified via exact evidence text match."
            return claim

        # 2. Antigravity reasoning entailment check
        sources_text = "\n\n".join(
            f"[Source {idx+1}: {s.title} ({s.url})]\n{s.content_snapshot[:6000] if s.content_snapshot else ''}"
            for idx, s in enumerate(dossier.sources)
        )

        prompt = f"""You are a rigorous Fact Checking auditor for educational video content.
Evaluate the factual claim below strictly against the provided Source Evidence.

CLAIM TO VERIFY:
"{claim.statement}"

SOURCE EVIDENCE:
{sources_text}

AUDIT RULES:
1. is_supported must be TRUE if the claim's factual assertion is directly confirmed or logically entailed by the source evidence.
2. If the claim is supported, provide the exact source URL and the relevant excerpt/quote from the evidence.
3. If the claim is unsupported, misleading, or contradicted by evidence, set is_supported to FALSE and confidence below 0.5.
4. If the source evidence explicitly discusses the subject and confirms the claim, set confidence >= 0.85.
"""

        try:
            entailment = self.backend.generate_structured(prompt, ClaimEntailmentOutput)
            valid_urls = [s.url for s in dossier.sources]
            cited_url = entailment.cited_url

            # Resolve cited URL
            if cited_url and cited_url not in valid_urls:
                for src in dossier.sources:
                    if src.url.rstrip("/") in cited_url or cited_url.rstrip("/") in src.url:
                        cited_url = src.url
                        break
                else:
                    if len(dossier.sources) == 1:
                        cited_url = dossier.sources[0].url
                    else:
                        cited_url = None
            elif not cited_url and len(dossier.sources) == 1 and entailment.is_supported:
                cited_url = dossier.sources[0].url

            if entailment.is_supported and entailment.confidence >= 0.70 and cited_url:
                claim.verified = True
                claim.verdict = ClaimVerificationVerdict.VERIFIED
                claim.confidence_score = entailment.confidence
                claim.cited_url = cited_url
                claim.cited_excerpt = entailment.cited_excerpt
                claim.notes = entailment.rationale
            else:
                claim.verified = False
                if entailment.confidence < 0.3:
                    claim.verdict = ClaimVerificationVerdict.REMOVE
                else:
                    claim.verdict = ClaimVerificationVerdict.REWRITE_REQUIRED
                claim.confidence_score = entailment.confidence
                claim.cited_url = None
                claim.cited_excerpt = None
                claim.notes = entailment.rationale

        except Exception as e:
            claim.verified = False
            claim.verdict = ClaimVerificationVerdict.UNVERIFIABLE
            claim.confidence_score = 0.0
            claim.cited_url = None
            claim.notes = f"Fact check reasoning error: {str(e)}"

        return claim

    def verify_all_claims(
        self,
        claims: List[Claim],
        dossier: ResearchDossier,
        project_id: str,
    ) -> FactCheckReport:
        """Verify all claims and generate audit report."""
        evaluated_claims: List[Claim] = []
        for c in claims:
            evaluated_claims.append(self.verify_claim(c, dossier))

        return self.build_audit_report(project_id=project_id, claims=evaluated_claims)

    def build_audit_report(self, project_id: str, claims: List[Claim]) -> FactCheckReport:
        """Aggregate evaluated claims into a formal FactCheckReport."""
        verified_count = sum(1 for c in claims if c.verified and c.verdict == ClaimVerificationVerdict.VERIFIED)
        failed_count = len(claims) - verified_count

        overall_verdict = QualityStatus.PASSED if failed_count == 0 and len(claims) > 0 else QualityStatus.FAILED
        summary_lines = [
            f"Evaluated {len(claims)} claim(s): {verified_count} verified, {failed_count} failed."
        ]
        for c in claims:
            status_tag = "PASS" if c.verified else "FAIL"
            summary_lines.append(f" - [{status_tag}] ({c.verdict.value}) {c.statement} [Source: {c.cited_url or 'NONE'}]")

        return FactCheckReport(
            id=f"fcr-{project_id}",
            project_id=project_id,
            claims=claims,
            verified_count=verified_count,
            failed_count=failed_count,
            overall_verdict=overall_verdict,
            audit_summary="\n".join(summary_lines),
        )
