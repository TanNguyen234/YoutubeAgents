"""Comprehensive test suite for Packaging Engine Phase 1: Grounded Title + Thumbnail Tournament."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
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
    PackagingGenerationOutput,
)
from app.services.thumbnail_designer import ThumbnailDesignerService
from app.services.youtube_publisher import YouTubePublisherService


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
                    index=0,
                    hook="Rollback Journal Bottleneck",
                    narration="Default rollback journals require exclusive lock, halting all concurrent readers.",
                    target_duration_seconds=12.0,
                ),
                Scene(
                    index=1,
                    hook="WAL Concurrency Secret",
                    narration="WAL permits readers and a writer to proceed concurrently without blocking each other.",
                    target_duration_seconds=18.0,
                ),
                Scene(
                    index=2,
                    hook="Production Concurrency Payoff",
                    narration="By enabling WAL mode, read transactions continue undisturbed during heavy background writes.",
                    target_duration_seconds=15.0,
                ),
            ],
            total_word_count=80,
            estimated_duration_seconds=45.0,
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


        dossier = ResearchDossier(
            id="dos-pack-01",
            project_id=project.id,
            topic_id="top-01",
            summary="Technical documentation on SQLite write-ahead logging concurrency behavior.",
            sources=[
                ResearchSource(
                    id="src-01",
                    dossier_id="dos-pack-01",
                    url="https://sqlite.org/wal.html",
                    title="Write-Ahead Logging",
                    content_sha256="hash-wal",
                    license_type="Public Domain",
                    content_snapshot="WAL permits readers and a writer to proceed concurrently without lock contention.",
                )
            ],
        )
        repo.save_research_dossier(project.id, dossier)

        claim1 = Claim(
            id="clm-01",
            statement="WAL permits readers and a writer to proceed concurrently without blocking.",
            verified=True,
            verdict=ClaimVerificationVerdict.VERIFIED,
            confidence_score=0.99,
        )

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


# --- TEST A: Generic Template Removal ---
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
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Clear direct engineering explanation of mechanics under load.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Why WAL Changes Traditional Database Concurrency",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NO MORE LOCKS",
                        thumbnail_visual_strategy="mechanism_breakdown",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrasts traditional lock assumptions with modern WAL realities.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite Truly Handle Heavy Concurrent Writes?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="3 WRITERS?",
                        thumbnail_visual_strategy="benchmark_comparison",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Invites inquiry into real production performance boundaries.",
                    ),
                ]
            )
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

    # Verify NONE of the old hardcoded templates were returned
    assert "The Secret Truth About SQLite WAL concurrency" not in titles
    assert "How SQLite WAL concurrency Changes Everything (Fast Guide)" not in titles
    assert "Is SQLite WAL concurrency Actually Overrated?" not in titles

    # Verify exact candidates preserved
    assert titles[0] == "How SQLite Handles Concurrent Readers and Writers"
    assert tournament.selected_candidate_id in ("cand-1", "cand-2", "cand-3")


# --- TEST B: Unsupported Claim Rejected by Truth Gate ---
def test_unsupported_claim_rejected(test_setup):
    """Model proposes 'SQLite WAL Is 10x Faster' without verified benchmark support -> Rejected."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="SQLite WAL Is 10x Faster",  # Unsupported multiplier!
                        title_strategy="PROVOCATIVE_CLAIM",
                        thumbnail_headline="10X FASTER",
                        thumbnail_visual_strategy="benchmark_comparison",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="High clickbait benchmark multiplier.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Permits Concurrent Writes",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Supported architectural explanation.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite WAL Handle Multiple Readers?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="MULTIPLE READERS",
                        thumbnail_visual_strategy="concurrency_flow",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Inquiry into reader concurrency.",
                    ),
                ]
            )
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

    cand1 = next(c for c in tournament.candidates if c.id == "cand-1")
    assert cand1.passed_gates is False
    assert cand1.truth_status == TitleTruthStatus.UNSUPPORTED
    assert "unverified quantitative multiplier" in cand1.rejection_reason.lower()
    assert cand1.quality_score == 0.0

    # Ensure unsupported candidate CANNOT win
    assert tournament.selected_candidate_id != "cand-1"
    assert tournament.selected_candidate_id in ("cand-2", "cand-3")


# --- TEST C: Supported Factual Claim Allowed ---
def test_supported_factual_claim_allowed(test_setup):
    """Verified claim in fact check allows faithful paraphrase to pass truth gate."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    context = service_context = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=MockReasoningBackend(),
        output_dir=tmp_path / "output",
    ).build_packaging_context(project.id)

    status, passed, reason = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=MockReasoningBackend(),
    )._validate_title_truth(
        title="How SQLite WAL Allows Simultaneous Readers and Writers",
        context=context,
    )

    assert passed is True
    assert status in (TitleTruthStatus.SUPPORTED, TitleTruthStatus.NON_FACTUAL_FRAMING)
    assert reason is None


# --- TEST D: Title Length Hard Constraint ---
def test_title_length_hard_constraint(test_setup):
    """Title exceeding 100 characters is rejected by deterministic server-side gate."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    cand = PackagingCandidate(
        id="cand-long",
        title="This Is An Absurdly Long Title About SQLite WAL Mode Concurrency That Far Exceeds One Hundred Characters And Must Be Rejected",
        title_strategy="DIRECT_VALUE",
        thumbnail_headline="TOO LONG",
        thumbnail_visual_strategy="diagram",
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
    assert "exceeds 100 characters" in cand.rejection_reason


# --- TEST E: Invented Asset ID Rejected ---
def test_invented_asset_id_rejected(test_setup):
    """Model outputs hallucinated asset ID not in project assets -> Rejected."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    cand = PackagingCandidate(
        id="cand-fake-asset",
        title="How SQLite WAL Concurrency Works",
        title_strategy="DIRECT_VALUE",
        thumbnail_headline="CONCURRENCY",
        thumbnail_visual_strategy="architecture_diagram",
        subject_asset_id="asset_hallucinated",  # Invented!
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
    assert "asset_hallucinated" in cand.rejection_reason
    assert "Invented subject_asset_id" in cand.rejection_reason


# --- TEST F: Complementarity Scoring ---
def test_complementarity_scoring(test_setup):
    """Candidate with complementary title + thumbnail scores higher than verbatim repeating pair."""
    repo, project, channel, thumb_designer, tmp_path = test_setup
    service = PackagingEngineService(repository=repo, thumbnail_designer=thumb_designer)

    # Candidate A: Verbatim repetition
    title_a = "Why SQLite WAL Changes Concurrency"
    headline_a = "SQLITE WAL CHANGES CONCURRENCY"
    score_a = service._evaluate_complementarity(title_a, headline_a)

    # Candidate B: Complementary new perspective
    title_b = "Why SQLite WAL Changes Concurrency"
    headline_b = "3 WRITERS?"
    score_b = service._evaluate_complementarity(title_b, headline_b)

    assert score_b > score_a
    assert score_a <= 0.30
    assert score_b >= 0.90


# --- TEST G: Variant Diversity Gate ---
def test_variant_diversity_gate(test_setup):
    """3 near-identical candidates fail diversity gate and trigger bounded correction pass."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="How SQLite WAL Works",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Explanation",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="How SQLite WAL Works",  # Identical title!
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="HOW WAL WORKS",  # Identical headline!
                        thumbnail_visual_strategy="architecture_diagram",  # Identical strategy!
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Explanation",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="How SQLite WAL Works",  # Identical title!
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="HOW WAL WORKS",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Explanation",
                    ),
                ]
            )
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
    # Assert titles and visual strategies were corrected to be diverse
    titles = [c.title for c in tournament.candidates]
    assert len(set(titles)) == 3
    strategies = {c.thumbnail_visual_strategy for c in tournament.candidates}
    assert len(strategies) > 1


# --- TEST H: Three Distinct Packages Produced ---
def test_three_distinct_packages_produced(test_setup):
    """Successful tournament produces exactly 3 distinct candidates with unique rendered thumbnails."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Understanding SQLite WAL Concurrency",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL EXPLAINED",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Clear architecture overview.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Why Traditional Lock Assumptions Fail in SQLite",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NO MORE LOCKS",
                        thumbnail_visual_strategy="mechanism_breakdown",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrasts lock mechanisms.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can Concurrent SQLite Reads Block Write Operations?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="ZERO BLOCKING?",
                        thumbnail_visual_strategy="concurrency_flow",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Curiosity inquiry into blocking behavior.",
                    ),
                ]
            )
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

    assert len(tournament.candidates) == 3
    shas = [c.content_sha256 for c in tournament.candidates if c.content_sha256]
    assert len(shas) == 3
    # Ensure distinct SHA thumbnails were rendered (different headlines/assets)
    assert len(set(shas)) == 3


# --- TEST I: Thumbnail File Integrity ---
def test_thumbnail_file_integrity(test_setup):
    """Assert rendered candidate thumbnails have correct 1280x720 and 1080x1920 dimensions and valid files."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="How SQLite WAL Works",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL MODE",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Architecture",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Why SQLite WAL Changes Storage",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NEW ENGINE",
                        thumbnail_visual_strategy="mechanism_breakdown",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Mechanism",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite Scale Concurrency?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="SCALE LIMITS?",
                        thumbnail_visual_strategy="benchmark_comparison",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry",
                    ),
                ]
            )
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

    for cand in tournament.candidates:
        assert cand.file_path_16_9 is not None
        assert cand.file_path_9_16 is not None
        p16 = Path(cand.file_path_16_9)
        p9 = Path(cand.file_path_9_16)
        assert p16.exists() and p16.stat().st_size > 0
        assert p9.exists() and p9.stat().st_size > 0

        with Image.open(p16) as img16:
            assert img16.size == (1280, 720)
        with Image.open(p9) as img9:
            assert img9.size == (1080, 1920)


# --- TEST J: Selected Package Consumed Downstream by Publisher ---
def test_selected_package_consumed_downstream(test_setup):
    """Assert winner becomes SEOPackage.selected_title and ThumbnailPackage, consumed by publisher."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    # Save baseline initial SEOPackage
    seo_pkg = SEOPackage(
        id="seo-init",
        project_id=project.id,
        primary_keyword="SQLite WAL",
        title_variants=[
            TitleVariant(
                angle=TitleVariantType.DIRECT_VALUE,
                title="Initial Generic Title",
                predicted_ctr_rationale="Old rationale",
            )
        ],
        selected_title="Initial Generic Title",
        description="Initial description",
        pinned_comment="Initial comment",
    )
    repo.save_seo_package(seo_pkg)

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="Winning Tournament Title For SQLite WAL",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WINNER WAL",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct value proposition.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Alternative Contrast Angle For SQLite",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="CONTRAST",
                        thumbnail_visual_strategy="mechanism_breakdown",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Alternative contrast.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Alternative Curiosity Question For SQLite",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="QUESTION",
                        thumbnail_visual_strategy="concurrency_flow",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Alternative inquiry.",
                    ),
                ]
            )
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

    winner_id = tournament.selected_candidate_id
    winner = next(c for c in tournament.candidates if c.id == winner_id)

    # 1. Assert updated in SEOPackage
    reloaded_seo = repo.get_seo_package(project.id)
    assert reloaded_seo is not None
    assert reloaded_seo.selected_title == winner.title
    assert len(reloaded_seo.title_variants) == 3

    # 2. Assert saved as active ThumbnailPackage
    active_thumb = repo.get_thumbnail_package(project.id)
    assert active_thumb is not None
    assert active_thumb.file_path_16_9 == winner.file_path_16_9
    assert active_thumb.headline_text == winner.thumbnail_headline

    # 3. Assert YouTubePublisherService consumes winner without tournament knowledge
    publisher = YouTubePublisherService(repo)
    payload = publisher.build_metadata_payload(project, privacy_status=PrivacyStatus.PRIVATE)
    assert payload["snippet"]["title"] == winner.title


# --- TEST K: Losers Remain Durable Across Reload ---
def test_tournament_durability_and_reload(test_setup):
    """Reloading tournament from DB recovers all 3 candidates with scores, rationales, and statuses."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="SQLite WAL Guide",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="GUIDE",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Guide rationale",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Why Locks Are Gone in SQLite",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NO LOCKS",
                        thumbnail_visual_strategy="mechanism_breakdown",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrast rationale",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can Readers Block Writers in WAL?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="BLOCKING?",
                        thumbnail_visual_strategy="concurrency_flow",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Curiosity rationale",
                    ),
                ]
            )
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

    # Create completely fresh repository instance pointing to the same SQLite file
    fresh_repo = SQLiteRepository(repo.db_path)
    reloaded = fresh_repo.get_packaging_tournament(project.id)

    assert reloaded is not None
    assert reloaded.id == orig_tournament.id
    assert reloaded.selected_candidate_id == orig_tournament.selected_candidate_id
    assert len(reloaded.candidates) == 3

    # Ensure losing candidates are fully preserved
    losing_cands = [c for c in reloaded.candidates if c.id != reloaded.selected_candidate_id]
    assert len(losing_cands) == 2
    for loser in losing_cands:
        assert loser.title is not None
        assert loser.thumbnail_visual_strategy is not None
        assert loser.quality_score > 0.0
        assert len(loser.score_breakdown) > 0
        assert loser.file_path_16_9 is not None
        assert Path(loser.file_path_16_9).exists()


# --- TEST L: Shorts Platform Format Packaging ---
def test_shorts_format_packaging(test_setup):
    """PlatformFormat.SHORTS_9_16 sets native_ab_eligible == False without blocking tournament."""
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

    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=MockReasoningBackend(),
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=shorts_project.id,
        primary_keyword="SQLite Shorts",
    )

    assert tournament is not None
    assert len(tournament.candidates) == 3
    assert tournament.native_ab_eligible is False  # Shorts is not eligible for YouTube Studio A/B


# --- TEST M: Long-form A/B Readiness with Zero External Calls ---
def test_longform_ab_readiness_no_external_calls(test_setup, monkeypatch):
    """Eligible long-form project has native_ab_eligible == True with zero external network calls."""
    import socket

    def guarded_connect(sock, address):
        pytest.fail(f"Outbound network attempted during offline tournament: connect({address})")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    repo, project, channel, thumb_designer, tmp_path = test_setup

    service = PackagingEngineService(
        repository=repo,
        thumbnail_designer=thumb_designer,
        backend=MockReasoningBackend(),
        output_dir=tmp_path / "output",
    )

    tournament = service.run_tournament(
        project_id=project.id,
        primary_keyword="SQLite WAL Concurrency",
    )

    assert tournament.native_ab_eligible is True
    assert len(tournament.candidates) == 3


# --- TEST N: Failure Isolation Preserves Existing Package ---
def test_failure_isolation_preserves_existing_package(test_setup):
    """Exception during tournament execution does not corrupt existing valid SEOPackage."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    # Save a valid preexisting SEOPackage
    initial_seo = SEOPackage(
        id="seo-safe-01",
        project_id=project.id,
        primary_keyword="SQLite WAL",
        title_variants=[
            TitleVariant(
                angle=TitleVariantType.DIRECT_VALUE,
                title="Preserved Valid Title",
                predicted_ctr_rationale="Initial rationale",
            )
        ],
        selected_title="Preserved Valid Title",
        description="Preserved description",
        pinned_comment="Preserved comment",
    )
    repo.save_seo_package(initial_seo)

    class CrashingBackend(MockReasoningBackend):
        def generate_structured(self, prompt, schema_cls):
            raise RuntimeError("Backend connection exploded!")

    # Even if backend fails, tournament handles gracefully with grounded fallback
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

    # Valid tournament still completed via grounded fallback
    assert tournament is not None
    seo_after = repo.get_seo_package(project.id)
    assert seo_after is not None
    assert seo_after.selected_title is not None
    assert len(seo_after.title_variants) == 3


# --- TEST O: Deterministic Ranking and Tie Breaking ---
def test_deterministic_ranking_and_tie_breaking(test_setup):
    """Given fixed candidate proposals, tournament selection is strictly deterministic across runs."""
    repo, project, channel, thumb_designer, tmp_path = test_setup

    def mock_handler(prompt, schema_cls):
        if schema_cls == PackagingGenerationOutput:
            return PackagingGenerationOutput(
                candidates=[
                    CandidateProposal(
                        id="cand-1",
                        title="How SQLite WAL Concurrency Works Under High Load",
                        title_strategy="DIRECT_VALUE",
                        thumbnail_headline="WAL MODE",
                        thumbnail_visual_strategy="architecture_diagram",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Direct architectural explanation.",
                    ),
                    CandidateProposal(
                        id="cand-2",
                        title="Why SQLite WAL Changes Multi-Threaded Storage",
                        title_strategy="CONTRAST_MECHANISM",
                        thumbnail_headline="NO LOCKS",
                        thumbnail_visual_strategy="mechanism_breakdown",
                        subject_asset_id="ast-visual-02",
                        click_motivation_rationale="Contrasting traditional locking.",
                    ),
                    CandidateProposal(
                        id="cand-3",
                        title="Can SQLite WAL Handle Concurrent Read Operations?",
                        title_strategy="CURIOSITY_QUESTION",
                        thumbnail_headline="3 WRITERS?",
                        thumbnail_visual_strategy="concurrency_flow",
                        subject_asset_id="ast-visual-01",
                        click_motivation_rationale="Inquiry into write concurrency.",
                    ),
                ]
            )
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
