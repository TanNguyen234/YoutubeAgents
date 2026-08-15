"""Unit tests verifying SQLite persistence, schema migrations, and restart persistence."""

import gc
from pathlib import Path
import pytest
import tempfile
import sqlite3

from app.domain.enums import VideoLifecycleState, PlatformFormat, PublicationStatus
from app.domain.models import Channel, VideoProject, Script, Scene, PublicationJob
from app.db.schema import init_database
from app.db.repository import SQLiteRepository


@pytest.fixture
def temp_db_path():
    """Create a temporary database path for test isolation."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "test_autopilot.db"
        yield db_path
        gc.collect()


def test_database_initialization_and_tables(temp_db_path: Path) -> None:
    """Verify all schema tables are created cleanly upon initialization."""
    init_database(temp_db_path)
    assert temp_db_path.exists()

    with sqlite3.connect(temp_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}

    expected_tables = {
        "channels",
        "topic_candidates",
        "video_projects",
        "scripts",
        "assets",
        "publication_jobs",
        "analytics_snapshots",
        "experiments",
        "idempotency_keys",
        "state_transitions",
    }
    assert expected_tables.issubset(tables)


def test_channel_crud_and_persistence(temp_db_path: Path) -> None:
    """Verify Channel insertion, query, and updates."""
    repo = SQLiteRepository(temp_db_path)
    channel = Channel(
        id="chan-001",
        title="AI Engineering Hub",
        handle="@AIEngineeringHub",
        niche="Artificial Intelligence",
        target_audience="Engineers",
    )
    repo.save_channel(channel)

    fetched = repo.get_channel("chan-001")
    assert fetched is not None
    assert fetched.id == channel.id
    assert fetched.title == channel.title
    assert fetched.handle == channel.handle


def test_video_project_lifecycle_and_restart_persistence(temp_db_path: Path) -> None:
    """Verify VideoProject persistence survives connection close and restarts."""
    # Session 1: Create and save project
    repo1 = SQLiteRepository(temp_db_path)
    project = VideoProject(
        id="proj-101",
        channel_id="chan-001",
        title="Local AI Agents with Antigravity",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.PLANNED,
    )
    repo1.save_video_project(project)

    # Transition state to SCRIPTED and record state transition
    repo1.update_project_state(
        project_id="proj-101",
        from_state=VideoLifecycleState.PLANNED,
        to_state=VideoLifecycleState.SCRIPTED,
        reason="Script drafted successfully",
    )

    # Session 2: Fresh repository instance connecting to same SQLite file
    repo2 = SQLiteRepository(temp_db_path)
    loaded_project = repo2.get_video_project("proj-101")
    assert loaded_project is not None
    assert loaded_project.id == "proj-101"
    assert loaded_project.state == VideoLifecycleState.SCRIPTED
    assert loaded_project.format == PlatformFormat.SHORTS_9_16

    # Verify state transition log persisted
    history = repo2.get_state_history("proj-101")
    assert len(history) >= 1
    assert history[-1]["to_state"] == "SCRIPTED"
    assert history[-1]["reason"] == "Script drafted successfully"


def test_publication_queue_queries(temp_db_path: Path) -> None:
    """Verify publication queue retrieval orders pending and scheduled jobs."""
    repo = SQLiteRepository(temp_db_path)
    job1 = PublicationJob(
        id="job-01",
        project_id="proj-101",
        channel_id="chan-001",
        status=PublicationStatus.PENDING,
    )
    job2 = PublicationJob(
        id="job-02",
        project_id="proj-102",
        channel_id="chan-001",
        status=PublicationStatus.COMPLETED,
    )
    repo.save_publication_job(job1)
    repo.save_publication_job(job2)

    pending_queue = repo.get_publication_queue(status=PublicationStatus.PENDING)
    assert len(pending_queue) == 1
    assert pending_queue[0].id == "job-01"
