"""Unit tests for FactChecker claim verification, provenance URL checking, and audit report generation."""

from datetime import datetime, timezone
import pytest
from app.domain.enums import ClaimVerificationVerdict, QualityStatus
from app.domain.models import Claim, ResearchDossier, ResearchSource
from app.services.fact_checker import FactChecker


@pytest.fixture
def checker():
    return FactChecker()


@pytest.fixture
def sample_dossier():
    source1 = ResearchSource(
        id="src-001",
        url="https://sqlite.org/wal.html",
        title="Write-Ahead Logging",
        authors=["SQLite Consortium"],
        content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        license_type="Public Domain",
    )
    return ResearchDossier(
        id="dos-001",
        topic_id="top-001",
        sources=[source1],
        claims=[],
        summary="Documentation of SQLite WAL mode and concurrency semantics.",
    )


def test_verify_supported_claim_resolves_to_verified(checker, sample_dossier):
    claim = Claim(
        id="clm-001",
        source_id="src-001",
        statement="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
    )
    evaluated_claim = checker.verify_claim(
        claim=claim,
        dossier=sample_dossier,
        source_url_map={"src-001": "https://sqlite.org/wal.html"},
        is_supported=True,
        confidence=0.95,
    )
    assert evaluated_claim.verified is True
    assert evaluated_claim.verdict == ClaimVerificationVerdict.VERIFIED
    assert evaluated_claim.cited_url == "https://sqlite.org/wal.html"
    assert evaluated_claim.confidence_score == 0.95


def test_verify_unsupported_claim_resolves_to_remove_or_unverifiable(checker, sample_dossier):
    claim = Claim(
        id="clm-002",
        source_id="src-missing",
        statement="SQLite can handle 10 billion transactions per second on an iPhone.",
    )
    evaluated_claim = checker.verify_claim(
        claim=claim,
        dossier=sample_dossier,
        source_url_map={},
        is_supported=False,
        confidence=0.1,
    )
    assert evaluated_claim.verified is False
    assert evaluated_claim.verdict in [ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.UNVERIFIABLE]
    assert evaluated_claim.cited_url is None


def test_generate_audit_report(checker, sample_dossier):
    verified_claim = Claim(
        id="clm-001",
        source_id="src-001",
        statement="SQLite supports WAL mode.",
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
    report = checker.build_audit_report(
        project_id="proj-001",
        claims=[verified_claim, flagged_claim],
    )
    assert report.project_id == "proj-001"
    assert report.verified_count == 1
    assert report.failed_count == 1
    assert report.overall_verdict == QualityStatus.FAILED  # Contains a REMOVE claim
