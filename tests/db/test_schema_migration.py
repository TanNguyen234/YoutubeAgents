"""Unit tests verifying SQLite schema versioning and upgrade migrations from legacy schemas."""

import gc
import sqlite3
import tempfile
from pathlib import Path
import pytest

from app.db.schema import init_database, migrate_database, SCHEMA_VERSION


@pytest.fixture
def legacy_v1_db_path():
    """Create a database initialized with the legacy Phase 3 (v1) schema."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "legacy_v1.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.executescript(
                """
                PRAGMA user_version = 1;

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

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                INSERT INTO channels VALUES ('chan-1', 'Legacy Channel', '@legacy', 'Tech', 'Devs', 'en', 1, '2026-01-01T00:00:00');
                INSERT INTO topic_candidates VALUES ('top-1', 'chan-1', 'Legacy AI', 8.0, 7.5, 10.0, 'Legacy rationale', '2026-01-01T00:00:00');
                INSERT INTO idempotency_keys VALUES ('legacy-key-1', 'default', 'COMPLETED', '{"res":"ok"}', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
                """
            )
            conn.commit()
        yield db_path
        gc.collect()


def test_schema_migration_from_v1_to_current(legacy_v1_db_path: Path) -> None:
    """Verify legacy database at user_version 1 cleanly upgrades to current schema version without data loss."""
    # Check pre-migration version
    with sqlite3.connect(legacy_v1_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        initial_version = cursor.fetchone()[0]
    assert initial_version == 1

    # Run migration / initialization
    migrate_database(legacy_v1_db_path)

    # Check post-migration version
    with sqlite3.connect(legacy_v1_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        final_version = cursor.fetchone()[0]
        assert final_version == SCHEMA_VERSION

        # Verify idempotency table has composite primary key and expires_at column
        cursor.execute("PRAGMA table_info(idempotency_keys);")
        columns = {row[1]: row for row in cursor.fetchall()}
        assert "expires_at" in columns
        assert columns["key"][5] == 2  # PK column index 2
        assert columns["scope"][5] == 1  # PK column index 1

        # Verify legacy data preserved
        cursor.execute("SELECT key, scope, status, response FROM idempotency_keys WHERE key = 'legacy-key-1'")
        idemp_row = cursor.fetchone()
        assert idemp_row is not None
        assert idemp_row[2] == "COMPLETED"

        cursor.execute("SELECT id, keyword, estimated_cpm FROM topic_candidates WHERE id = 'top-1'")
        topic_row = cursor.fetchone()
        assert topic_row is not None
        assert topic_row[1] == "Legacy AI"
