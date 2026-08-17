"""Tests for Schema v3 migrations:
- Pure Phase 3.7 v2 database to v3.
- Pseudo v2 (Phase 4.1 interim) to v3.
- Empty database directly to v3.
- Migration idempotency.
"""

from pathlib import Path
import sqlite3
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import SCHEMA_VERSION, init_database, migrate_database
from app.domain.models import VideoLifecycleState


# Exact DDL of pure Phase 3.7 v2 database from commit 5d91a3e
PHASE_37_V2_DDL = """
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    handle TEXT NOT NULL,
    niche TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    default_language TEXT NOT NULL DEFAULT 'en',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_candidates (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    opportunity_score REAL NOT NULL,
    authority_score REAL NOT NULL,
    estimated_cpm REAL,
    rationale TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    hook TEXT NOT NULL,
    scenes_json TEXT NOT NULL,
    total_word_count INTEGER NOT NULL,
    estimated_duration_seconds REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    source_url TEXT NOT NULL,
    license_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quality_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    loudness_lufs REAL NOT NULL,
    duration_seconds REAL NOT NULL,
    sync_drift_ms REAL NOT NULL,
    issues_json TEXT,
    checked_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
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

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    youtube_video_id TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    watch_time_hours REAL NOT NULL DEFAULT 0.0,
    ctr_percent REAL NOT NULL DEFAULT 0.0,
    average_view_duration_seconds REAL NOT NULL DEFAULT 0.0,
    retention_at_3s_percent REAL,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    variant_details_json TEXT,
    status TEXT NOT NULL,
    result_summary TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT NOT NULL,
    response TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT,
    transitioned_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);
"""


def test_phase37_v2_to_v3(tmp_path: Path):
    """A pure Phase 3.7 v2 database (user_version=2, no score_breakdown_json, no sections_json) must migrate to v3."""
    db_path = tmp_path / "phase37_v2.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(PHASE_37_V2_DDL)
    conn.execute("PRAGMA user_version = 2;")

    # Insert sample legacy data adhering to exact 5d91a3e v2 schema
    conn.execute(
        "INSERT INTO channels VALUES ('c1', 'Tech Hub', '@tech', 'AI', 'Devs', 'en', 1, '2026-08-15T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO video_projects VALUES ('p1', 'c1', 'Async Python', 'SHORT', 'CREATED', '[]', '2026-08-15T00:00:00Z', '2026-08-15T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO topic_candidates VALUES ('t1', 'c1', 'Async Python', 8.5, 9.0, 15.0, 'Good topic', '2026-08-15T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO scripts VALUES ('s1', 'p1', 'Async Python', 'Hook line', '[]', 100, 30.0, '2026-08-15T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    # Run migration
    migrate_database(db_path)

    # Verify user_version is 3
    conn = sqlite3.connect(db_path)
    v = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert v == 3

    # Verify new columns exist in existing tables
    t_cols = [r[1] for r in conn.execute("PRAGMA table_info(topic_candidates);").fetchall()]
    assert "score_breakdown_json" in t_cols

    s_cols = [r[1] for r in conn.execute("PRAGMA table_info(scripts);").fetchall()]
    assert "sections_json" in s_cols

    # Verify Phase 4 tables exist
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    assert "research_dossiers" in tables
    assert "research_sources" in tables
    assert "claims" in tables
    assert "fact_check_reports" in tables

    # Verify legacy data preserved
    ch = conn.execute("SELECT title FROM channels WHERE id='c1'").fetchone()
    assert ch[0] == "Tech Hub"
    conn.close()


def test_phase41_pseudo_v2_to_v3(tmp_path: Path):
    """A Phase 4.1 pseudo-v2 database that already has sections_json must migrate cleanly without column duplication errors."""
    db_path = tmp_path / "phase41_pseudo_v2.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(PHASE_37_V2_DDL)
    conn.execute("ALTER TABLE scripts ADD COLUMN sections_json TEXT;")
    conn.execute("PRAGMA user_version = 2;")
    conn.commit()
    conn.close()

    # Run migration
    migrate_database(db_path)

    conn = sqlite3.connect(db_path)
    v = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert v == 3

    t_cols = [r[1] for r in conn.execute("PRAGMA table_info(topic_candidates);").fetchall()]
    assert "score_breakdown_json" in t_cols
    conn.close()


def test_empty_database_migration_direct_to_v3(tmp_path: Path):
    """An empty database initialized directly must have schema version 3 and all intelligence tables."""
    db_path = tmp_path / "empty_v3.db"
    repo = SQLiteRepository(db_path)

    conn = sqlite3.connect(db_path)
    v = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert v == 3

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    assert "research_dossiers" in tables
    assert "research_sources" in tables
    assert "claims" in tables
    assert "fact_check_reports" in tables
    conn.close()


def test_migration_v3_is_idempotent(tmp_path: Path):
    """Running migrate_database multiple times on a v3 DB must be a safe no-op."""
    db_path = tmp_path / "idempotent_v3.db"
    init_database(db_path)

    # Re-run migration twice
    migrate_database(db_path)
    migrate_database(db_path)

    conn = sqlite3.connect(db_path)
    v = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert v == 3
    conn.close()
