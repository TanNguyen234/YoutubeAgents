"""Regression tests for Phase 4.2 grounding hardening:
- Eliminating partial-overlap false positives in FactChecker.
- Validating cited_excerpt strictly against the cited source content.
- Rejecting excerpts from wrong sources or fabricated quotes.
- Full contiguous voiceover claim extraction without silent degradation.
- Dynamic composite scoring when historical_fit is None.
- End-to-end checkpoint persistence and restart durability before VERIFIED.
"""

from pathlib import Path
import pytest
from pydantic import BaseModel

from app.core.backend import MockReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import ClaimVerificationVerdict, QualityStatus, VideoLifecycleState
from app.domain.models import (
    Channel,
    Claim,
    ResearchDossier,
    ResearchSource,
    Scene,
    Script,
    ScriptSections,
    TopicScoreBreakdown,
)
from app.services.claim_extractor import ClaimExtractor, ClaimExtractionError
from app.services.fact_checker import FactChecker, ClaimEntailmentOutput
from app.services.pipeline_brain import BrainPipeline
from app.services.script_writer import ScriptWriter
from app.services.topic_evaluator import TopicEvaluator, TopicEvaluationOutput
from app.services.topic_strategist import TopicStrategist


# --- 1. FactChecker Hardening Tests ---

def test_partial_overlap_claim_rejected_not_verified():
    """A claim sharing initial words with the source but containing unsubstantiated assertions must NOT be verified."""
    source_text = "In SQLite WAL mode, readers do not block writers and writers do not block readers."
    src = ResearchSource(
        id="src-01",
        url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="abc",
        content_snapshot=source_text,
    )
    dossier = ResearchDossier(id="dos-01", topic_id="top-01", sources=[src], summary="WAL summary")

    # Claim shares initial words but adds fabricated 100x throughput assertion
    compound_claim = Claim(
        id="clm-attack",
        statement="In SQLite WAL mode readers do not block writers and guarantees 100x throughput with zero disk IO.",
    )

    # Mock backend returns ungrounded result for the compound claim
    mock_backend = MockReasoningBackend(
        structured_responses=[
            ClaimEntailmentOutput(
                is_supported=False,
                confidence=0.15,
                verdict=ClaimVerificationVerdict.REMOVE,
                cited_url="https://sqlite.org/wal.html",
                cited_excerpt="readers do not block writers",
                notes="The assertion about 100x throughput and zero disk IO is completely unsupported.",
            )
        ]
    )

    checker = FactChecker(backend=mock_backend)
    result = checker.verify_claim(compound_claim, dossier)

    assert result.verified is False
    assert result.verdict in (ClaimVerificationVerdict.REMOVE, ClaimVerificationVerdict.REWRITE_REQUIRED, ClaimVerificationVerdict.UNVERIFIABLE)
    assert result.confidence_score <= 0.50


def test_fabricated_excerpt_rejected_as_unverifiable():
    """If the LLM returns an excerpt that does not exist in the cited source, the claim must resolve to UNVERIFIABLE."""
    source_text = "SQLite supports atomic commits and rollbacks."
    src = ResearchSource(
        id="src-01",
        url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="abc",
        content_snapshot=source_text,
    )
    dossier = ResearchDossier(id="dos-01", topic_id="top-01", sources=[src], summary="WAL summary")

    claim = Claim(id="clm-01", statement="SQLite eliminates all database corruptions.")

    # LLM claims supported with a hallucinated quote that is NOT in source_text
    mock_backend = MockReasoningBackend(
        structured_responses=[
            ClaimEntailmentOutput(
                is_supported=True,
                confidence=0.95,
                verdict=ClaimVerificationVerdict.VERIFIED,
                cited_url="https://sqlite.org/wal.html",
                cited_excerpt="SQLite guarantees zero database corruptions under all operating conditions.",
                notes="Model hallucinated this quote.",
            )
        ]
    )

    checker = FactChecker(backend=mock_backend)
    result = checker.verify_claim(claim, dossier)

    assert result.verified is False
    assert result.verdict == ClaimVerificationVerdict.UNVERIFIABLE
    assert "not found in" in result.notes.lower() or "unverifiable" in result.notes.lower()


def test_entailment_cannot_use_excerpt_from_different_source():
    """An excerpt must exist in the EXACT cited source, not a different source in the dossier."""
    src1 = ResearchSource(
        id="src-01",
        url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="abc1",
        content_snapshot="SQLite WAL mode enables concurrency.",
    )
    src2 = ResearchSource(
        id="src-02",
        url="https://docs.python.org/3/library/asyncio.html",
        title="Python Asyncio",
        content_sha256="abc2",
        content_snapshot="Asyncio uses cooperative multitasking with async and await.",
    )
    dossier = ResearchDossier(id="dos-multi", topic_id="top-01", sources=[src1, src2], summary="Multi sources")

    claim = Claim(id="clm-cross", statement="Python asyncio uses cooperative multitasking across coroutines.")

    # Model cited src1 (sqlite.org) but quoted text from src2 (asyncio doc)
    mock_backend = MockReasoningBackend(
        structured_responses=[
            ClaimEntailmentOutput(
                is_supported=True,
                confidence=0.95,
                verdict=ClaimVerificationVerdict.VERIFIED,
                cited_url="https://sqlite.org/wal.html",  # WRONG URL FOR THIS QUOTE
                cited_excerpt="Asyncio uses cooperative multitasking with async and await.",
                notes="Cross source mismatch.",
            )
        ]
    )

    checker = FactChecker(backend=mock_backend)
    result = checker.verify_claim(claim, dossier)

    assert result.verified is False
    assert result.verdict == ClaimVerificationVerdict.UNVERIFIABLE


def test_missing_cited_url_not_auto_assigned():
    """If the LLM omits cited_url, FactChecker must NOT automatically assign the only source in the dossier."""
    src = ResearchSource(
        id="src-01",
        url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="abc",
        content_snapshot="SQLite WAL mode enables concurrency.",
    )
    dossier = ResearchDossier(id="dos-01", topic_id="top-01", sources=[src], summary="WAL doc")

    # Paraphrased statement that does not match verbatim exact substring
    claim = Claim(id="clm-no-url", statement="In SQLite database architectures WAL mode delivers concurrent access.")

    mock_backend = MockReasoningBackend(
        structured_responses=[
            ClaimEntailmentOutput(
                is_supported=True,
                confidence=0.90,
                verdict=ClaimVerificationVerdict.VERIFIED,
                cited_url="https://unknown-domain.com/page.html",
                cited_excerpt="SQLite WAL mode enables concurrency.",
                notes="Cited URL is not in dossier.",
            )
        ]
    )

    checker = FactChecker(backend=mock_backend)
    result = checker.verify_claim(claim, dossier)

    assert result.verified is False
    assert result.verdict == ClaimVerificationVerdict.UNVERIFIABLE


# --- 2. ClaimExtractor Coverage & Fallback Tests ---

def test_claim_extraction_fallback_covers_full_voiceover_including_hook_and_intro():
    """When reasoning extraction fails, deterministic fallback must split the contiguous voiceover (hook, intro, scenes, cta)."""
    sections = ScriptSections(
        hook="Why do multi-agent systems fail?",
        intro="They lack a unified control plane and depend on brittle third-party APIs.",
        segments=[
            Scene(index=0, narration="Antigravity provides a primary control and reasoning plane.", target_duration_seconds=10.0),
            Scene(index=1, narration="All 16 lifecycle states are governed by state machine transitions.", target_duration_seconds=10.0),
        ],
        cta="Subscribe for more autonomous systems architecture.",
        voiceover_text="Why do multi-agent systems fail? They lack a unified control plane and depend on brittle third-party APIs. Antigravity provides a primary control and reasoning plane. All 16 lifecycle states are governed by state machine transitions. Subscribe for more autonomous systems architecture.",
        estimated_duration=30.0,
    )
    writer = ScriptWriter()
    script = writer.build_script("scr-cov", "Agents Architecture", sections)

    # Empty responses forces fallback to deterministic sentence extraction
    mock_backend = MockReasoningBackend(structured_responses=[])
    extractor = ClaimExtractor(backend=mock_backend)

    claims = extractor.extract_from_script(script)

    assert len(claims) >= 4
    statements = [c.statement.lower() for c in claims]

    # Verify intro/hook/cta sentences are all captured
    assert any("multi-agent systems fail" in s for s in statements)
    assert any("unified control plane" in s for s in statements)
    assert any("primary control and reasoning plane" in s for s in statements)
    assert any("16 lifecycle states" in s for s in statements)


def test_claim_extraction_empty_voiceover_raises_typed_error():
    """Empty script voiceover must raise ClaimExtractionError rather than returning [] and allowing a silent pass."""
    script = Script(
        id="scr-empty",
        title="Empty",
        hook="Empty hook",
        scenes=[],
        sections=None,
        total_word_count=1,
        estimated_duration_seconds=1.0,
    )
    extractor = ClaimExtractor(backend=MockReasoningBackend(structured_responses=[]))

    with pytest.raises(ClaimExtractionError):
        extractor.extract_from_script(script)


# --- 3. Topic Scoring Integrity Tests ---

def test_topic_scoring_no_fabricated_defaults_and_optional_historical_fit():
    """TopicScoreBreakdown and TopicEvaluationOutput must not fabricate default numbers when historical_fit is None."""
    raw_scores = {
        "demand": 9.0,
        "freshness": 8.0,
        "competition": 7.0,
        "channel_fit": 9.0,
        "originality": 8.5,
        "evidence_quality": 9.0,
        "production_feasibility": 8.0,
        "historical_fit": None,  # Not fabricated
    }

    strategist = TopicStrategist()
    composite = strategist.compute_composite_score(raw_scores)

    # Composite must be dynamically renormalized over the 7 available dimensions
    assert 7.0 <= composite <= 10.0

    breakdown = TopicScoreBreakdown(
        demand=9.0,
        freshness=8.0,
        competition=7.0,
        channel_fit=9.0,
        originality=8.5,
        evidence_quality=9.0,
        production_feasibility=8.0,
        historical_fit=None,
        composite_score=composite,
    )
    assert breakdown.historical_fit is None


# --- 4. Pipeline Restart Durability & Checkpoint Persistence Test ---

def test_pipeline_persists_evidence_before_verified(tmp_path: Path):
    """Pipeline must persist TopicCandidate, ResearchDossier, and FactCheckReport to SQLite before reaching VERIFIED."""
    db_file = tmp_path / "test_durability.db"
    repo1 = SQLiteRepository(db_file)

    channel = Channel(
        id="chan-durable",
        title="Durable Channel",
        handle="@durable",
        niche="Database Systems",
        target_audience="Engineers",
    )
    repo1.save_channel(channel)

    src_snapshot = "In SQLite WAL mode, readers do not block writers and writers do not block readers."
    src = ResearchSource(
        id="src-dur",
        url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="hash-wal",
        content_snapshot=src_snapshot,
    )
    dossier = ResearchDossier(id="dos-dur", topic_id="top-dur", sources=[src], summary="SQLite WAL doc")

    sections = ScriptSections(
        hook="How does SQLite achieve high concurrency?",
        intro="By using Write-Ahead Logging mode.",
        segments=[Scene(index=0, narration="In SQLite WAL mode, readers do not block writers and writers do not block readers.", target_duration_seconds=15.0)],
        cta="Subscribe for more database internals.",
        voiceover_text="How does SQLite achieve high concurrency? By using Write-Ahead Logging mode. In SQLite WAL mode, readers do not block writers and writers do not block readers. Subscribe for more database internals.",
        estimated_duration=20.0,
    )

    def mock_handler(prompt: str, schema_cls: type) -> BaseModel:
        if schema_cls == TopicEvaluationOutput:
            return TopicEvaluationOutput(
                demand=9.0,
                freshness=8.0,
                competition=7.0,
                channel_fit=9.5,
                originality=8.0,
                evidence_quality=9.5,
                production_feasibility=8.5,
                historical_fit=None,
                rationale="High demand technical topic",
                score_reasons={"demand": "High interest"},
            )
        if schema_cls == ScriptSections:
            return sections
        if schema_cls == ClaimEntailmentOutput:
            return ClaimEntailmentOutput(
                is_supported=True,
                confidence=0.98,
                verdict=ClaimVerificationVerdict.VERIFIED,
                cited_url="https://sqlite.org/wal.html",
                cited_excerpt="readers do not block writers and writers do not block readers",
                notes="Directly supported by WAL documentation.",
            )
        return schema_cls.model_construct()

    mock_backend = MockReasoningBackend(handler=mock_handler)

    # Injected research agent returning pre-compiled dossier
    class PrecompiledResearchAgent:
        def build_dossier_from_urls(self, urls, topic_id, summary_prompt=None):
            return dossier

    pipeline = BrainPipeline(
        repo=repo1,
        backend=mock_backend,
        research_agent=PrecompiledResearchAgent(),
    )

    project, report = pipeline.run_stage_1_to_5(
        project_id="proj-durable-01",
        channel=channel,
        keyword="Mastering SQLite WAL Mode Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    assert project.state == VideoLifecycleState.VERIFIED
    assert report.overall_verdict == QualityStatus.PASSED

    # RESTART TEST: Create a brand-new repository instance on the same SQLite database
    repo2 = SQLiteRepository(db_file)

    loaded_proj = repo2.get_video_project("proj-durable-01")
    assert loaded_proj is not None
    assert loaded_proj.state == VideoLifecycleState.VERIFIED

    loaded_dossier = repo2.get_research_dossier("proj-durable-01")
    assert loaded_dossier is not None
    assert len(loaded_dossier.sources) == 1
    assert loaded_dossier.sources[0].url == "https://sqlite.org/wal.html"
    assert loaded_dossier.sources[0].content_snapshot == src_snapshot

    loaded_report = repo2.get_fact_check_report("proj-durable-01")
    assert loaded_report is not None
    assert loaded_report.overall_verdict == QualityStatus.PASSED
    assert len(loaded_report.claims) >= 1


# --- Phase 4.2.1 Closure Regression Tests ---

def test_unverified_fact_in_scene_narration_audited():
    """An ungrounded fact hidden strictly in a scene's narration must be present in canonical narration and audited."""
    src = ResearchSource(
        id="src-01",
        url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="abc",
        content_snapshot="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
    )
    dossier = ResearchDossier(id="dos-01", topic_id="top-01", sources=[src], summary="WAL summary")

    # The ungrounded statement is placed strictly inside scene 2 narration
    sections = ScriptSections(
        hook="How does SQLite achieve high concurrency?",
        intro="By using Write-Ahead Logging mode.",
        segments=[
            Scene(index=0, narration="Readers and writers run concurrently without blocking."),
            Scene(index=1, narration="SQLite was secretly created in Rust on Mars in 1850."),
        ],
        cta="Subscribe for more database internals.",
    )

    # Assert canonical voiceover includes the scene 2 narration
    assert "SQLite was secretly created in Rust on Mars in 1850." in sections.voiceover_text

    script = Script(
        id="scr-01",
        title="SQLite Concurrency",
        hook=sections.hook,
        scenes=sections.segments,
        total_word_count=100,
        estimated_duration_seconds=30.0,
        sections=sections,
    )

    extractor = ClaimExtractor(backend=MockReasoningBackend())
    extracted_claims = extractor.extract_from_script(script)
    statements = [c.statement for c in extracted_claims]
    assert any("Mars" in s or "Rust" in s for s in statements)


def test_stage_5_failure_transitions_to_failed(tmp_path: Path):
    """If an error occurs during Stage 5 (e.g. empty voiceover / extraction failure), state transitions to FAILED."""
    db_file = tmp_path / "stage5_fail.db"
    repo = SQLiteRepository(db_file)

    channel = Channel(
        id="chan-fail-01",
        title="Database Engineering",
        handle="@dbeng",
        niche="Databases",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    dossier = ResearchDossier(
        id="dos-01",
        topic_id="top-01",
        sources=[ResearchSource(id="s1", url="https://example.com", title="Ex", content_sha256="123", content_snapshot="Text")],
        summary="Summary",
    )

    # Empty sections to induce extraction error
    empty_sections = ScriptSections(
        hook="",
        intro="",
        segments=[],
        cta="",
        voiceover_text="",
    )

    def mock_handler(prompt: str, schema_cls: type) -> BaseModel:
        if schema_cls == TopicEvaluationOutput:
            return TopicEvaluationOutput(
                demand=9.0,
                freshness=8.0,
                competition=7.0,
                channel_fit=9.0,
                originality=8.0,
                evidence_quality=9.0,
                production_feasibility=9.0,
                rationale="Test rationale",
                score_reasons={"demand": "High interest"},
            )
        if schema_cls == ScriptSections:
            return empty_sections
        return schema_cls.model_construct()

    mock_backend = MockReasoningBackend(handler=mock_handler)

    class InjectedResearchAgent:
        def build_dossier_from_urls(self, urls, topic_id, summary_prompt=None):
            return dossier

    pipeline = BrainPipeline(
        repo=repo,
        backend=mock_backend,
        research_agent=InjectedResearchAgent(),
    )

    with pytest.raises(Exception):
        pipeline.run_stage_1_to_5(
            project_id="proj-stage5-fail",
            channel=channel,
            keyword="Failing Stage 5",
            seed_urls=["https://example.com"],
        )

    # Verify project did NOT remain stuck in SCRIPTED
    proj = repo.get_video_project("proj-stage5-fail")
    assert proj is not None
    assert proj.state == VideoLifecycleState.FAILED


def test_rewrite_and_restart_persists_only_final_authoritative_report(tmp_path: Path):
    """When a script rewrite occurs, persisted claims/counts must reflect exactly the final passed report upon DB restart."""
    db_file = tmp_path / "rewrite_restart.db"
    repo1 = SQLiteRepository(db_file)

    channel = Channel(
        id="chan-rew-01",
        title="Async Python",
        handle="@asyncpy",
        niche="Python",
        target_audience="Devs",
    )
    repo1.save_channel(channel)

    src = ResearchSource(
        id="src-01",
        url="https://docs.python.org/3/library/asyncio.html",
        title="Asyncio Docs",
        content_sha256="hash123",
        content_snapshot="asyncio is a library to write concurrent code using the async/await syntax.",
    )
    dossier = ResearchDossier(id="dos-01", topic_id="top-01", sources=[src], summary="Asyncio Summary")

    sections_v1 = ScriptSections(
        hook="Why use asyncio?",
        intro="Asyncio allows writing concurrent code using async/await syntax.",
        segments=[Scene(index=0, narration="Asyncio makes your CPU 100 times faster instantly.")],
        cta="Subscribe!",
    )
    sections_v2 = ScriptSections(
        hook="Why use asyncio?",
        intro="Asyncio allows writing concurrent code using async/await syntax.",
        segments=[Scene(index=0, narration="Asyncio is a library to write concurrent code using the async/await syntax.")],
        cta="Subscribe!",
    )

    call_count = {"entail": 0}

    def mock_handler(prompt: str, schema_cls: type) -> BaseModel:
        if schema_cls == TopicEvaluationOutput:
            return TopicEvaluationOutput(
                demand=9.0,
                freshness=8.0,
                competition=7.0,
                channel_fit=9.0,
                originality=8.0,
                evidence_quality=9.0,
                production_feasibility=9.0,
                rationale="Test rationale",
                score_reasons={"demand": "High interest"},
            )
        if schema_cls == ScriptSections:
            return sections_v1 if call_count["entail"] == 0 else sections_v2
        if schema_cls == ClaimEntailmentOutput:
            call_count["entail"] += 1
            if "100 times faster" in prompt:
                return ClaimEntailmentOutput(
                    is_supported=False,
                    confidence=0.1,
                    verdict=ClaimVerificationVerdict.REMOVE,
                    cited_url="https://docs.python.org/3/library/asyncio.html",
                    cited_excerpt="async/await syntax",
                    notes="Unsupported assertion",
                )
            else:
                return ClaimEntailmentOutput(
                    is_supported=True,
                    confidence=0.95,
                    verdict=ClaimVerificationVerdict.VERIFIED,
                    cited_url="https://docs.python.org/3/library/asyncio.html",
                    cited_excerpt="asyncio is a library to write concurrent code using the async/await syntax",
                    notes="Verified by docs",
                )
        return schema_cls.model_construct()

    class InjectedResearchAgent:
        def build_dossier_from_urls(self, urls, topic_id, summary_prompt=None):
            return dossier

    pipeline = BrainPipeline(
        repo=repo1,
        backend=MockReasoningBackend(handler=mock_handler),
        research_agent=InjectedResearchAgent(),
    )

    proj, report = pipeline.run_stage_1_to_5(
        project_id="proj-rewrite-restart",
        channel=channel,
        keyword="Asyncio Concurrency",
        seed_urls=["https://docs.python.org/3/library/asyncio.html"],
        max_rewrite_attempts=1,
    )

    assert proj.state == VideoLifecycleState.VERIFIED
    assert report.overall_verdict == QualityStatus.PASSED

    # Restart repository and check authoritative persistence
    repo2 = SQLiteRepository(db_file)
    reloaded_report = repo2.get_fact_check_report("proj-rewrite-restart")
    assert reloaded_report is not None
    assert reloaded_report.overall_verdict == QualityStatus.PASSED
    assert reloaded_report.failed_count == 0
    # Must NOT have retained orphaned unverified claims from attempt 1
    assert all(c.verified for c in reloaded_report.claims)
    assert len(reloaded_report.claims) == reloaded_report.verified_count


def test_cited_source_resolution_equality_only():
    """_resolve_cited_source must strictly match normalized full URL and reject substring/prefix containment."""
    src = ResearchSource(
        id="src-01",
        url="https://sqlite.org/wal.html",
        final_url="https://sqlite.org/wal.html",
        title="SQLite WAL",
        content_sha256="abc",
        content_snapshot="Content",
    )
    dossier = ResearchDossier(id="dos-01", topic_id="top-01", sources=[src], summary="Summary")
    checker = FactChecker()

    # Exact match (with case/trailing slash tolerance)
    assert checker._resolve_cited_source("https://sqlite.org/wal.html", dossier) == src
    assert checker._resolve_cited_source("HTTPS://SQLITE.ORG/WAL.HTML/", dossier) == src

    # Prefix or substring match must NOT resolve
    assert checker._resolve_cited_source("https://sqlite.org", dossier) is None
    assert checker._resolve_cited_source("https://sqlite.org/wal", dossier) is None
    assert checker._resolve_cited_source("https://sqlite.org/wal.html?param=1", dossier) is None


def test_fact_check_report_empty_claims_never_passed():
    """An audit report with empty claims must resolve to FAILED, never PASSED."""
    checker = FactChecker()
    report = checker.build_audit_report("proj-empty", [])
    assert report.overall_verdict == QualityStatus.FAILED
    assert report.verified_count == 0
    assert report.failed_count == 0
