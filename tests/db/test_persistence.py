"""Unit tests verifying SQLite persistence, foreign key constraints, state CAS atomicity, and restart persistence."""

import gc
from pathlib import Path
import pytest
import tempfile
import sqlite3

from app.domain.enums import VideoLifecycleState, PlatformFormat, PublicationStatus, PrivacyStatus
from app.domain.models import Channel, VideoProject, Script, Scene, PublicationJob, Asset, AssetType
from app.domain.state_machine import InvalidStateTransitionError
from app.db.schema import init_database
from app.db.repository import SQLiteRepository, StateConcurrencyError


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


def test_pragma_foreign_keys_enabled(temp_db_path: Path) -> None:
    """Verify foreign key constraints are actively enabled on repository connections."""
    repo = SQLiteRepository(temp_db_path)
    with repo._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        fk_status = cursor.fetchone()[0]
    assert fk_status == 1


def test_orphan_insert_rejected_by_foreign_keys(temp_db_path: Path) -> None:
    """Verify foreign key enforcement blocks inserting orphaned records without parents."""
    repo = SQLiteRepository(temp_db_path)

    # 1. VideoProject referencing non-existent channel_id should fail
    orphan_project = VideoProject(
        id="proj-orphan-01",
        channel_id="non-existent-channel",
        title="Orphan Project",
        format=PlatformFormat.SHORTS_9_16,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_video_project(orphan_project)

    # Now create valid channel
    channel = Channel(
        id="chan-001",
        title="AI Engineering Hub",
        handle="@AIEngineeringHub",
        niche="AI",
        target_audience="Devs",
    )
    repo.save_channel(channel)

    # 2. PublicationJob referencing non-existent project_id should fail
    orphan_job = PublicationJob(
        id="pub-orphan-01",
        project_id="non-existent-project",
        channel_id="chan-001",
        status=PublicationStatus.PENDING,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_publication_job(orphan_job)

    # 3. Asset referencing non-existent project_id should fail direct insert
    with repo._get_connection() as conn:
        cursor = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO assets (id, project_id, asset_type, file_path, source_url, license_type, content_sha256, created_at)
                VALUES ('ast-orphan', 'non-existent-proj', 'VIDEO_CLIP', 'path.mp4', 'url', 'lic', 'sha', '2026-01-01T00:00:00')
                """
            )
            conn.commit()


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
    repo1 = SQLiteRepository(temp_db_path)
    channel = Channel(
        id="chan-001",
        title="AI Engineering Hub",
        handle="@AIEngineeringHub",
        niche="AI",
        target_audience="Devs",
    )
    repo1.save_channel(channel)

    project = VideoProject(
        id="proj-101",
        channel_id="chan-001",
        title="Local AI Agents with Antigravity",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
    )
    repo1.save_video_project(project)

    # Valid step 1: CREATED -> RESEARCHING
    repo1.update_project_state(
        project_id="proj-101",
        to_state=VideoLifecycleState.RESEARCHING,
        reason="Starting research",
    )

    # Valid step 2: RESEARCHING -> PLANNED
    repo1.update_project_state(
        project_id="proj-101",
        to_state=VideoLifecycleState.PLANNED,
        reason="Topic selected",
    )

    # Session 2: Fresh repository instance connecting to same SQLite file
    repo2 = SQLiteRepository(temp_db_path)
    loaded_project = repo2.get_video_project("proj-101")
    assert loaded_project is not None
    assert loaded_project.id == "proj-101"
    assert loaded_project.state == VideoLifecycleState.PLANNED
    assert loaded_project.format == PlatformFormat.SHORTS_9_16

    # Verify state transition log persisted
    history = repo2.get_state_history("proj-101")
    assert len(history) == 2
    assert history[0]["to_state"] == "RESEARCHING"
    assert history[1]["to_state"] == "PLANNED"


def test_update_project_state_validates_db_state_and_rejects_invalid_transition(temp_db_path: Path) -> None:
    """Verify update_project_state reads DB state and rejects illegal transitions."""
    repo = SQLiteRepository(temp_db_path)
    channel = Channel(id="chan-001", title="AI Hub", handle="@AI", niche="AI", target_audience="Devs")
    repo.save_channel(channel)

    project = VideoProject(
        id="proj-state-01",
        channel_id="chan-001",
        title="State Test",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    # Attempt illegal transition: CREATED -> PUBLISHED
    with pytest.raises(InvalidStateTransitionError) as exc_info:
        repo.update_project_state(
            project_id="proj-state-01",
            to_state=VideoLifecycleState.PUBLISHED,
            reason="Illegal leap",
        )
    assert "Invalid transition from CREATED to PUBLISHED" in str(exc_info.value)

    # Verify DB state remained CREATED and no transition history was inserted
    reloaded = repo.get_video_project("proj-state-01")
    assert reloaded.state == VideoLifecycleState.CREATED
    assert len(repo.get_state_history("proj-state-01")) == 0


def test_update_project_state_cas_expected_state_protection(temp_db_path: Path) -> None:
    """Verify CAS protection when optional expected_from_state does not match actual DB state."""
    repo = SQLiteRepository(temp_db_path)
    channel = Channel(id="chan-001", title="AI Hub", handle="@AI", niche="AI", target_audience="Devs")
    repo.save_channel(channel)

    project = VideoProject(
        id="proj-cas-01",
        channel_id="chan-001",
        title="CAS Test",
        state=VideoLifecycleState.CREATED,
    )
    repo.save_video_project(project)

    # Advance state to RESEARCHING
    repo.update_project_state(project_id="proj-cas-01", to_state=VideoLifecycleState.RESEARCHING)

    # Try advancing to PLANNED claiming expected state is CREATED (stale caller view)
    with pytest.raises(StateConcurrencyError) as exc_info:
        repo.update_project_state(
            project_id="proj-cas-01",
            to_state=VideoLifecycleState.PLANNED,
            expected_from_state=VideoLifecycleState.CREATED,
        )
    assert "Expected state CREATED does not match current state RESEARCHING" in str(exc_info.value)


def test_publication_queue_queries(temp_db_path: Path) -> None:
    """Verify publication queue retrieval orders pending and scheduled jobs."""
    repo = SQLiteRepository(temp_db_path)
    channel = Channel(id="chan-001", title="AI Hub", handle="@AI", niche="AI", target_audience="Devs")
    repo.save_channel(channel)

    proj1 = VideoProject(id="proj-101", channel_id="chan-001", title="P1", state=VideoLifecycleState.APPROVED)
    proj2 = VideoProject(id="proj-102", channel_id="chan-001", title="P2", state=VideoLifecycleState.APPROVED)
    repo.save_video_project(proj1)
    repo.save_video_project(proj2)

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
