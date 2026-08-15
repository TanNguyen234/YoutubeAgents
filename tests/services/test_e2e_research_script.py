"""Deterministic integration test executing Stages 1 through 5 using mock reasoning backend (TEST contract)."""

import gc
import tempfile
from pathlib import Path
import pytest

from app.core.backend import MockReasoningBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import ClaimVerificationVerdict, QualityStatus, VideoLifecycleState
from app.domain.models import (
    Channel,
    Claim,
    ResearchSource,
    Scene,
    ScriptSections,
)
from app.services.claim_extractor import ClaimExtractionOutput
from app.services.fact_checker import ClaimEntailmentOutput
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent
from app.services.topic_evaluator import TopicEvaluationOutput


@pytest.fixture
def mock_brain_pipeline():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "unit_brain.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)

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
                    intro="By default, SQLite uses rollback journals which lock the database during writes.",
                    segments=[
                        Scene(
                            index=0,
                            hook="WAL mode solves this.",
                            narration="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                            target_duration_seconds=12.0,
                            visual_prompt="WAL architecture diagram",
                        ),
                    ],
                    cta="Subscribe for more database engineering tutorials.",
                    voiceover_text="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                    estimated_duration=15.0,
                )
            elif schema_cls == ClaimExtractionOutput:
                return ClaimExtractionOutput(
                    claims=["In SQLite WAL mode, readers do not block writers and writers do not block readers."]
                )
            elif schema_cls == ClaimEntailmentOutput:
                return ClaimEntailmentOutput(
                    is_supported=True,
                    confidence=0.98,
                    cited_url="https://sqlite.org/wal.html",
                    cited_excerpt="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                    rationale="Directly confirmed by SQLite documentation.",
                )
            raise ValueError(f"Unhandled schema: {schema_cls}")

        backend = MockReasoningBackend(handler=mock_handler)

        class FixtureResearchAgent(ResearchAgent):
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
            research_agent=FixtureResearchAgent(),
        )
        yield pipeline, repo
        gc.collect()


def test_deterministic_pipeline_execution(mock_brain_pipeline):
    """Verify end-to-end stage execution transitions to VERIFIED under valid mock conditions."""
    pipeline, repo = mock_brain_pipeline
    channel = Channel(
        id="chan-det-01",
        title="Database Systems Engineering",
        handle="@DBEngineering",
        niche="Database Internals",
        target_audience="Backend engineers",
    )
    repo.save_channel(channel)

    project, report = pipeline.run_stage_1_to_5(
        project_id="proj-det-01",
        channel=channel,
        keyword="Mastering SQLite WAL Mode",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    assert project.state == VideoLifecycleState.VERIFIED
    assert report.overall_verdict == QualityStatus.PASSED
    assert report.verified_count == 1
    assert report.failed_count == 0
    assert report.claims[0].verdict == ClaimVerificationVerdict.VERIFIED
