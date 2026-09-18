"""Tests for Schema v4 migration:
- Real v3 database (user_version=3, publication_jobs without contains_synthetic_media) migrates to v4.
- Old publication rows survive v4 migration with default 0.
- Saving new PublicationJob works after v3 to v4 upgrade.
- Migration is idempotent.
- PRAGMA user_version is 4 after migration.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import SCHEMA_VERSION, init_database, migrate_database
from app.domain.enums import PrivacyStatus, PublicationStatus
from app.domain.models import PublicationJob


# Pure schema v3 DDL where publication_jobs did NOT have contains_synthetic_media
V3_WITHOUT_SYNTHETIC_MEDIA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    handle TEXT NOT NULL,
    niche TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    default_language TEXT NOT NULL DEFAULT 'en',
    youtube_category_id TEXT NOT NULL DEFAULT '28',
    made_for_kids INTEGER NOT NULL DEFAULT 0,
    default_tags_json TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_projects (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    format TEXT NOT NULL,
    state TEXT NOT NULL,
    metadata_tags TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS publication_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    status TEXT NOT NULL,
    privacy_status TEXT NOT NULL DEFAULT 'private',
    scheduled_publish_time TEXT,
    youtube_video_id TEXT,
    published_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE RESTRICT
);
"""


def _setup_v3_db(db_path: Path):
    """Set up an existing real v3 database with user_version=3 and publication_jobs missing contains_synthetic_media."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(V3_WITHOUT_SYNTHETIC_MEDIA_DDL)
    conn.execute("PRAGMA user_version = 3;")

    conn.execute(
        "INSERT INTO channels VALUES ('chan_1', 'AI Daily', '@aidaily', 'AI', 'Developers', 'en', '28', 0, '[]', 1, '2026-09-01T00:00:00Z');"
    )
    conn.execute(
        "INSERT INTO video_projects VALUES ('proj_1', 'chan_1', 'Video 1', 'EXPLAINER', 'REVIEW_APPROVED', '[]', '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z');"
    )
    conn.execute(
        """
        INSERT INTO publication_jobs (id, project_id, channel_id, status, privacy_status, scheduled_publish_time, youtube_video_id, published_at, error_message, created_at)
        VALUES ('pub_old', 'proj_1', 'chan_1', 'COMPLETED', 'private', NULL, 'yt_abc123', '2026-09-01T12:00:00Z', NULL, '2026-09-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()


def test_v3_database_migrates_publication_job_synthetic_media_column(tmp_path: Path):
    """An existing v3 DB must gain contains_synthetic_media column in publication_jobs and user_version 4."""
    db_path = tmp_path / "test_v3_migrate.db"
    _setup_v3_db(db_path)

    # Before migration: verify column is absent
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(publication_jobs);").fetchall()}
    assert "contains_synthetic_media" not in cols
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 3
    conn.close()

    # Migrate
    migrate_database(db_path)

    # After migration
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == SCHEMA_VERSION
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(publication_jobs);").fetchall()}
    assert "contains_synthetic_media" in cols_after
    conn.close()


def test_old_publication_rows_survive_v4_migration(tmp_path: Path):
    """Existing publication_job records must survive v4 migration with contains_synthetic_media defaulting to 0."""
    db_path = tmp_path / "test_v3_rows_survive.db"
    _setup_v3_db(db_path)

    migrate_database(db_path)

    repo = SQLiteRepository(db_path)
    jobs = repo.get_publication_queue(PublicationStatus.COMPLETED)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "pub_old"
    assert job.youtube_video_id == "yt_abc123"
    assert job.contains_synthetic_media is False


def test_save_publication_job_works_after_v3_to_v4_upgrade(tmp_path: Path):
    """New publication jobs with contains_synthetic_media can be saved and retrieved via repository."""
    db_path = tmp_path / "test_v3_save_job.db"
    _setup_v3_db(db_path)

    # Upgrade via repository initialization
    repo = SQLiteRepository(db_path)

    new_job = PublicationJob(
        id="pub_new",
        project_id="proj_1",
        channel_id="chan_1",
        status=PublicationStatus.PENDING,
        privacy_status=PrivacyStatus.PRIVATE,
        scheduled_publish_time=None,
        youtube_video_id=None,
        published_at=None,
        contains_synthetic_media=True,
        error_message=None,
        created_at=datetime.now(timezone.utc),
    )

    # This previously failed with sqlite3.OperationalError: table publication_jobs has no column named contains_synthetic_media
    repo.save_publication_job(new_job)

    queue = repo.get_publication_queue(PublicationStatus.PENDING)
    saved = next(j for j in queue if j.id == "pub_new")
    assert saved.contains_synthetic_media is True


def test_migration_is_idempotent(tmp_path: Path):
    """Re-running migrate_database on a v4 DB must be a safe no-op and preserve user_version=4."""
    db_path = tmp_path / "test_v4_idempotent.db"
    _setup_v3_db(db_path)

    migrate_database(db_path)
    migrate_database(db_path)
    migrate_database(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == SCHEMA_VERSION
    cols = {r[1] for r in conn.execute("PRAGMA table_info(publication_jobs);").fetchall()}
    assert "contains_synthetic_media" in cols
    conn.close()
