"""Tests for immutable canonical narration binding and state invariants in media production."""

import hashlib
from pathlib import Path
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import init_database
from app.domain.enums import PlatformFormat, VideoLifecycleState
from app.domain.models import Channel, Scene, Script, VideoProject
from app.media.pipeline import MediaProductionError, MediaProductionPipeline


@pytest.fixture
def test_repo(tmp_path: Path) -> SQLiteRepository:
    db_path = tmp_path / "test_binding.db"
    init_database(db_path)
    repo = SQLiteRepository(db_path)
    channel = Channel(
        id="chan-test",
        title="Test Channel",
        handle="@testchannel",
        niche="Tech",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    return repo


def test_production_rejects_unverified_project(test_repo: SQLiteRepository, tmp_path: Path):
    """Pipeline must refuse to render projects not in VERIFIED state."""
    script = Script(
        id="sc-01",
        title="SQLite Architecture",
        hook="How does SQLite achieve high concurrency?",
        scenes=[
            Scene(scene_index=0, narration="Scene one text.", hook="Hook", visual_prompt="Visual 1"),
        ],
        total_word_count=4,
        estimated_duration_seconds=5.0,
    )
    project = VideoProject(
        id="proj-unverified",
        channel_id="chan-test",
        title="SQLite Architecture",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    test_repo.save_video_project(project)
    test_repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    test_repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    test_repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)

    pipeline = MediaProductionPipeline(repository=test_repo, base_output_dir=tmp_path)
    with pytest.raises(MediaProductionError, match="Production requires project to be in VERIFIED"):
        pipeline.run_production(project_id="proj-unverified")


def test_canonical_narration_hash_binding(test_repo: SQLiteRepository):
    """Canonical narration hash must be deterministically tied to all script scenes."""
    script = Script(
        id="sc-02",
        title="WAL Mode",
        hook="SQLite WAL mode allows concurrent readers.",
        scenes=[
            Scene(scene_index=0, narration="Readers read from WAL index shared memory.", hook="Hook 1", visual_prompt="P1"),
            Scene(scene_index=1, narration="Writers append new frames without blocking readers.", hook="Hook 2", visual_prompt="P2"),
        ],
        total_word_count=18,
        estimated_duration_seconds=10.0,
    )
    canonical = script.get_canonical_narration()
    expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert "SQLite WAL mode allows concurrent readers." in canonical
    assert "Readers read from WAL index shared memory." in canonical
    assert "Writers append new frames without blocking readers." in canonical
    assert len(expected_hash) == 64


def test_canonical_narration_hash_changes_if_spoken_narration_changes():
    """Any alteration to spoken narration words must change the canonical SHA-256 hash."""
    script_a = Script(
        id="sc-03a",
        title="WAL Architecture",
        hook="How WAL works",
        scenes=[
            Scene(scene_index=0, narration="Exact original wording for scene zero.", hook="H0", visual_prompt="P0"),
        ],
        total_word_count=6,
        estimated_duration_seconds=3.0,
    )
    script_b = Script(
        id="sc-03b",
        title="WAL Architecture",
        hook="How WAL works",
        scenes=[
            Scene(scene_index=0, narration="Exact modified wording for scene zero.", hook="H0", visual_prompt="P0"),
        ],
        total_word_count=6,
        estimated_duration_seconds=3.0,
    )

    hash_a = hashlib.sha256(script_a.get_canonical_narration().encode("utf-8")).hexdigest()
    hash_b = hashlib.sha256(script_b.get_canonical_narration().encode("utf-8")).hexdigest()

    assert hash_a != hash_b


def test_canonical_narration_survives_repository_roundtrip(tmp_path: Path):
    """Persisting to SQLite and reloading from a fresh repository instance preserves exact canonical narration and SHA-256."""
    db_file = tmp_path / "roundtrip.db"
    init_database(db_file)

    repo_1 = SQLiteRepository(db_file)
    channel = Channel(
        id="chan-rt",
        title="Persistence Channel",
        handle="@perschannel",
        niche="Database Engineering",
        target_audience="Developers",
    )
    repo_1.save_channel(channel)

    script = Script(
        id="sc-rt-01",
        title="WAL Persistence Test",
        hook="Does WAL persistence work across restarts?",
        scenes=[
            Scene(scene_index=0, narration="Scene 1: Readers access the wal-index memory.", hook="H1", visual_prompt="P1"),
            Scene(scene_index=1, narration="Scene 2: Writers commit to disk sequentially.", hook="H2", visual_prompt="P2"),
        ],
        total_word_count=16,
        estimated_duration_seconds=8.0,
    )
    project = VideoProject(
        id="proj-rt-01",
        channel_id="chan-rt",
        title="WAL Persistence Test",
        format=PlatformFormat.SHORTS_9_16,
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo_1.save_video_project(project)
    original_canonical = script.get_canonical_narration()
    original_hash = hashlib.sha256(original_canonical.encode("utf-8")).hexdigest()

    # Discard repo_1, open fresh repository on same DB file
    repo_2 = SQLiteRepository(db_file)
    reloaded_project = repo_2.get_video_project("proj-rt-01")

    assert reloaded_project is not None
    assert reloaded_project.script is not None
    reloaded_canonical = reloaded_project.script.get_canonical_narration()
    reloaded_hash = hashlib.sha256(reloaded_canonical.encode("utf-8")).hexdigest()

    assert reloaded_canonical == original_canonical
    assert reloaded_hash == original_hash
