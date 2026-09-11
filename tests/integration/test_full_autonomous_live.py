"""End-to-end integration test for the complete 15-stage YouTube Autopilot lifecycle."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import PrivacyStatus, QualityStatus, VideoLifecycleState
from app.domain.models import Channel
from app.services.pipeline_brain import BrainPipeline


@pytest.mark.live
def test_full_15_stage_autonomous_lifecycle():
    """Verify that BrainPipeline executes all 15 stages end-to-end without mocking domain services."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "e2e_full_test.db"
        init_database(db_path)
        repo = SQLiteRepository(db_path)

        channel = Channel(
            id="chan-full-e2e",
            title="Systems & Concurrency Engineering",
            handle="@SystemsEngineering",
            niche="Distributed Systems & Storage",
            target_audience="Engineers",
        )
        repo.save_channel(channel)

        pipeline = BrainPipeline(repository=repo)

        # Execute all 15 stages
        receipt = pipeline.run_full_autonomous_lifecycle(
            project_id="proj-full-e2e-01",
            channel=channel,
            keyword="Mastering SQLite WAL Concurrency",
            seed_urls=[
                "https://sqlite.org/wal.html",
                "https://sqlite.org/atomiccommit.html",
            ],
            enable_gflow=False,  # Use deterministic visual cards for fast isolated test runner
            auto_approve=True,
            approved_privacy=PrivacyStatus.PRIVATE,
            operator_name="AutomatedTester",
            voice="en-US-GuyNeural",
            simulate_analytics_views=2000,
        )

        # Stage 1-5 checks
        assert receipt["fact_report"]["overall_verdict"] == "PASSED"
        assert receipt["fact_report"]["verified_count"] >= 1

        # Stage 6-11 checks
        assert receipt["qa_result"]["status"] == "PASSED"
        assert -16.0 <= receipt["qa_result"]["loudness_lufs"] <= -12.0
        assert receipt["qa_result"]["duration_seconds"] > 0
        assert Path(receipt["render_manifest"]["final_video_path"]).exists()

        # Stage 12 checks
        assert receipt["review_record"]["action"] == "APPROVE"
        assert receipt["review_record"]["operator"] == "AutomatedTester"

        # Stage 13 checks: Truthful state handling (PENDING / BLOCKED in DRY_RUN without live OAuth tokens)
        assert receipt["publication_job"]["status"] in ("COMPLETED", "SCHEDULED", "PENDING")
        assert receipt["final_state"] in ("PUBLISHED", "SCHEDULED", "BLOCKED")

        # Stage 14 checks
        assert receipt["analytics_snapshot"]["views"] == 2000
        assert receipt["analytics_snapshot"]["watch_time_hours"] > 0

        # Stage 15 checks
        assert receipt["strategy_feedback"]["total_snapshots"] >= 1
        assert len(receipt["strategy_feedback"]["recommendations"]) >= 1
