"""FactChecker service verifying claims against persisted source evidence using Antigravity entailment."""

from datetime import datetime, timezone
import re
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.domain.enums import ClaimVerificationVerdict, QualityStatus
from app.domain.models import Claim, FactCheckReport, ResearchDossier, ResearchSource


class ClaimEntailmentOutput(BaseModel):
    """Structured LLM output for fact checking entailment."""

    is_supported: bool = Field(description="True if the claim is fully factually supported / entailed by source evidence, False otherwise")
    confidence: float = Field(ge=0.0, le=1.0, description="Entailment confidence score between 0.0 and 1.0")
    verdict: ClaimVerificationVerdict = Field(default=ClaimVerificationVerdict.VERIFIED, description="Verdict: VERIFIED, REWRITE_REQUIRED, REMOVE, or UNVERIFIABLE")
    cited_url: str = Field(description="Source URL from the provided SOURCE EVIDENCE header (e.g. 'https://sqlite.org/wal.html'). If unsupported, use 'NONE'.")
    cited_excerpt: str = Field(description="Exact verbatim quote copied directly from the cited source text proving this claim. If unsupported, use 'NONE'.")
    notes: str = Field(default="", description="Detailed rationale explaining the entailment or why it is ungrounded")


class FactChecker:
    """Audits extracted claims against research dossier source text with strict citation binding."""

    def __init__(self, backend: Optional[ReasoningBackend] = None):
        self.backend = backend or AntigravityCLIBackend()

    def _deterministic_substring_check(self, statement: str, dossier: ResearchDossier) -> Optional[Tuple[str, str]]:
        """Fast-path check: returns (source_url, excerpt) if the entire normalized claim is a continuous substring of source text."""
        clean_stmt = " ".join(statement.lower().split()).strip(".,;:!?\"'")
        words = clean_stmt.split()
        if len(words) < 4:
            return None

        for src in dossier.sources:
            if not src.content_snapshot:
                continue
            src_clean = " ".join(src.content_snapshot.lower().split())
            if clean_stmt in src_clean:
                return (src.final_url or src.url, statement)

        return None

    @staticmethod
    def _normalize_url(url: Optional[str]) -> str:
        if not url:
            return ""
        u = url.strip()
        while u.endswith("/"):
            u = u[:-1]
        return u.lower()

    def _resolve_cited_source(self, cited_url: Optional[str], dossier: ResearchDossier) -> Optional[ResearchSource]:
        """Resolve cited URL to an exact ResearchSource in the dossier via strict normalized equality."""
        if not cited_url or cited_url.strip().upper() == "NONE":
            return None
        clean_url = self._normalize_url(cited_url)
        if not clean_url:
            return None
        for src in dossier.sources:
            src_u = self._normalize_url(src.url)
            src_f = self._normalize_url(src.final_url)
            if clean_url == src_u or clean_url == src_f:
                return src
        return None

    def verify_claim(self, claim: Claim, dossier: ResearchDossier) -> Claim:
        """Verify a single claim against dossier evidence using strict citation and entailment checks."""
        # 1. Exact continuous full-statement fast path
        fast_hit = self._deterministic_substring_check(claim.statement, dossier)
        if fast_hit:
            source_url, excerpt = fast_hit
            return Claim(
                id=claim.id,
                statement=claim.statement,
                verified=True,
                verdict=ClaimVerificationVerdict.VERIFIED,
                confidence_score=1.0,
                cited_url=source_url,
                cited_excerpt=excerpt,
                notes="Verified via exact verbatim continuous substring match in source evidence.",
            )

        # 2. Prepare source context for Antigravity entailment
        sources_text = ""
        avail_urls = []
        for s in dossier.sources:
            url_str = s.final_url or s.url
            avail_urls.append(url_str)
            snapshot = (s.content_snapshot or "")[:4000]
            sources_text += f"\n=== SOURCE URL: {url_str} ===\n{snapshot}\n"
        urls_str = ", ".join(f'"{u}"' for u in avail_urls)

        prompt = f"""You are a strict, professional Fact-Checking Auditor for educational videos.
Audit the following CLAIM strictly against the provided SOURCE EVIDENCE.

CLAIM TO VERIFY:
"{claim.statement}"

SOURCE EVIDENCE:
{sources_text}

AVAILABLE SOURCE URLS:
[{urls_str}]

AUDIT INSTRUCTIONS:
1. If the claim is factually supported or entailed by the source evidence:
   - Set is_supported to TRUE and confidence >= 0.85.
   - Set verdict to "VERIFIED".
   - Set cited_url to the EXACT matching URL from the AVAILABLE SOURCE URLS list above (e.g. {urls_str}).
   - Set cited_excerpt to the EXACT verbatim text quote copied directly from that source document that proves the claim.
2. If the claim is unsupported, misleading, or contradicted by evidence:
   - Set is_supported to FALSE, confidence below 0.5, and verdict to "REWRITE_REQUIRED" or "REMOVE".
   - Set cited_url to "NONE".
   - Set cited_excerpt to "NONE".
3. Provide a clear rationale in notes.
"""
        entailment = self.backend.generate_structured(prompt, ClaimEntailmentOutput)
        if isinstance(entailment, dict):
            entailment = ClaimEntailmentOutput.model_validate(entailment)

        # 3. Resolve cited source in dossier
        cited_source = self._resolve_cited_source(entailment.cited_url, dossier)
        if not cited_source:
            if not entailment.is_supported:
                return Claim(
                    id=claim.id,
                    source_id=None,
                    statement=claim.statement,
                    verified=False,
                    verdict=entailment.verdict if entailment.verdict in (ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.REWRITE_REQUIRED) else ClaimVerificationVerdict.REMOVE,
                    confidence_score=round(entailment.confidence, 2),
                    cited_url=None,
                    cited_excerpt=None,
                    notes=entailment.notes or "Claim was evaluated as unsupported by evidence.",
                )
            return Claim(
                id=claim.id,
                source_id=None,
                statement=claim.statement,
                verified=False,
                verdict=ClaimVerificationVerdict.UNVERIFIABLE,
                confidence_score=0.2,
                cited_url=entailment.cited_url,
                cited_excerpt=entailment.cited_excerpt,
                notes=f"Cited URL '{entailment.cited_url}' is missing or not present in the verified research dossier.",
            )

        # 4. Validate cited excerpt exists in the cited source snapshot
        cited_excerpt = entailment.cited_excerpt
        if not cited_excerpt or cited_excerpt.strip().upper() in ("NONE", "NULL", ""):
            if not entailment.is_supported:
                return Claim(
                    id=claim.id,
                    source_id=cited_source.id,
                    statement=claim.statement,
                    verified=False,
                    verdict=entailment.verdict if entailment.verdict in (ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.REWRITE_REQUIRED) else ClaimVerificationVerdict.REMOVE,
                    confidence_score=round(entailment.confidence, 2),
                    cited_url=cited_source.final_url or cited_source.url,
                    cited_excerpt=None,
                    notes=entailment.notes or "Claim was evaluated as unsupported by evidence.",
                )
            return Claim(
                id=claim.id,
                source_id=cited_source.id,
                statement=claim.statement,
                verified=False,
                verdict=ClaimVerificationVerdict.UNVERIFIABLE,
                confidence_score=0.2,
                cited_url=cited_source.final_url or cited_source.url,
                cited_excerpt=None,
                notes="No source excerpt was provided by the reasoning engine to ground this claim.",
            )

        clean_excerpt = " ".join(re.findall(r'\w+', cited_excerpt.lower()))
        clean_source_text = " ".join(re.findall(r'\w+', (cited_source.content_snapshot or "").lower()))

        if clean_excerpt not in clean_source_text:
            return Claim(
                id=claim.id,
                source_id=cited_source.id,
                statement=claim.statement,
                verified=False,
                verdict=ClaimVerificationVerdict.UNVERIFIABLE,
                confidence_score=0.2,
                cited_url=cited_source.final_url or cited_source.url,
                cited_excerpt=cited_excerpt,
                notes=f"Cited excerpt quote was not found in the verified source document '{cited_source.final_url or cited_source.url}'.",
            )

        # 5. Grant VERIFIED only if supported, confidence >= 0.70, and quote verified
        if entailment.is_supported and entailment.confidence >= 0.70:
            return Claim(
                id=claim.id,
                source_id=cited_source.id,
                statement=claim.statement,
                verified=True,
                verdict=ClaimVerificationVerdict.VERIFIED,
                confidence_score=round(entailment.confidence, 2),
                cited_url=cited_source.final_url or cited_source.url,
                cited_excerpt=cited_excerpt,
                notes=entailment.notes or "Supported by verified source citation.",
            )
        else:
            verdict = entailment.verdict if entailment.verdict in (ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.REWRITE_REQUIRED) else ClaimVerificationVerdict.REMOVE
            return Claim(
                id=claim.id,
                source_id=cited_source.id,
                statement=claim.statement,
                verified=False,
                verdict=verdict,
                confidence_score=round(entailment.confidence, 2),
                cited_url=cited_source.final_url or cited_source.url,
                cited_excerpt=cited_excerpt,
                notes=entailment.notes or "Claim was evaluated as unsupported by evidence.",
            )

    def build_audit_report(self, project_id: str, claims: List[Claim]) -> FactCheckReport:
        """Construct a FactCheckReport from an already-verified list of claims."""
        if not claims or len(claims) == 0:
            return FactCheckReport(
                id=f"fcr-{project_id}",
                project_id=project_id,
                claims=[],
                verified_count=0,
                failed_count=0,
                overall_verdict=QualityStatus.FAILED,
                audit_summary="No claims evaluated. An empty claims audit cannot produce a PASSED verdict.",
                created_at=datetime.now(timezone.utc),
            )

        verified_count = sum(1 for c in claims if c.verified)
        failed_count = len(claims) - verified_count
        overall_verdict = QualityStatus.PASSED if (failed_count == 0 and verified_count > 0) else QualityStatus.FAILED

        summary_lines = [f"Evaluated {len(claims)} claim(s): {verified_count} verified, {failed_count} failed."]
        for c in claims:
            status = "PASS" if c.verified else "FAIL"
            source_info = f" [Source: {c.cited_url}]" if c.cited_url else " [No Citation]"
            summary_lines.append(f" - [{status}] ({c.verdict.value}) {c.statement}{source_info}")

        return FactCheckReport(
            id=f"fcr-{project_id}",
            project_id=project_id,
            claims=claims,
            verified_count=verified_count,
            failed_count=failed_count,
            overall_verdict=overall_verdict,
            audit_summary="\n".join(summary_lines),
            created_at=datetime.now(timezone.utc),
        )

    def verify_all_claims(
        self,
        claims: List[Claim],
        dossier: ResearchDossier,
        project_id: str,
    ) -> FactCheckReport:
        """Verify a list of claims against a research dossier and generate a FactCheckReport."""
        verified_claims: List[Claim] = []
        for claim in claims:
            checked = self.verify_claim(claim, dossier)
            verified_claims.append(checked)

        return self.build_audit_report(project_id, verified_claims)
