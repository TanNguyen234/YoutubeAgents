"""Unit tests verifying the Verification Gate prevents ungrounded scripts from reaching VERIFIED."""

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
    ResearchDossier,
    ResearchSource,
    Scene,
    ScriptSections,
)
from app.services.claim_extractor import ClaimExtractionOutput, ClaimExtractor
from app.services.fact_checker import ClaimEntailmentOutput, FactChecker
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent, ResearchFetchError
from app.services.script_generator import ScriptGenerator
from app.services.script_writer import ScriptWriter
from app.services.topic_evaluator import TopicEvaluationOutput, TopicEvaluator
from app.services.topic_strategist import TopicStrategist


@pytest.fixture
def mock_pipeline_and_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "gate_test.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)

        def mock_backend_handler(prompt, schema_cls):
            if schema_cls == TopicEvaluationOutput:
                return TopicEvaluationOutput(
                    demand=8.5,
                    freshness=8.0,
                    competition=6.0,
                    channel_fit=9.0,
                    originality=8.0,
                    evidence_quality=9.0,
                    production_feasibility=8.5,
                    historical_fit=8.0,
                    rationale="High developer demand for database architecture tutorials",
                    score_reasons={"demand": "High interest", "channel_fit": "Exact match"},
                )
            elif schema_cls == ScriptSections:
                return ScriptSections(
                    hook="Why is your database locking up under load?",
                    intro="SQLite default mode blocks writes during reads.",
                    segments=[
                        Scene(
                            index=0,
                            hook="WAL mode solves this.",
                            narration="In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                            target_duration_seconds=12.0,
                            visual_prompt="WAL architecture diagram",
                        ),
                        Scene(
                            index=1,
                            hook="Unverified claim.",
                            narration="Enabling WAL mode will boost your database throughput 10x immediately with zero disk IO.",
                            target_duration_seconds=10.0,
                            visual_prompt="Fake graph",
                        ),
                    ],
                    cta="Subscribe for more database engineering tips.",
                    voiceover_text="In SQLite WAL mode, readers do not block writers and writers do not block readers. Enabling WAL mode will boost your database throughput 10x immediately with zero disk IO.",
                    estimated_duration=25.0,
                )
            elif schema_cls == ClaimExtractionOutput:
                return ClaimExtractionOutput(
                    claims=[
                        "In SQLite WAL mode, readers do not block writers and writers do not block readers.",
                        "Enabling WAL mode will boost your database throughput 10x immediately with zero disk IO.",
                    ]
                )
            elif schema_cls == ClaimEntailmentOutput:
                if "10x immediately" in prompt:
                    return ClaimEntailmentOutput(
                        is_supported=False,
                        confidence=0.1,
                        verdict=ClaimVerificationVerdict.REMOVE,
                        cited_url="NONE",
                        cited_excerpt="NONE",
                        notes="The claim of 10x throughput and zero disk IO is unsubstantiated by SQLite WAL documentation.",
                    )
                else:
                    return ClaimEntailmentOutput(
                        is_supported=True,
                        confidence=0.98,
                        verdict=ClaimVerificationVerdict.VERIFIED,
                        cited_url="https://sqlite.org/wal.html",
                        cited_excerpt="readers do not block writers and writers do not block readers",
                        notes="Directly supported by SQLite documentation.",
                    )
            raise ValueError(f"Unhandled mock schema: {schema_cls}")

        backend = MockReasoningBackend(handler=mock_backend_handler)

        class MockResearchAgent(ResearchAgent):
            def fetch_source_from_url(self, source_id, url, title=None, authors=None, license_type="UNKNOWN"):
                if "invalid" in url:
                    raise ResearchFetchError(url, "404 Not Found", status_code=404)
                return ResearchSource(
                    id=source_id,
                    url=url,
                    final_url=url,
                    http_status=200,
                    title="Write-Ahead Logging in SQLite",
                    authors=["SQLite Team"],
                    content_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                    content_snapshot="In SQLite WAL mode, readers do not block writers and writers do not block readers. Write-ahead logging writes changes to a separate file.",
                    license_type="Public Domain",
                )

        pipeline = BrainPipeline(
            repository=repo,
            backend=backend,
            research_agent=MockResearchAgent(),
        )
        yield pipeline, repo
        gc.collect()


def test_unsupported_claim_blocks_verified_and_transitions_to_failed(mock_pipeline_and_repo):
    """Regression test: A script with an ungrounded claim ('10x throughput') MUST NOT reach VERIFIED."""
    pipeline, repo = mock_pipeline_and_repo
    channel = Channel(
        id="chan-001",
        title="Database Engineering",
        handle="@DBEng",
        niche="Database Systems",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    project, report = pipeline.run_stage_1_to_5(
        project_id="proj-gate-01",
        channel=channel,
        keyword="SQLite WAL Mode Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
        max_rewrite_attempts=0,  # Do not loop rewrite
    )

    # Invariant: Project MUST NOT reach VERIFIED; must transition to FAILED
    assert project.state == VideoLifecycleState.FAILED
    assert report.overall_verdict == QualityStatus.FAILED
    assert report.failed_count >= 1
    unsupported = [c for c in report.claims if not c.verified]
    assert len(unsupported) >= 1
    assert "10x immediately" in unsupported[0].statement


def test_network_fetch_failure_transitions_to_blocked(mock_pipeline_and_repo):
    """Test: When network research fails, project transitions to BLOCKED, not FAILED or stranding."""
    pipeline, repo = mock_pipeline_and_repo
    channel = Channel(
        id="chan-001",
        title="Database Engineering",
        handle="@DBEng",
        niche="Database Systems",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    with pytest.raises(ResearchFetchError):
        pipeline.run_stage_1_to_5(
            project_id="proj-blocked-01",
            channel=channel,
            keyword="Invalid Source Topic",
            seed_urls=["https://sqlite.org/invalid_page.html"],
        )

    project = repo.get_video_project("proj-blocked-01")
    assert project.state == VideoLifecycleState.BLOCKED
