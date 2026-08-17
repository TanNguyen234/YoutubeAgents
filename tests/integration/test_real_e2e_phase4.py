"""Real E2E integration test executing Stages 1 through 5 with live HTTP and Antigravity CLI reasoning."""

import gc
import tempfile
from pathlib import Path
import pytest

from app.core.backend import AntigravityCLIBackend
from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import ClaimVerificationVerdict, QualityStatus, VideoLifecycleState
from app.domain.models import Channel
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent


@pytest.fixture
def real_pipeline_and_repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "real_e2e.db"
        evidence_dir = Path(tmp_dir) / "evidence"
        init_database(db_path)
        repo = SQLiteRepository(db_path)
        backend = AntigravityCLIBackend(timeout_seconds=120)
        researcher = ResearchAgent(evidence_storage_dir=evidence_dir)

        pipeline = BrainPipeline(
            repository=repo,
            backend=backend,
            research_agent=researcher,
        )
        yield pipeline, repo
        gc.collect()


@pytest.mark.live
def test_real_live_e2e_sqlite_wal_mode(real_pipeline_and_repo):
    """REAL E2E Topic 1: Live HTTP fetch from sqlite.org, Antigravity topic scoring, script generation, and fact checking."""
    pipeline, repo = real_pipeline_and_repo
    channel = Channel(
        id="chan-sqlite-real-01",
        title="Database Systems Engineering",
        handle="@DBEngineering",
        niche="Database Internals",
        target_audience="Backend engineers and systems programmers",
    )
    repo.save_channel(channel)

    project, report = pipeline.run_stage_1_to_5(
        project_id="proj-live-01",
        channel=channel,
        keyword="Mastering SQLite WAL Mode Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    assert project.state == VideoLifecycleState.VERIFIED
    assert project.script is not None
    assert len(project.script.scenes) >= 2
    assert report.overall_verdict == QualityStatus.PASSED
    assert report.verified_count >= 1
    assert report.failed_count == 0
    for claim in report.claims:
        assert claim.verdict == ClaimVerificationVerdict.VERIFIED
        assert claim.cited_url == "https://sqlite.org/wal.html"
