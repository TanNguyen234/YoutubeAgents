"""Comprehensive database schema migration tests verifying Schema v8 integrity (Test R)."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from app.db.schema import SCHEMA_VERSION, init_database, migrate_database
from app.domain.enums import AnalyticsSource
from app.domain.models import AnalyticsSnapshot, RetentionPoint


# Exact genuine v7 schema DDL before v8 analytics_snapshots evolution
GENUINE_V7_SCHEMA_SQL = """
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

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    youtube_video_id TEXT,
    snapshot_type TEXT NOT NULL DEFAULT 'REAL',
    is_simulated INTEGER NOT NULL DEFAULT 0,
    views INTEGER NOT NULL DEFAULT 0,
    watch_time_hours REAL NOT NULL DEFAULT 0.0,
    ctr_percent REAL NOT NULL DEFAULT 0.0,
    average_view_duration_seconds REAL NOT NULL DEFAULT 0.0,
    retention_at_3s_percent REAL,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS packaging_tournaments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    selected_candidate_id TEXT,
    selection_reason TEXT,
    native_ab_eligible INTEGER NOT NULL DEFAULT 0,
    scoring_version TEXT NOT NULL DEFAULT 'v1.0',
    status TEXT NOT NULL DEFAULT 'COMPLETED',
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_packaging_tournaments_project ON packaging_tournaments(project_id);
"""


def test_fresh_db_initializes_v8():
    """Verify clean database initializes directly to Schema v8 with new analytics columns and indexes."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "fresh_v8.db"
        init_database(db_path)

        with sqlite3.connect(db_path) as conn:
            # 1. PRAGMA user_version == 8
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 8
            assert ver == SCHEMA_VERSION

            # 2. analytics_snapshots table exists with all v8 columns
            cursor = conn.execute("PRAGMA table_info(analytics_snapshots);")
            cols = {row[1]: row for row in cursor.fetchall()}

            assert "source" in cols
            assert "report_start_date" in cols
            assert "report_end_date" in cols
            assert "average_view_percentage" in cols
            assert "impressions" in cols
            assert "ctr_percent" in cols
            assert "retention_curve_json" in cols

            # ctr_percent must NOT have notnull constraint
            # In sqlite table_info: cid, name, type, notnull, dflt_value, pk
            ctr_col = cols["ctr_percent"]
            assert ctr_col[3] == 0, "ctr_percent must be nullable (notnull == 0)"

            # 3. Unique index idx_analytics_snapshots_window exists
            idx_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_analytics_snapshots_window';"
            ).fetchone()
            assert idx_row is not None


def test_genuine_v7_to_v8_migration_preserves_data():
    """Construct genuine v7 database with SIMULATED and legacy REAL rows, migrate to v8, and assert fidelity."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "migrate_v7_to_v8.db"

        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.executescript(GENUINE_V7_SCHEMA_SQL)
            conn.execute("PRAGMA user_version = 7;")

            # Insert channel and projects
            conn.execute(
                """
                INSERT INTO channels (id, title, handle, niche, target_audience, created_at)
                VALUES ('chan-01', 'Test Channel', '@TestChan', 'Tech', 'Devs', ?);
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO video_projects (id, channel_id, title, format, state, created_at, updated_at)
                VALUES ('proj-01', 'chan-01', 'SQLite Tuning', 'SHORTS_9_16', 'PUBLISHED', ?, ?);
                """,
                (now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO video_projects (id, channel_id, title, format, state, created_at, updated_at)
                VALUES ('proj-02', 'chan-01', 'Docker Containers', 'LONG_FORM_16_9', 'PUBLISHED', ?, ?);
                """,
                (now_iso, now_iso),
            )

            # Insert a representative SIMULATED row in v7
            conn.execute(
                """
                INSERT INTO analytics_snapshots (
                    id, project_id, youtube_video_id, snapshot_type, is_simulated,
                    views, watch_time_hours, ctr_percent, average_view_duration_seconds,
                    retention_at_3s_percent, captured_at
                ) VALUES (
                    'snap-sim-old', 'proj-01', 'sim-video-1', 'SIMULATED', 1,
                    5000, 150.0, 8.5, 45.0, 72.0, ?
                );
                """,
                (now_iso,),
            )

            # Insert a representative old REAL / legacy row in v7
            conn.execute(
                """
                INSERT INTO analytics_snapshots (
                    id, project_id, youtube_video_id, snapshot_type, is_simulated,
                    views, watch_time_hours, ctr_percent, average_view_duration_seconds,
                    retention_at_3s_percent, captured_at
                ) VALUES (
                    'snap-real-old', 'proj-02', 'yt-vid-old-123', 'REAL', 0,
                    12000, 400.0, 6.2, 80.0, 60.0, ?
                );
                """,
                (now_iso,),
            )
            conn.commit()

        # Run migration
        migrate_database(db_path)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 8
            assert ver == SCHEMA_VERSION

            # Verify SIMULATED row was preserved and assigned source = 'SIMULATED'
            r_sim = conn.execute("SELECT * FROM analytics_snapshots WHERE id='snap-sim-old';").fetchone()
            assert r_sim is not None
            assert r_sim["source"] == "SIMULATED"
            assert r_sim["is_simulated"] == 1
            assert r_sim["snapshot_type"] == "SIMULATED"
            assert r_sim["views"] == 5000
            assert r_sim["watch_time_hours"] == 150.0
            assert r_sim["ctr_percent"] == 8.5
            assert r_sim["average_view_duration_seconds"] == 45.0
            assert r_sim["retention_at_3s_percent"] == 72.0

            # Verify legacy REAL row was preserved and assigned source = 'LEGACY_UNVERIFIED'
            r_real = conn.execute("SELECT * FROM analytics_snapshots WHERE id='snap-real-old';").fetchone()
            assert r_real is not None
            assert r_real["source"] == "LEGACY_UNVERIFIED"
            assert r_real["is_simulated"] == 0
            assert r_real["snapshot_type"] == "REAL"
            assert r_real["views"] == 12000
            assert r_real["watch_time_hours"] == 400.0
            assert r_real["ctr_percent"] == 6.2
            assert r_real["average_view_duration_seconds"] == 80.0
            assert r_real["retention_at_3s_percent"] == 60.0
            assert r_real["youtube_video_id"] == "yt-vid-old-123"

            # Verify nullable CTR works by inserting a row with NULL ctr_percent
            conn.execute(
                """
                INSERT INTO analytics_snapshots (
                    id, project_id, youtube_video_id, source, snapshot_type, is_simulated,
                    report_start_date, report_end_date, views, watch_time_hours,
                    average_view_duration_seconds, average_view_percentage, impressions,
                    ctr_percent, retention_at_3s_percent, retention_curve_json, captured_at
                ) VALUES (
                    'snap-null-ctr', 'proj-01', 'yt-null-1', 'YOUTUBE_ANALYTICS_API', 'REAL', 0,
                    '2026-09-01', '2026-09-20', 300, 10.0,
                    60.0, 50.0, NULL,
                    NULL, NULL, NULL, ?
                );
                """,
                (now_iso,),
            )
            r_null = conn.execute("SELECT * FROM analytics_snapshots WHERE id='snap-null-ctr';").fetchone()
            assert r_null["ctr_percent"] is None
            assert r_null["impressions"] is None
            assert r_null["source"] == "YOUTUBE_ANALYTICS_API"


def test_v8_migration_is_idempotent():
    """Verify that calling migrate_database repeatedly on v8 causes no errors or state changes."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "idempotent_v8.db"
        init_database(db_path)

        # Run multiple migration passes
        migrate_database(db_path)
        migrate_database(db_path)

        with sqlite3.connect(db_path) as conn:
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 8
            assert ver == SCHEMA_VERSION

            count = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='analytics_snapshots';"
            ).fetchone()[0]
            assert count == 1
