"""Full autonomous lifecycle contract and service interface integration tests."""

import gc
from pathlib import Path
import tempfile
import pytest

from app.core.backend import MockReasoningBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import ClaimVerificationVerdict, PrivacyStatus, QualityStatus, VideoLifecycleState
from app.domain.models import (
    Channel,
    ResearchSource,
    Scene,
    ScriptSections,
)
from app.services.claim_extractor import ClaimExtractionOutput
from app.services.editorial_calendar import EditorialCalendarService
from app.services.fact_checker import ClaimEntailmentOutput
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent
from app.services.seo_optimizer import SEOOptimizerService
from app.services.thumbnail_designer import ThumbnailDesignerService
from app.services.topic_evaluator import TopicEvaluationOutput


@pytest.fixture
def mock_lifecycle_setup():
    """Setup safe non-live backend, database, and repository for contract testing."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "contract_test.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)

        channel = Channel(
            id="chan-contract-01",
            title="Systems & Concurrency Engineering",
            handle="@SystemsEngineering",
            niche="Distributed Systems & Storage",
            target_audience="Engineers",
        )
        repo.save_channel(channel)

        def mock_handler(prompt, schema_cls):
            if schema_cls == TopicEvaluationOutput:
                return TopicEvaluationOutput(
                    demand=8.5,
                    freshness=7.5,
                    competition=6.0,
                    channel_fit=9.5,
                    originality=8.0,
                    evidence_quality=9.0,
                    production_feasibility=8.5,
                    historical_fit=8.5,
                    rationale="High developer demand for database architecture tutorials",
                    score_reasons={"demand": "8.5/10", "channel_fit": "9.5/10"},
                )
            elif schema_cls == ScriptSections:
                return ScriptSections(
                    hook="Why is your database locking up under load?",
                    intro="By default, SQLite uses rollback journals which lock during writes.",
                    segments=[
                        Scene(
                            index=0,
                            hook="WAL mode solves this.",
                            narration="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                            target_duration_seconds=10.0,
                            visual_prompt="WAL architecture diagram",
                        ),
                    ],
                    cta="Subscribe for the next episode on storage engines.",
                    voiceover_text="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                    estimated_duration=10.0,
                )
            elif schema_cls == ClaimExtractionOutput:
                return ClaimExtractionOutput(
                    claims=["In SQLite WAL mode, readers do not block writers and writers do not block readers."]
                )
            elif schema_cls == ClaimEntailmentOutput:
                return ClaimEntailmentOutput(
                    is_supported=True,
                    confidence=0.99,
                    cited_url="https://sqlite.org/wal.html",
                    cited_excerpt="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                    rationale="Confirmed by authoritative documentation.",
                )
            raise ValueError(f"Unhandled schema: {schema_cls}")

        backend = MockReasoningBackend(handler=mock_handler)

        class ContractResearchAgent(ResearchAgent):
            def fetch_source_from_url(self, source_id, url, title=None, authors=None, license_type="UNKNOWN"):
                return ResearchSource(
                    id=source_id,
                    url=url,
                    final_url=url,
                    http_status=200,
                    title="Write-Ahead Logging in SQLite",
                    authors=["SQLite Consortium"],
                    content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    content_snapshot="In SQLite WAL mode, readers do not block writers and writers do not block readers. Write-ahead logging uses a separate file.",
                    license_type="Public Domain",
                )

        pipeline = BrainPipeline(
            repository=repo,
            backend=backend,
            research_agent=ContractResearchAgent(),
        )

        yield pipeline, repo, channel, tmp_dir
        gc.collect()


def test_full_lifecycle_accepts_series_continuity(mock_lifecycle_setup):
    """Verify run_stage_1_to_5 and full lifecycle accept series_continuity without contract error."""
    pipeline, repo, channel, tmp_dir = mock_lifecycle_setup

    cal_service = EditorialCalendarService(repo)
    series = cal_service.register_series(
        channel_id=channel.id,
        title="SQLite Deep Dives",
        target_niche=channel.niche,
    )
    continuity = cal_service.get_episodic_continuity_context(series.id, 1)

    # Test run_stage_1_to_5 contract directly
    project, report = pipeline.run_stage_1_to_5(
        project_id="proj-cont-01",
        channel=channel,
        keyword="Mastering SQLite WAL Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
        series_continuity=continuity,
    )

    assert project.state == VideoLifecycleState.VERIFIED
    assert report.overall_verdict == QualityStatus.PASSED


def test_full_lifecycle_calls_real_seo_contract(mock_lifecycle_setup, monkeypatch):
    """Verify SEO packaging uses the canonical generate_and_save_seo_package with real research sources summary."""
    pipeline, repo, channel, tmp_dir = mock_lifecycle_setup

    seo_calls = []
    orig_method = SEOOptimizerService.generate_and_save_seo_package

    def spy_generate_and_save(self, project_id, primary_keyword, series_context=None, sources_summary=None):
        seo_calls.append({
            "project_id": project_id,
            "primary_keyword": primary_keyword,
            "series_context": series_context,
            "sources_summary": sources_summary,
        })
        return orig_method(self, project_id, primary_keyword, series_context, sources_summary)

    monkeypatch.setattr(SEOOptimizerService, "generate_and_save_seo_package", spy_generate_and_save)

    receipt = pipeline.run_full_autonomous_lifecycle(
        project_id="proj-seo-01",
        channel=channel,
        keyword="Mastering SQLite WAL Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
        series_title="Storage Mastery",
        enable_gflow=False,
        auto_approve=True,
        approved_privacy=PrivacyStatus.PRIVATE,
        operator_name="ContractTester",
        simulate_analytics_views=100,
    )

    assert len(seo_calls) == 1
    call = seo_calls[0]
    assert call["project_id"] == "proj-seo-01"
    assert call["primary_keyword"] == "Mastering SQLite WAL Concurrency"
    assert "https://sqlite.org/wal.html" in (call["sources_summary"] or "")
    assert receipt["seo_package"]["selected_title"] is not None


def test_full_lifecycle_constructs_thumbnail_designer_with_output_dir(mock_lifecycle_setup, monkeypatch):
    """Verify ThumbnailDesignerService is instantiated with deterministic output directory."""
    pipeline, repo, channel, tmp_dir = mock_lifecycle_setup

    init_calls = []
    orig_init = ThumbnailDesignerService.__init__

    def spy_init(self, repository, output_dir):
        init_calls.append({"repo": repository, "output_dir": output_dir})
        return orig_init(self, repository, output_dir)

    monkeypatch.setattr(ThumbnailDesignerService, "__init__", spy_init)

    receipt = pipeline.run_full_autonomous_lifecycle(
        project_id="proj-thumb-01",
        channel=channel,
        keyword="Mastering SQLite WAL Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
        enable_gflow=False,
        auto_approve=True,
        approved_privacy=PrivacyStatus.PRIVATE,
        operator_name="ContractTester",
    )

    assert len(init_calls) >= 1
    assert init_calls[0]["output_dir"] is not None
    assert "proj-thumb-01" in str(init_calls[0]["output_dir"])
    assert Path(receipt["thumbnail_package"]["file_path_16_9"]).exists()
    assert Path(receipt["thumbnail_package"]["file_path_9_16"]).exists()
