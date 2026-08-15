"""Unit tests verifying SQLite schema versioning, real Phase-3 v0 legacy detection, and upgrade migrations."""

import gc
import sqlite3
import tempfile
from pathlib import Path
import pytest

from app.db.schema import init_database, migrate_database, SCHEMA_VERSION


@pytest.fixture
def legacy_v0_db_path():
    """Create a database initialized with the REAL Phase 3 legacy schema (user_version == 0, pre-versioning)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "legacy_v0_real.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            # Exact Phase 3 DDL as originally committed (user_version defaults to 0)
            conn.executescript(
                """
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
                    estimated_cpm REAL NOT NULL DEFAULT 10.0,
                    rationale TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (channel_id) REFERENCES channels(id)
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
                    FOREIGN KEY (channel_id) REFERENCES channels(id)
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
                    FOREIGN KEY (project_id) REFERENCES video_projects(id)
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
                    FOREIGN KEY (project_id) REFERENCES video_projects(id)
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
                    FOREIGN KEY (project_id) REFERENCES video_projects(id)
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
                    FOREIGN KEY (project_id) REFERENCES video_projects(id),
                    FOREIGN KEY (channel_id) REFERENCES channels(id)
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
                    FOREIGN KEY (project_id) REFERENCES video_projects(id)
                );

                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    variant_details_json TEXT,
                    status TEXT NOT NULL,
                    result_summary TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES video_projects(id)
                );

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    reason TEXT,
                    transitioned_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES video_projects(id)
                );

                -- Insert representative legacy Phase-3 records
                INSERT INTO channels VALUES ('chan-v0', 'Legacy Channel', '@legacy', 'AI', 'Devs', 'en', 1, '2026-01-01T00:00:00');
                INSERT INTO topic_candidates VALUES ('top-v0', 'chan-v0', 'Legacy Topic', 8.5, 9.0, 10.0, 'Historical reason', '2026-01-01T00:00:00');
                INSERT INTO idempotency_keys VALUES ('legacy-key-v0', 'default', 'COMPLETED', '{"result":"v0_ok"}', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
                """
            )
            conn.commit()
        yield db_path
        gc.collect()


def test_real_legacy_phase3_v0_migration(legacy_v0_db_path: Path) -> None:
    """Verify real Phase 3 legacy DB (user_version == 0 with existing tables) is properly detected and migrated."""
    # 1. Assert pre-migration user_version is 0 (NOT fabricated user_version=1)
    with sqlite3.connect(legacy_v0_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        assert cursor.fetchone()[0] == 0

        # Assert legacy schema has the old schema properties before migration
        cursor.execute("PRAGMA table_info(topic_candidates);")
        v0_topic_cols = {row[1]: row for row in cursor.fetchall()}
        assert v0_topic_cols["estimated_cpm"][3] == 1  # NOT NULL flag was 1
        assert v0_topic_cols["estimated_cpm"][4] == "10.0"  # DEFAULT was 10.0

        cursor.execute("PRAGMA table_info(idempotency_keys);")
        v0_idemp_cols = {row[1]: row for row in cursor.fetchall()}
        assert "expires_at" not in v0_idemp_cols
        assert v0_idemp_cols["key"][5] == 1  # Single PK on key
        assert v0_idemp_cols["scope"][5] == 0  # Scope was not part of PK

    # 2. Run migrate_database()
    migrate_database(legacy_v0_db_path)

    # 3. Assert post-migration version is updated to SCHEMA_VERSION (2)
    with sqlite3.connect(legacy_v0_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        assert cursor.fetchone()[0] == SCHEMA_VERSION

        # 4. Verify topic_candidates schema: estimated_cpm is nullable, no default 10.0
        cursor.execute("PRAGMA table_info(topic_candidates);")
        v2_topic_cols = {row[1]: row for row in cursor.fetchall()}
        assert v2_topic_cols["estimated_cpm"][3] == 0  # NOT NULL flag must be 0 (nullable)
        assert v2_topic_cols["estimated_cpm"][4] is None  # DEFAULT must be None

        # 5. Verify idempotency_keys schema: expires_at added, composite PK (scope, key)
        cursor.execute("PRAGMA table_info(idempotency_keys);")
        v2_idemp_cols = {row[1]: row for row in cursor.fetchall()}
        assert "expires_at" in v2_idemp_cols
        assert v2_idemp_cols["scope"][5] > 0  # Part of composite PK
        assert v2_idemp_cols["key"][5] > 0  # Part of composite PK

        # 6. Verify legacy rows survived migration
        cursor.execute("SELECT id, keyword, estimated_cpm FROM topic_candidates WHERE id = 'top-v0'")
        topic_row = cursor.fetchone()
        assert topic_row is not None
        assert topic_row[0] == "top-v0"
        assert topic_row[1] == "Legacy Topic"
        assert topic_row[2] == 10.0  # Historical stored value preserved

        cursor.execute("SELECT key, scope, status, response, expires_at FROM idempotency_keys WHERE key = 'legacy-key-v0'")
        idemp_row = cursor.fetchone()
        assert idemp_row is not None
        assert idemp_row[0] == "legacy-key-v0"
        assert idemp_row[1] == "default"
        assert idemp_row[2] == "COMPLETED"
        assert idemp_row[4] is None  # Migrated expires_at is None

        # 7. Verify foreign keys check passes cleanly
        cursor.execute("PRAGMA foreign_key_check;")
        fk_violations = cursor.fetchall()
        assert len(fk_violations) == 0


def test_empty_database_migration_direct_to_v2() -> None:
    """Verify completely empty database creates v2 schema directly with user_version = 2."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "empty_new.db"
        migrate_database(db_path)

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA user_version;")
            assert cursor.fetchone()[0] == SCHEMA_VERSION

            cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
            table_count = cursor.fetchone()[0]
            assert table_count >= 10
        gc.collect()


def test_migration_is_idempotent(legacy_v0_db_path: Path) -> None:
    """Verify calling migrate_database multiple times is completely idempotent."""
    migrate_database(legacy_v0_db_path)
    migrate_database(legacy_v0_db_path)

    with sqlite3.connect(legacy_v0_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        assert cursor.fetchone()[0] == SCHEMA_VERSION

        cursor.execute("SELECT count(*) FROM topic_candidates;")
        assert cursor.fetchone()[0] == 1
