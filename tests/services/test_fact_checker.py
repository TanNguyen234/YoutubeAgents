"""Unit tests for FactChecker claim verification, provenance URL checking, and audit report generation."""

from datetime import datetime, timezone
import pytest
from app.core.backend import MockReasoningBackend
from app.domain.enums import ClaimVerificationVerdict, QualityStatus
from app.domain.models import Claim, ResearchDossier, ResearchSource
from app.services.fact_checker import ClaimEntailmentOutput, FactChecker


@pytest.fixture
def sample_dossier():
    source1 = ResearchSource(
        id="src-001",
        url="https://sqlite.org/wal.html",
        title="Write-Ahead Logging",
        authors=["SQLite Consortium"],
        content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        content_snapshot="In SQLite WAL mode, readers do not block writers and writers do not block readers. Write-ahead logging uses a separate -wal file.",
        license_type="Public Domain",
    )
    return ResearchDossier(
        id="dos-001",
        topic_id="top-001",
        sources=[source1],
        claims=[],
        summary="Documentation of SQLite WAL mode and concurrency semantics.",
    )


def test_verify_supported_claim_resolves_to_verified(sample_dossier):
    # Verbatim match directly from snapshot text
    checker = FactChecker(backend=MockReasoningBackend())
    claim = Claim(
        id="clm-001",
        source_id="src-001",
        statement="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
    )
    evaluated = checker.verify_claim(claim=claim, dossier=sample_dossier)
    assert evaluated.verified is True
    assert evaluated.verdict == ClaimVerificationVerdict.VERIFIED
    assert evaluated.cited_url == "https://sqlite.org/wal.html"
    assert evaluated.confidence_score == 1.0


def test_verify_unsupported_claim_resolves_to_remove(sample_dossier):
    def mock_handler(prompt, schema_cls):
        if schema_cls == ClaimEntailmentOutput:
            return ClaimEntailmentOutput(
                is_supported=False,
                confidence=0.1,
                cited_url=None,
                cited_excerpt=None,
                rationale="No mention of 10 billion transactions per second in SQLite documentation.",
            )
        raise ValueError("Unhandled schema")

    checker = FactChecker(backend=MockReasoningBackend(handler=mock_handler))
    claim = Claim(
        id="clm-002",
        statement="SQLite can handle 10 billion transactions per second on an iPhone.",
    )
    evaluated = checker.verify_claim(claim=claim, dossier=sample_dossier)
    assert evaluated.verified is False
    assert evaluated.verdict in [ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.UNVERIFIABLE]
    assert evaluated.cited_url is None


def test_generate_audit_report(sample_dossier):
    verified_claim = Claim(
        id="clm-001",
        source_id="src-001",
        statement="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        confidence_score=0.98,
        cited_url="https://sqlite.org/wal.html",
    )
    flagged_claim = Claim(
        id="clm-002",
        source_id="src-001",
        statement="SQLite is maintained by Microsoft.",
        verified=False,
        verdict=ClaimVerificationVerdict.REMOVE,
        confidence_score=0.0,
        cited_url=None,
    )
    checker = FactChecker(backend=MockReasoningBackend())
    report = checker.build_audit_report(
        project_id="proj-001",
        claims=[verified_claim, flagged_claim],
    )
    assert report.project_id == "proj-001"
    assert report.verified_count == 1
    assert report.failed_count == 1
    assert report.overall_verdict == QualityStatus.FAILED  # Contains a REMOVE claim
