"""Fact checker validating factual claims against grounded research sources and assigning verdicts."""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from app.domain.enums import ClaimVerificationVerdict, QualityStatus
from app.domain.models import Claim, FactCheckReport, ResearchDossier


class FactChecker:
    """Audits script claims against research dossiers to enforce truthfulness and URL provenance."""

    def verify_claim(
        self,
        claim: Claim,
        dossier: ResearchDossier,
        source_url_map: Dict[str, str],
        is_supported: bool,
        confidence: float,
        notes: Optional[str] = None,
    ) -> Claim:
        """Evaluate an individual claim against verified sources and produce a typed verdict."""
        # Find real URL from dossier or provided map
        cited_url = source_url_map.get(claim.source_id)
        if not cited_url:
            for src in dossier.sources:
                if src.id == claim.source_id:
                    cited_url = src.url
                    break

        if is_supported and confidence >= 0.70 and cited_url:
            verdict = ClaimVerificationVerdict.VERIFIED
            verified = True
        elif is_supported and 0.40 <= confidence < 0.70:
            verdict = ClaimVerificationVerdict.REWRITE_REQUIRED
            verified = False
        elif not is_supported and confidence <= 0.30:
            verdict = ClaimVerificationVerdict.REMOVE
            verified = False
            cited_url = None
        else:
            verdict = ClaimVerificationVerdict.UNVERIFIABLE
            verified = False
            cited_url = None

        return Claim(
            id=claim.id,
            source_id=claim.source_id,
            statement=claim.statement,
            verified=verified,
            verdict=verdict,
            confidence_score=confidence,
            cited_url=cited_url,
            notes=notes or claim.notes,
        )

    def build_audit_report(
        self,
        project_id: str,
        claims: List[Claim],
        report_id: Optional[str] = None,
    ) -> FactCheckReport:
        """Generate a complete FactCheckReport with counts and overall QualityStatus gate verdict."""
        rep_id = report_id or f"fcr-{uuid4().hex[:8]}"
        verified_count = sum(1 for c in claims if c.verdict == ClaimVerificationVerdict.VERIFIED)
        failed_count = sum(1 for c in claims if c.verdict in [ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.UNVERIFIABLE])
        rewrite_count = sum(1 for c in claims if c.verdict == ClaimVerificationVerdict.REWRITE_REQUIRED)

        if failed_count > 0:
            overall_verdict = QualityStatus.FAILED
            summary = f"Fact check FAILED: {failed_count} claim(s) rejected or unverifiable."
        elif rewrite_count > 0:
            overall_verdict = QualityStatus.WARNING
            summary = f"Fact check WARNING: {rewrite_count} claim(s) require rewriting."
        elif verified_count == len(claims) and len(claims) > 0:
            overall_verdict = QualityStatus.PASSED
            summary = f"Fact check PASSED: All {verified_count} claim(s) verified with source URL citations."
        else:
            overall_verdict = QualityStatus.PENDING
            summary = "Fact check PENDING: No evaluated claims found."

        return FactCheckReport(
            id=rep_id,
            project_id=project_id,
            claims=claims,
            verified_count=verified_count,
            failed_count=failed_count,
            overall_verdict=overall_verdict,
            audit_summary=summary,
        )
