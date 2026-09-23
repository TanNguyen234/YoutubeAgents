"""Comprehensive test suite for Packaging Engine Phase 1: Grounded Title + Thumbnail Tournament.

Covers full audit matrix:
- Cases A-E: Title Truth Grounding (declarative hallucination, "Why" presupposition, semantic paraphrase, invented claim ID, backend failure).
- Cases F-G: Visual strategy execution and metadata.
- Cases H-I: Bounded diversity correction and fail-closed unresolved diversity.
- Cases J-K: Real thumbnail file validation, SHA integrity, missing file failure.
- Case L: Rejection isolation from SEOPackage.title_variants.
- Cases M-O: Shorts 9:16 support, long-form local readiness semantics, made_for_kids disqualification.
- Schema v7 tests: verified in tests/db/test_schema_v7_migration.py.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import socket
import tempfile
from typing import Any, Dict, List, Optional
import pytest
from PIL import Image

from app.core.backend import MockReasoningBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import (
    AssetType,
    ClaimVerificationVerdict,
    PackagingTournamentStatus,
    PlatformFormat,
    PrivacyStatus,
    QualityStatus,
    TitleTruthStatus,
    TitleVariantType,
    VideoLifecycleState,
)
from app.domain.models import (
    Asset,
    Channel,
    Claim,
    FactCheckReport,
    PackagingCandidate,
    PackagingContext,
    PackagingTournament,
    ResearchDossier,
    ResearchSource,
    Scene,
    Script,
    SEOPackage,
    ThumbnailPackage,
    TitleVariant,
    VideoProject,
)
from app.services.packaging_engine import (
    CandidateProposal,
    PackagingEngineService,
    PackagingError,
    PackagingGenerationOutput,
    TitleGroundingEvaluation,
    TitleProposition,
)
from app.services.thumbnail_designer import ThumbnailDesignerService
from app.services.youtube_publisher import YouTubePublisherService


def default_mock_grounding_evaluator(prompt: str) -> TitleGroundingEvaluation:
    """Deterministic semantic grounding evaluator for test suite."""
    m = re.search(r'CANDIDATE TITLE: "(.*?)"', prompt)
    title = m.group(1).lower() if m else ""

    # Factual corruption claim without verified support
    if "corruption" in title:
        return TitleGroundingEvaluation(
            topic_framing_only=False,
            propositions=[TitleProposition(text="Prevents Database Corruption", factual=True, supporting_claim_ids=[])],
        )

    # Factual lock removal claim without verified support
    if any(k in title for k in ("eliminates all write locks", "eliminates write locks", "eliminates locks", "no more locks")):
        return TitleGroundingEvaluation(
            topic_framing_only=False,
            propositions=[TitleProposition(text="Eliminates all write locks", factual=True, supporting_claim_ids=[])],
        )

    # Hallucinated claim ID
    if "hallucinated_claim" in title or "disk throughput" in title:
        return TitleGroundingEvaluation(
            topic_framing_only=False,
            propositions=[TitleProposition(text="Unsupported claim with fake ID", factual=True, supporting_claim_ids=["claim-hallucinated"])],
        )

    # Supported concurrent read/write claim
    if any(k in title for k in (
        "lets reads continue during writes",
        "allows simultaneous readers and writers",
        "concurrent readers and writers",
        "simultaneous readers and writers",
        "allows readers and writers",
        "permits concurrent",
    )):
        return TitleGroundingEvaluation(
            topic_framing_only=False,
            propositions=[TitleProposition(text="Permits concurrent read and write operations", factual=True, supporting_claim_ids=["clm-01"])],
        )

    # Default: topic framing only
    return TitleGroundingEvaluation(topic_framing_only=True, propositions=[])


@pytest.fixture
def test_setup():
    """Setup safe non-live repository, project, channel, and thumbnail designer."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / "test_packaging.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)

        channel = Channel(
            id="chan-pack-01",
            title="Database Internals",
            handle="@DBInternals",
            niche="Storage Engines",
            target_audience="Backend & Systems Engineers",
        )
        repo.save_channel(channel)

        # Create dummy visual assets
        asset1_path = tmp_path / "wal_diagram.png"
        img1 = Image.new("RGB", (1280, 720), color=(10, 25, 45))
        img1.save(asset1_path)

        asset2_path = tmp_path / "benchmark_chart.png"
        img2 = Image.new("RGB", (1280, 720), color=(20, 35, 60))
        img2.save(asset2_path)

        asset1 = Asset(
            id="ast-visual-01",
            project_id="proj-pack-01",
            asset_type=AssetType.IMAGE,
            file_path=str(asset1_path),
            source_url="file://wal_diagram.png",
            license_type="PROPRIETARY",
            content_sha256="sha-asset-1",
        )
        asset2 = Asset(
            id="ast-visual-02",
            project_id="proj-pack-01",
            asset_type=AssetType.IMAGE,
            file_path=str(asset2_path),
            source_url="file://benchmark_chart.png",
            license_type="PROPRIETARY",
            content_sha256="sha-asset-2",
        )

        script = Script(
            id="scr-pack-01",
            title="Mastering SQLite WAL Concurrency",
            hook="Why do multi-threaded SQLite writes cause database lock errors?",
            scenes=[
                Scene(
                    scene_number=1,
                    visual_description="A multi-threaded database application crashing with SQLiteBusyException.",
                    narration="If your application writes concurrently to SQLite, you will eventually hit database is locked.",
                    duration_seconds=5.0,
                ),
                Scene(
                    scene_number=2,
                    visual_description="Diagram of the Write-Ahead Log buffer allowing concurrent reads during writes.",
                    narration="WAL mode completely separates write append transactions from reader snapshot isolation.",
                    duration_seconds=10.0,
                ),
            ],
            total_word_count=180,
            estimated_duration_seconds=60.0,
        )

        project = VideoProject(
            id="proj-pack-01",
            channel_id=channel.id,
            title="Mastering SQLite WAL Concurrency",
            format=PlatformFormat.LONG_FORM_16_9,
            state=VideoLifecycleState.CREATED,
            script=script,
            assets=[asset1, asset2],
        )
        repo.save_video_project(project)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
        repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)

        claim1 = Claim(
            id="clm-01",
            project_id=project.id,
            statement="WAL permits readers and a writer to proceed concurrently without blocking.",
            verified=True,
            verdict=ClaimVerificationVerdict.VERIFIED,
            confidence_score=0.99,
        )

        dossier = ResearchDossier(
            id="dos-pack-01",
            topic_id="top-01",
            summary="WAL permits readers and a writer to proceed concurrently without lock contention.",
            sources=[
                ResearchSource(
                    id="src-01",
                    url="https://sqlite.org/wal.html",
                    title="Write-Ahead Logging",
                    content_sha256="hash-wal",
                    license_type="Public Domain",
                    content_snapshot="WAL permits readers and a writer to proceed concurrently without lock contention.",
                )
            ],
            claims=[claim1],
        )
        repo.save_research_dossier(project.id, dossier)

        fact_report = FactCheckReport(
            id="fcr-pack-01",
            project_id=project.id,
            claims=[claim1],
            verified_count=1,
            failed_count=0,
            overall_verdict=QualityStatus.PASSED,
            audit_summary="All claims grounded in official SQLite specification.",
        )
        repo.save_fact_check_report(fact_report)

        thumb_dir = tmp_path / "thumbnails"
        thumb_designer = ThumbnailDesignerService(repo, thumb_dir)

        yield repo, project, channel, thumb_designer, tmp_path


# --- TEST 1: Generic Template Removal ---
def test_generic_template_removal(test_setup):
    """Assert normal packaging generation does NOT return the 3 old generic templates."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="How SQLite Handles Concurrent Readers and Writers",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Clear direct engineering explanation.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Understanding SQLite WAL Mechanics",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="WAL MECHANICS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrasts lock assumptions.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite Scale Write Concurrency?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="WRITE SCALE?",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry into performance boundaries.",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL concurrency",
    )

    assert len(tournament.candidates) == 3
    titles = [c.title for c in tournament.candidates]

    assert "The Secret Truth About SQLite WAL concurrency" not in titles
    assert "How SQLite WAL concurrency Changes Everything (Fast Guide)" not in titles
    assert "Is SQLite WAL concurrency Actually Overrated?" not in titles
    assert titles[0] == "How SQLite Handles Concurrent Readers and Writers"
    assert tournament.selected_candidate_id in ("cand-1", "cand-2", "cand-3")


# --- TEST 2 (AUDIT CASE A): Unsupported Declarative Claim with Keyword Overlap Rejected ---
def test_unsupported_declarative_claim_with_keyword_overlap_rejected(test_setup):
    """Candidate 'SQLite WAL Prevents Database Corruption' contains keyword but no supporting claim -> REJECTED."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="SQLite WAL Prevents Database Corruption",  # Keyword overlap, plausible, but unsupported!
                        title_strategy="PROVOCATIVE_CLAIM",
                        thumbnail_headline="NO CORRUPTION",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="High stakes reliability claim.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Understanding SQLite WAL Concurrency",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Supported architectural explanation.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="How SQLite WAL Works Under Load",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="UNDER LOAD",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Inquiry into reader concurrency.",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    cand1 = next(c for c in tournament.candidates if c.id == "cand-1")
    assert cand1.passed_gates is False
    assert cand1.truth_status == TitleTruthStatus.UNSUPPORTED
    assert "Prevents Database Corruption" in cand1.rejection_reason or "corruption" in cand1.rejection_reason.lower()
    assert cand1.quality_score == 0.0

    # Ensure unsupported candidate CANNOT win
    assert tournament.selected_candidate_id != "cand-1"
    assert tournament.selected_candidate_id in ("cand-2", "cand-3")


# --- TEST 3 (AUDIT CASE B): Unsupported Presupposition inside Why Question Rejected ---
def test_unsupported_presupposition_inside_why_question_rejected(test_setup):
    """Candidate 'Why SQLite WAL Eliminates All Write Locks' starts with Why but has false presupposition -> REJECTED."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-why",
                        title="Why SQLite WAL Eliminates All Write Locks",  # False presupposition in question!
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NO MORE LOCKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Contrasts locking mechanisms.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Understanding SQLite WAL Storage",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL STORAGE",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct storage value.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="How SQLite WAL Coordinates Transactions",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="TRANSACTIONS",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Inquiry into coordination.",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    cand_why = next(c for c in tournament.candidates if "Eliminates All Write Locks" in c.title)
    assert cand_why.id == "cand-1"
    assert cand_why.passed_gates is False
    assert cand_why.truth_status == TitleTruthStatus.UNSUPPORTED
    assert cand_why.quality_score == 0.0
    assert tournament.selected_candidate_id != cand_why.id


# --- TEST 4 (AUDIT CASE C): Real Semantic Paraphrase Supported with Claim ID ---
def test_verified_semantic_paraphrase_supported_with_claim_id(test_setup):
    """'How SQLite WAL Lets Reads Continue During Writes' semantically entails verified claim clm-01 -> SUPPORTED."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=MockReasoningBackend(handler=lambda p, s: default_mock_grounding_evaluator(p)),
        output_dir=tmp_path / "output",
    )
    context = service.build_packaging_context(project.id)

    status, passed, reason, claim_id = service._validate_title_truth(
        title="How SQLite WAL Lets Reads Continue During Writes",
        context=context,
    )

    assert passed is True
    assert status == TitleTruthStatus.SUPPORTED
    assert reason is None
    assert claim_id == "clm-01"


# --- TEST 5 (AUDIT CASE D): Hallucinated Supporting Claim ID Rejected by Server ---
def test_hallucinated_claim_id_rejected_by_server(test_setup):
    """Model provides supporting_claim_ids=['claim-hallucinated'] -> Server rejects authority."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def hallucinated_evaluator(prompt: str) -> TitleGroundingEvaluation:
        return TitleGroundingEvaluation(
            topic_framing_only=False,
            propositions=[
                TitleProposition(
                    text="Increases Disk Throughput by 400%",
                    factual=True,
                    supporting_claim_ids=["claim-hallucinated"],  # Not in context.verified_claim_ids!
                )
            ],
        )

    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=MockReasoningBackend(handler=lambda p, s: hallucinated_evaluator(p)),
        output_dir=tmp_path / "output",
    )
    context = service.build_packaging_context(project.id)

    status, passed, reason, claim_id = service._validate_title_truth(
        title="SQLite WAL Greatly Increases Disk Throughput",
        context=context,
    )

    assert passed is False
    assert status == TitleTruthStatus.UNSUPPORTED
    assert "without verified claim support" in reason
    assert claim_id is None


# --- TEST 6 (AUDIT CASE E): Backend Grounding Failure Fails Closed for Factual Title ---
def test_backend_grounding_failure_on_factual_title_fails_closed(test_setup):
    """When backend grounding raises an error on a factual proposition, candidate fails closed."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    class FailingBackend(MockReasoningBackend):
        def generate_structured(self, prompt, schema_cls):
            raise RuntimeError("Backend LLM timeout during semantic grounding.")

    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=FailingBackend(),
        output_dir=tmp_path / "output",
    )
    context = service.build_packaging_context(project.id)

    status, passed, reason, claim_id = service._validate_title_truth(
        title="SQLite WAL Allows Complete Parallel Writing",
        context=context,
    )

    assert passed is False
    assert status == TitleTruthStatus.UNSUPPORTED
    assert "Semantic grounding evaluation unavailable" in reason
    assert claim_id is None


# --- TEST 7: Hard Gates: Title Length and Headline Words ---
def test_title_and_headline_hard_constraints(test_setup):
    """Title > 100 chars or headline > 4 words rejected by deterministic gates."""
    repo, project, channel, thumb_designer, tmp_path = test_setup
    service = PackagingEngineService(repository=repo, thumbnail_designer=thumb_designer)

    context = PackagingContext(
        project_id=project.id,
        channel_id=channel.id,
        primary_keyword="SQLite WAL",
        target_audience="Engineers",
        video_topic="SQLite WAL",
        summary="Summary",
    )

    # 1. Title too long
    cand_long = PackagingCandidate(
        id="cand-long",
        title="This Is An Absurdly Long Title About SQLite WAL Mode Concurrency That Far Exceeds One Hundred Characters And Must Be Rejected",
        title_strategy="DIRECT_VALUE",
        thumbnail_headline="TOO LONG",
        thumbnail_visual_strategy="FOCUS",
    )
    service._evaluate_hard_gates(cand_long, context, project.assets)
    assert cand_long.passed_gates is False
    assert "exceeds 100 characters" in cand_long.rejection_reason

    # 2. Headline too long
    cand_hd = PackagingCandidate(
        id="cand-hd",
        title="Valid Title Under Limit",
        title_strategy="DIRECT_VALUE",
        thumbnail_headline="THIS HEADLINE HAS FIVE WORDS TOTAL",  # 6 words > 4
        thumbnail_visual_strategy="FOCUS",
    )
    service._evaluate_hard_gates(cand_hd, context, project.assets)
    assert cand_hd.passed_gates is False
    assert "exceeds 4 words" in cand_hd.rejection_reason


# --- TEST 8: Asset Provenance Gate ---
def test_invented_asset_id_rejected(test_setup):
    """Model outputs hallucinated asset ID not in project assets -> Rejected."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    cand = PackagingCandidate(
        id="cand-fake-asset",
        title="How SQLite WAL Concurrency Works",
        title_strategy="DIRECT_VALUE",
        thumbnail_headline="CONCURRENCY",
        thumbnail_visual_strategy="FOCUS",
        subject_asset_id="asset_hallucinated",
    )
    context = PackagingContext(
        project_id=project.id,
        channel_id=channel.id,
        primary_keyword="SQLite WAL",
        target_audience="Engineers",
        video_topic="SQLite WAL",
        summary="Summary",
    )

    service = PackagingEngineService(repository=repo, thumbnail_designer=thumb_designer)
    service._evaluate_hard_gates(cand, context, project.assets)

    assert cand.passed_gates is False
    assert "Invented subject_asset_id" in cand.rejection_reason


# --- TEST 9: Complementarity Scoring ---
def test_complementarity_scoring(test_setup):
    """Complementary title + headline scores significantly higher than verbatim repetition."""
    repo, project, channel, thumb_designer, tmp_path = test_setup
    service = PackagingEngineService(repository=repo, thumbnail_designer=thumb_designer)

    score_repeat = service._evaluate_complementarity("Why SQLite WAL Changes Concurrency", "SQLITE WAL CHANGES CONCURRENCY")
    score_comp = service._evaluate_complementarity("Why SQLite WAL Changes Concurrency", "3 WRITERS?")

    assert score_comp > score_repeat
    assert score_repeat <= 0.30
    assert score_comp >= 0.85


# --- TEST 10 (AUDIT CASE H): Diversity Correction Succeeds and Re-Check Passes ---
def test_variant_diversity_correction_succeeds(test_setup):
    """Near-identical candidates trigger 1 correction pass; diverse results pass re-check with status CORRECTED."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    call_count = {"proposals": 0, "corrections": 0}

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            call_count["proposals"] += 1
            if "diversity correction" in prompt.lower() or "too similar" in prompt.lower():
                call_count["corrections"] += 1
                # Return legitimately diverse candidates on correction
                return PackagingGenerationOutput(
                    candidates=[
                        CandidateProposal(
                            id="cand-c1",
                            title="Understanding SQLite WAL Internals",
                            title_strategy="DIRECT_VALUE",
                            thumbnail_headline="WAL INTERNALS",
                            thumbnail_visual_strategy="FOCUS",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Architecture overview.",
                        ),
                        CandidateProposal(
                            id="cand-c2",
                            title="Why Traditional Lock Assumptions Fail",
                            title_strategy="CONTRAST_MECHANISM",
                            thumbnail_headline="LOCK TRAPS",
                            thumbnail_visual_strategy="SPLIT_CONTRAST",
                            subject_asset_id="ast-visual-02",
                            click_motivation_rationale="Contrasts lock models.",
                        ),
                        CandidateProposal(
                            id="cand-c3",
                            title="Can Concurrent Reads Interfere With Writes?",
                            title_strategy="CURIOSITY_QUESTION",
                            thumbnail_headline="ZERO CONFLICTS?",
                            thumbnail_visual_strategy="DETAIL_CROP",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Explores write interference.",
                        ),
                    ]
                )
            else:
                # Return near-duplicate candidates initially
                return PackagingGenerationOutput(
                    candidates=[
                        CandidateProposal(
                            id="cand-1",
                            title="How SQLite WAL Handles Concurrency",
                            title_strategy="DIRECT_VALUE",
                            thumbnail_headline="HOW WAL WORKS",
                            thumbnail_visual_strategy="FOCUS",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Explanation",
                        ),
                        CandidateProposal(
                            id="cand-2",
                            title="How SQLite WAL Handles Concurrency Better",
                            title_strategy="DIRECT_VALUE",
                            thumbnail_headline="HOW WAL WORKS",
                            thumbnail_visual_strategy="FOCUS",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Explanation",
                        ),
                        CandidateProposal(
                            id="cand-3",
                            title="How SQLite WAL Handles Concurrent Access",
                            title_strategy="DIRECT_VALUE",
                            thumbnail_headline="HOW WAL WORKS",
                            thumbnail_visual_strategy="FOCUS",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Explanation",
                        ),
                    ]
                )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    assert tournament.status == PackagingTournamentStatus.CORRECTED
    assert call_count["corrections"] == 1
    titles = [c.title for c in tournament.candidates]
    assert len(set(titles)) == 3


# --- TEST 11 (AUDIT CASE I): Diversity Correction Fails Closed on Second Failure ---
def test_variant_diversity_correction_fails_closed(test_setup):
    """Near-duplicates that remain too similar after 1 correction pass raise PackagingError without overwriting downstream."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    # Pre-save valid SEOPackage and ThumbnailPackage to verify failure isolation
    initial_seo = SEOPackage(
        id="seo-safe-01",
        project_id=project.id,
        primary_keyword="SQLite WAL",
        title_variants=[
            TitleVariant(
                angle=TitleVariantType.DIRECT_VALUE,
                title="Preserved Valid Original Title",
                predicted_ctr_rationale="Initial rationale",
            )
        ],
        selected_title="Preserved Valid Original Title",
        description="Preserved description",
        pinned_comment="Preserved comment",
    )
    repo.save_seo_package(initial_seo)

    thumb_16 = tmp_path / "init_16_9.jpg"
    thumb_9 = tmp_path / "init_9_16.jpg"
    Image.new("RGB", (1280, 720), (1, 1, 1)).save(thumb_16)
    Image.new("RGB", (1080, 1920), (1, 1, 1)).save(thumb_9)
    initial_thumb = ThumbnailPackage(
        id="thumb-safe-01",
        project_id=project.id,
        file_path_16_9=str(thumb_16),
        file_path_9_16=str(thumb_9),
        headline_text="INITIAL THUMB",
        content_sha256="sha-initial-valid",
        provenance={"created_by": "test"},
    )
    repo.save_thumbnail_package(initial_thumb)

    def stubborn_duplicate_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            # Intentionally return near-duplicates on BOTH initial and correction passes
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="How SQLite WAL Handles Concurrency",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL CONCURRENCY",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Explanation",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Handles Concurrency Better",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL CONCURRENCY",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Explanation",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="How SQLite WAL Handles Concurrent Access",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL CONCURRENCY",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Explanation",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=stubborn_duplicate_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    with pytest.raises(PackagingError) as exc_info:
        service.run_tournament(
            project_id=project.id,
            primary_keyword="SQLite WAL",
        )

    assert "PACKAGING_DIVERSITY_UNRESOLVED" in str(exc_info.value)

    # Assert DOWNSTREAM IS UNTOUCHED: no overwrite occurred!
    reloaded_seo = repo.get_seo_package(project.id)
    assert reloaded_seo.selected_title == "Preserved Valid Original Title"

    reloaded_thumb = repo.get_thumbnail_package(project.id)
    assert reloaded_thumb.headline_text == "INITIAL THUMB"
    assert reloaded_thumb.content_sha256 == "sha-initial-valid"


# --- TEST 12 (AUDIT CASE F): Visual Strategy Reaches Renderer and Records Requested vs Actual ---
def test_thumbnail_visual_strategy_execution_and_metadata(test_setup):
    """Candidate visual strategy reaches renderer and records requested, actual, and fallback reason."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-focus",
                        title="Understanding SQLite WAL Architecture",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL FOCUS",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Focus layout",
                    ),
                    CandidateProposal(
                        id="cand-split",
                        title="Why SQLite WAL Changes Traditional Concurrency",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="SPLIT WAL",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-01",
                        supporting_asset_ids=["ast-visual-02"],
                        click_motivation_rationale="Split layout",
                    ),
                    CandidateProposal(
                        id="cand-crop",
                        title="Can SQLite WAL Handle Heavy Loads?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="DETAIL CROP",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Crop layout",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    c_focus = tournament.candidates[0]
    c_split = tournament.candidates[1]
    c_crop = tournament.candidates[2]

    assert [c.id for c in tournament.candidates] == ["cand-1", "cand-2", "cand-3"]
    assert c_focus.requested_visual_strategy == "FOCUS"
    assert c_focus.actual_visual_strategy == "FOCUS"

    assert c_split.requested_visual_strategy == "SPLIT_CONTRAST"
    assert c_split.actual_visual_strategy == "SPLIT_CONTRAST"

    assert c_crop.requested_visual_strategy == "DETAIL_CROP"
    assert c_crop.actual_visual_strategy == "DETAIL_CROP"


# --- TEST 13 (AUDIT CASE J & K): Selected Thumbnail SHA Equals Real File SHA and Missing File Fails ---
def test_selected_thumbnail_sha_equals_real_file_sha_and_missing_fails(test_setup):
    """Selected winner ThumbnailPackage content_sha256 matches actual file bytes, and missing file fails activation."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite WAL",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL MODE",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Value",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="HOW IT WORKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Mechanism",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Key Concepts in SQLite WAL",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="KEY CONCEPTS",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    winner = next(c for c in tournament.candidates if c.id == tournament.selected_candidate_id)
    active_thumb = repo.get_thumbnail_package(project.id)

    # 1. Real file verification: SHA matches actual file bytes
    with open(active_thumb.file_path_16_9, "rb") as f:
        real_file_sha = hashlib.sha256(f.read()).hexdigest()

    assert active_thumb.content_sha256 == real_file_sha
    assert active_thumb.content_sha256 == winner.content_sha256
    assert active_thumb.content_sha256 != hashlib.sha256(winner.title.encode("utf-8")).hexdigest()

    # 2. Missing file failure: cannot activate winner without real rendered file
    fake_winner = PackagingCandidate(
        id="cand-nofile",
        title="Valid Title",
        title_strategy="DIRECT_VALUE",
        thumbnail_headline="NO FILE",
        thumbnail_visual_strategy="FOCUS",
        content_sha256="fake-sha",
        file_path_16_9=str(tmp_path / "non_existent_file.jpg"),
    )
    with pytest.raises(PackagingError) as exc_info:
        service._update_downstream_contracts(project, fake_winner, tournament)
    assert "Cannot activate winner without real rendered thumbnail file" in str(exc_info.value)


# --- TEST 14 (AUDIT CASE L): Rejected Candidate Not Exposed as Normal Publishable Title Variant ---
def test_rejected_candidate_not_exposed_as_publishable_title_variant(test_setup):
    """When a candidate is rejected by a gate, SEOPackage.title_variants excludes it, preserving only valid variants."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    # Pre-save initial SEOPackage
    seo_init = SEOPackage(
        id="seo-init",
        project_id=project.id,
        primary_keyword="SQLite WAL",
        title_variants=[],
        selected_title="Old Title",
        description="Desc",
        pinned_comment="Initial comment",
    )
    repo.save_seo_package(seo_init)

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-rejected",
                        title="SQLite WAL Prevents Database Corruption",  # Unsupported claim -> Rejected!
                        title_strategy="PROVOCATIVE_CLAIM",
                        thumbnail_headline="CORRUPTION",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Unsupported claim",
                    ),
                    CandidateProposal(
                        id="cand-valid-1",
                        title="Understanding SQLite WAL Internals",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL INTERNALS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Valid direct value",
                    ),
                    CandidateProposal(
                        id="cand-valid-2",
                        title="How SQLite WAL Coordinates Writes",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="HOW IT WORKS",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Valid question",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    # Tournament retains rejected candidate for durability audit
    assert len(tournament.candidates) == 3
    assert any("Corruption" in c.title and not c.passed_gates for c in tournament.candidates)
    assert tournament.candidates[0].id == "cand-1"
    assert not tournament.candidates[0].passed_gates

    # But SEOPackage.title_variants MUST only contain valid gate-passed candidates!
    seo_after = repo.get_seo_package(project.id)
    assert len(seo_after.title_variants) == 2
    pub_titles = [v.title for v in seo_after.title_variants]
    assert "SQLite WAL Prevents Database Corruption" not in pub_titles
    assert "Understanding SQLite WAL Internals" in pub_titles
    assert "How SQLite WAL Coordinates Writes" in pub_titles

    # Format readiness is False because not all 3 variants passed gates
    assert tournament.native_ab_eligible is False


# --- TEST 15 (AUDIT CASE M): Shorts Format Packaging ---
def test_shorts_format_packaging_semantics(test_setup):
    """PlatformFormat.SHORTS_9_16 generates 9:16 thumbnail artifact, native_ab_eligible == False, no Studio automation."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    shorts_script = Script(
        id="scr-shorts-01",
        title="SQLite in 60 Seconds",
        hook=project.script.hook,
        scenes=project.script.scenes,
        total_word_count=project.script.total_word_count,
        estimated_duration_seconds=project.script.estimated_duration_seconds,
    )
    shorts_project = VideoProject(
        id="proj-shorts-01",
        channel_id=channel.id,
        title="SQLite in 60 Seconds",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=shorts_script,
        assets=project.assets,
    )
    repo.save_video_project(shorts_project)
    repo.update_project_state(shorts_project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(shorts_project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(shorts_project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(shorts_project.id, to_state=VideoLifecycleState.VERIFIED)

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite in 60 Seconds",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="SQLITE 60S",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Shorts value",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works Fast",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="FAST WAL",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Fast breakdown",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite Handle Quick Writes?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="QUICK WRITES",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=shorts_project.id,
        primary_keyword="SQLite Shorts",
    )

    assert tournament is not None
    assert len(tournament.candidates) == 3
    assert tournament.native_ab_eligible is False

    # 9:16 vertical thumbnail artifact generated and valid
    winner = next(c for c in tournament.candidates if c.id == tournament.selected_candidate_id)
    assert Path(winner.file_path_9_16).exists()
    with Image.open(winner.file_path_9_16) as img9:
        assert img9.size == (1080, 1920)


# --- TEST 16 (AUDIT CASE N): Long-Form Local Readiness Semantics with Zero External Calls ---
def test_longform_ab_readiness_no_external_calls(test_setup, monkeypatch):
    """Eligible long-form project has native_ab_eligible == True locally with zero external network or Studio API calls."""
    def guarded_connect(sock, address):
        pytest.fail(f"Outbound network attempted during offline tournament: connect({address})")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite WAL Concurrency",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL MODE",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct value",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works Under Load",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrast mechanism",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite Scale Write Concurrency?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="WRITE SCALE",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL Concurrency",
    )

    # Local packaging readiness flag is True
    assert tournament.native_ab_eligible is True
    assert len(tournament.candidates) == 3


# --- TEST 17 (AUDIT CASE O): Channel made_for_kids Disqualifies Native A/B ---
def test_made_for_kids_disqualifies_native_ab(test_setup):
    """When channel.made_for_kids == 1, native_ab_eligible is strictly False regardless of format."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    # Set channel to made_for_kids
    kids_channel = Channel(
        id="chan-kids-01",
        title="Kids Coding Fun",
        handle="@KidsCoding",
        niche="Education",
        target_audience="Kids",
        made_for_kids=1,
    )
    repo.save_channel(kids_channel)

    kids_script = Script(
        id="scr-kids-01",
        title="SQLite for Kids",
        hook="How does SQLite WAL work for kids?",
        scenes=project.script.scenes,
        total_word_count=project.script.total_word_count,
        estimated_duration_seconds=project.script.estimated_duration_seconds,
    )

    kids_project = VideoProject(
        id="proj-kids-01",
        channel_id=kids_channel.id,
        title="SQLite for Kids",
        format=PlatformFormat.LONG_FORM_16_9,
        state=VideoLifecycleState.CREATED,
        script=kids_script,
        assets=project.assets,
    )
    repo.save_video_project(kids_project)
    repo.update_project_state(kids_project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(kids_project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(kids_project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(kids_project.id, to_state=VideoLifecycleState.VERIFIED)

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite WAL",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL MODE",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct value",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrast mechanism",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite Scale Concurrency?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="WRITE SCALE",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=kids_project.id,
        primary_keyword="SQLite WAL",
    )

    assert tournament.native_ab_eligible is False


# --- TEST 18: Reasoning Backend Failure Activates Grounded Fallback ---
def test_reasoning_backend_failure_activates_grounded_fallback(test_setup):
    """Reasoning backend failure during candidate proposal activates conservative grounded fallback candidates."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    class CrashingBackend(MockReasoningBackend):
        def generate_structured(self, prompt, schema_cls):
            raise RuntimeError("Backend connection exploded!")

    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=CrashingBackend(),
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    assert tournament is not None
    assert len(tournament.candidates) == 3
    # Check that conservative fallback titles were used
    titles = [c.title for c in tournament.candidates]
    assert "Understanding SQLite WAL" in titles
    assert "How SQLite WAL Works" in titles
    assert "Key Concepts in SQLite WAL" in titles


# --- TEST 19: Tournament Durability and Reload from Fresh Repository ---
def test_tournament_durability_and_reload(test_setup):
    """Reloading tournament from fresh DB recovers all 3 candidates with scores, rationales, and statuses."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite WAL",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="GUIDE",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Guide rationale",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NO LOCKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrast rationale",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Key Concepts in SQLite WAL",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="BLOCKING?",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Curiosity rationale",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output",
    )

    orig_tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL",
    )

    fresh_repo = SQLiteRepository(repo.db_path)
    reloaded = fresh_repo.get_packaging_tournament(project.id)

    assert reloaded is not None
    assert reloaded.id == orig_tournament.id
    assert reloaded.selected_candidate_id == orig_tournament.selected_candidate_id
    assert len(reloaded.candidates) == 3

    losing_cands = [c for c in reloaded.candidates if c.id != reloaded.selected_candidate_id]
    assert len(losing_cands) == 2
    for loser in losing_cands:
        assert loser.title is not None
        assert loser.thumbnail_visual_strategy is not None
        assert loser.quality_score > 0.0
        assert len(loser.score_breakdown) > 0
        assert loser.file_path_16_9 is not None
        assert Path(loser.file_path_16_9).exists()


# --- TEST 20: Deterministic Ranking and Tie Breaking ---
def test_deterministic_ranking_and_tie_breaking(test_setup):
    """Given fixed candidate proposals, tournament selection is strictly deterministic across runs."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite WAL",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL MODE",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct architectural explanation.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrasting traditional locking.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Key Concepts in SQLite WAL",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="KEY CONCEPTS",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry into write concurrency.",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service1 = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output1",
    )
    t1 = service1.run_tournament(project.id, "SQLite WAL")

    service2 = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output2",
    )
    t2 = service2.run_tournament(project.id, "SQLite WAL")

    assert t1.selected_candidate_id == t2.selected_candidate_id
    assert t1.selection_reason == t2.selection_reason
    scores_1 = [c.quality_score for c in t1.candidates]
    scores_2 = [c.quality_score for c in t2.candidates]
    assert scores_1 == scores_2


# --- TEST 21: Initial Generation Duplicate Backend IDs Canonicalized & Render Integrity ---
def test_initial_generation_duplicate_backend_ids_canonicalized_and_render_integrity(test_setup):
    """ReasoningBackend duplicate candidate IDs are completely ignored; canonical IDs cand-1..3 are enforced and files/SHAs remain uncorrupted."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",  # duplicate ID
                        title="Understanding SQLite WAL",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL EXPLAINED",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct explanation.",
                    ),
                    CandidateProposal(
                        id="cand-1",  # duplicate ID
                        title="How SQLite WAL Works",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="SPLIT_CONTRAST",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrasting mechanism.",
                    ),
                    CandidateProposal(
                        id="cand-1",  # duplicate ID
                        title="Key Concepts in SQLite WAL",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="KEY CONCEPTS",
                        thumbnail_visual_strategy="DETAIL_CROP",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Architectural concepts.",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output_dup_id",
    )

    tournament = service.run_tournament(project.id, "SQLite WAL")

    # Section 7 assertions
    candidate_ids = [c.id for c in tournament.candidates]
    assert candidate_ids == ["cand-1", "cand-2", "cand-3"]
    assert len(set(candidate_ids)) == 3

    # Section 8 assertions: File integrity & SHA match
    paths_16_9 = [c.file_path_16_9 for c in tournament.candidates]
    paths_9_16 = [c.file_path_9_16 for c in tournament.candidates]
    assert len(set(paths_16_9)) == 3
    assert len(set(paths_9_16)) == 3

    for cand in tournament.candidates:
        assert Path(cand.file_path_16_9).exists()
        file_bytes = Path(cand.file_path_16_9).read_bytes()
        actual_sha = hashlib.sha256(file_bytes).hexdigest()
        assert actual_sha == cand.content_sha256

    # Section 9 assertions: Selected ID resolves uniquely before and after SQLite reload
    matches = [c for c in tournament.candidates if c.id == tournament.selected_candidate_id]
    assert len(matches) == 1

    fresh_repo = SQLiteRepository(repo.db_path)
    reloaded = fresh_repo.get_packaging_tournament(project.id)
    assert reloaded is not None
    assert reloaded.selected_candidate_id == tournament.selected_candidate_id
    reloaded_matches = [c for c in reloaded.candidates if c.id == reloaded.selected_candidate_id]
    assert len(reloaded_matches) == 1
    assert [c.id for c in reloaded.candidates] == ["cand-1", "cand-2", "cand-3"]


# --- TEST 22: Diversity Correction Duplicate Backend IDs Canonicalized ---
def test_diversity_correction_duplicate_backend_ids_canonicalized(test_setup):
    """When initial candidates fail diversity, correction backend returning duplicate IDs is canonicalized server-side."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            if "PREVIOUS CANDIDATES" in prompt:
                # Correction output returning duplicate IDs
                return PackagingGenerationOutput(
                    candidates=[
                        CandidateProposal(
                            id="whatever-same-id",
                            title="Understanding SQLite WAL In Depth",
                            title_strategy="DIRECT_VALUE",
                            thumbnail_headline="IN DEPTH",
                            thumbnail_visual_strategy="FOCUS",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Distinct outcome.",
                        ),
                        CandidateProposal(
                            id="whatever-same-id",
                            title="Why SQLite WAL Changes Storage Concurrency",
                            title_strategy="CONTRAST_MECHANISM",
                            thumbnail_headline="CONCURRENCY",
                            thumbnail_visual_strategy="SPLIT_CONTRAST",
                            subject_asset_id="ast-visual-02",
                            click_motivation_rationale="Distinct contrast.",
                        ),
                        CandidateProposal(
                            id="whatever-same-id",
                            title="Can SQLite WAL Prevent Blocking at Scale?",
                            title_strategy="CURIOSITY_QUESTION",
                            thumbnail_headline="AT SCALE?",
                            thumbnail_visual_strategy="DETAIL_CROP",
                            subject_asset_id="ast-visual-01",
                            click_motivation_rationale="Distinct inquiry.",
                        ),
                    ]
                )
            # Initial output: near-duplicate titles that fail diversity
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="How SQLite WAL Handles Concurrency",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="CONCURRENCY",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Variant 1.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Handles Concurrency Better",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="CONCURRENCY",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Variant 2.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="How SQLite WAL Handles Concurrent Access",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="CONCURRENCY",
                        thumbnail_visual_strategy="FOCUS",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Variant 3.",
                    ),
                ]
            )
        if schema_cls == TitleGroundingEvaluation:
            return default_mock_grounding_evaluator(prompt)
        raise ValueError(f"Unhandled: {schema_cls}")

    backend = MockReasoningBackend(handler=mock_handler)
    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=backend,
        output_dir=tmp_path / "output_div_dup_id",
    )

    tournament = service.run_tournament(project.id, "SQLite WAL")

    assert tournament.status == PackagingTournamentStatus.CORRECTED
    candidate_ids = [c.id for c in tournament.candidates]
    assert candidate_ids == ["cand-1", "cand-2", "cand-3"]
    assert len(set(candidate_ids)) == 3

    for cand in tournament.candidates:
        assert Path(cand.file_path_16_9).exists()
        file_bytes = Path(cand.file_path_16_9).read_bytes()
        assert hashlib.sha256(file_bytes).hexdigest() == cand.content_sha256
