"""Comprehensive database schema migration tests verifying Schema v9 integrity."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from app.db.schema import SCHEMA_VERSION, init_database, migrate_database
from app.domain.enums import AnalyticsSource, PackagingTournamentStatus, PublicationStatus
from app.domain.models import AnalyticsSnapshot, PublicationJob, RetentionPoint


# Exact genuine v8 schema DDL before v9 publication packaging & reach tables
GENUINE_V8_SCHEMA_SQL = """
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
    contains_synthetic_media INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    youtube_video_id TEXT,
    source TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    snapshot_type TEXT NOT NULL DEFAULT 'REAL',
    is_simulated INTEGER NOT NULL DEFAULT 0,
    report_start_date TEXT,
    report_end_date TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    watch_time_hours REAL NOT NULL DEFAULT 0.0,
    average_view_duration_seconds REAL NOT NULL DEFAULT 0.0,
    average_view_percentage REAL,
    impressions INTEGER,
    ctr_percent REAL,
    retention_at_3s_percent REAL,
    retention_curve_json TEXT,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_project ON analytics_snapshots(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_snapshots_window ON analytics_snapshots(project_id, source, report_end_date) WHERE report_end_date IS NOT NULL;

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


def test_fresh_db_initializes_v9():
    """Verify clean database initializes directly to Schema v9 with reach tables and publication packaging columns."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "fresh_v9.db"
        init_database(db_path)

        with sqlite3.connect(db_path) as conn:
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 9
            assert ver == SCHEMA_VERSION

            # 1. publication_jobs has new packaging columns
            cursor = conn.execute("PRAGMA table_info(publication_jobs);")
            pub_cols = {row[1]: row for row in cursor.fetchall()}
            assert "packaging_tournament_id" in pub_cols
            assert "packaging_candidate_id" in pub_cols
            assert "deployed_title" in pub_cols
            assert "deployed_thumbnail_sha256" in pub_cols
            assert "packaging_fingerprint" in pub_cols
            assert "packaging_attribution_status" in pub_cols

            # All new columns must be nullable (notnull == 0)
            assert pub_cols["packaging_tournament_id"][3] == 0
            assert pub_cols["packaging_candidate_id"][3] == 0
            assert pub_cols["deployed_title"][3] == 0
            assert pub_cols["deployed_thumbnail_sha256"][3] == 0
            assert pub_cols["packaging_fingerprint"][3] == 0
            assert pub_cols["packaging_attribution_status"][3] == 0

            # 2. reporting_jobs table exists
            r_jobs_cols = {row[1] for row in conn.execute("PRAGMA table_info(reporting_jobs);").fetchall()}
            assert "job_id" in r_jobs_cols
            assert "report_type_id" in r_jobs_cols
            assert "last_processed_create_time" in r_jobs_cols

            # 3. reporting_report_receipts table exists
            r_receipts_cols = {row[1] for row in conn.execute("PRAGMA table_info(reporting_report_receipts);").fetchall()}
            assert "report_id" in r_receipts_cols
            assert "content_sha256" in r_receipts_cols
            assert "create_time" in r_receipts_cols

            # 4. reach_observations table exists with unique index
            r_reach_cols = {row[1] for row in conn.execute("PRAGMA table_info(reach_observations);").fetchall()}
            assert "thumbnail_impressions" in r_reach_cols
            assert "thumbnail_impressions_ctr" in r_reach_cols
            assert "source_report_create_time" in r_reach_cols
            assert "source" in r_reach_cols

            idx_reach = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_reach_observations_video_date';"
            ).fetchone()
            assert idx_reach is not None


def test_genuine_v8_to_v9_migration_preserves_data():
    """Construct genuine v8 database with publication job, snapshot, and tournament; migrate to v9."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "migrate_v8_to_v9.db"

        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.executescript(GENUINE_V8_SCHEMA_SQL)
            conn.execute("PRAGMA user_version = 8;")

            # Insert channel and project
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

            # Insert v8 publication job (without v9 columns)
            conn.execute(
                """
                INSERT INTO publication_jobs (
                    id, project_id, channel_id, status, privacy_status,
                    scheduled_publish_time, youtube_video_id, published_at,
                    contains_synthetic_media, error_message, created_at
                ) VALUES (
                    'pub-01', 'proj-01', 'chan-01', 'COMPLETED', 'private',
                    NULL, 'yt-vid-v8', ?,
                    0, NULL, ?
                );
                """,
                (now_iso, now_iso),
            )

            # Insert v8 analytics snapshot
            conn.execute(
                """
                INSERT INTO analytics_snapshots (
                    id, project_id, youtube_video_id, source, snapshot_type, is_simulated,
                    report_start_date, report_end_date, views, watch_time_hours,
                    average_view_duration_seconds, average_view_percentage, impressions,
                    ctr_percent, retention_at_3s_percent, retention_curve_json, captured_at
                ) VALUES (
                    'snap-v8', 'proj-01', 'yt-vid-v8', 'YOUTUBE_ANALYTICS_API', 'REAL', 0,
                    '2026-09-01', '2026-09-20', 1000, 25.0,
                    90.0, 55.0, NULL,
                    NULL, 70.0, NULL, ?
                );
                """,
                (now_iso,),
            )

            # Insert v8 packaging tournament
            candidates = [
                {
                    "id": "cand-1",
                    "title": "Title 1",
                    "title_strategy": "DIRECT_VALUE",
                    "thumbnail_headline": "HEADLINE",
                    "thumbnail_visual_strategy": "DIAGRAM",
                    "truth_status": "SUPPORTED",
                    "passed_gates": True,
                    "quality_score": 0.8,
                    "score_breakdown": {},
                    "rationale": "Rationale 1",
                },
                {
                    "id": "cand-2",
                    "title": "Title 2",
                    "title_strategy": "CURIOSITY_QUESTION",
                    "thumbnail_headline": "CURIOSITY",
                    "thumbnail_visual_strategy": "SCREENSHOT",
                    "truth_status": "SUPPORTED",
                    "passed_gates": True,
                    "quality_score": 0.9,
                    "score_breakdown": {},
                    "rationale": "Rationale 2",
                },
                {
                    "id": "cand-3",
                    "title": "Title 3",
                    "title_strategy": "CONTRAST_MECHANISM",
                    "thumbnail_headline": "CONTRAST",
                    "thumbnail_visual_strategy": "CODE",
                    "truth_status": "SUPPORTED",
                    "passed_gates": True,
                    "quality_score": 0.7,
                    "score_breakdown": {},
                    "rationale": "Rationale 3",
                },
            ]
            conn.execute(
                """
                INSERT INTO packaging_tournaments (
                    id, project_id, created_at, candidates_json, selected_candidate_id,
                    selection_reason, native_ab_eligible, scoring_version, status
                ) VALUES (
                    'trn-v8', 'proj-01', ?, ?, 'cand-2',
                    'Higher offline score', 0, 'v1.0', 'COMPLETED'
                );
                """,
                (now_iso, json.dumps(candidates)),
            )
            conn.commit()

        # Execute migration
        migrate_database(db_path)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 9
            assert ver == SCHEMA_VERSION

            # Verify existing publication job preserved and new packaging fields are NULL (Section 75-76)
            row_pub = conn.execute("SELECT * FROM publication_jobs WHERE id='pub-01';").fetchone()
            assert row_pub is not None
            assert row_pub["youtube_video_id"] == "yt-vid-v8"
            assert row_pub["packaging_tournament_id"] is None
            assert row_pub["packaging_candidate_id"] is None
            assert row_pub["deployed_title"] is None
            assert row_pub["deployed_thumbnail_sha256"] is None
            assert row_pub["packaging_fingerprint"] is None
            assert row_pub["packaging_attribution_status"] is None

            # Verify analytics snapshot preserved
            row_snap = conn.execute("SELECT * FROM analytics_snapshots WHERE id='snap-v8';").fetchone()
            assert row_snap is not None
            assert row_snap["views"] == 1000
            assert row_snap["source"] == "YOUTUBE_ANALYTICS_API"

            # Verify tournament preserved
            row_trn = conn.execute("SELECT * FROM packaging_tournaments WHERE id='trn-v8';").fetchone()
            assert row_trn is not None
            assert row_trn["selected_candidate_id"] == "cand-2"

            # Verify new tables exist and can accept valid rows
            conn.execute(
                """
                INSERT INTO reporting_jobs (
                    job_id, report_type_id, remote_name, channel_id,
                    last_processed_create_time, created_at, updated_at
                ) VALUES (
                    'job-01', 'channel_reach_basic_a1', 'test-reach-job', 'chan-01',
                    NULL, ?, ?
                );
                """,
                (now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO reporting_report_receipts (
                    report_id, job_id, report_type_id, start_time, end_time,
                    create_time, content_sha256, processed_at
                ) VALUES (
                    'rep-01', 'job-01', 'channel_reach_basic_a1', ?, ?,
                    ?, 'sha256-test', ?
                );
                """,
                (now_iso, now_iso, now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO reach_observations (
                    id, project_id, publication_job_id, youtube_video_id, report_date,
                    thumbnail_impressions, thumbnail_impressions_ctr, source_report_id,
                    source_report_create_time, source, created_at
                ) VALUES (
                    'reach-01', 'proj-01', 'pub-01', 'yt-vid-v8', '2026-09-20',
                    1500, 4.5, 'rep-01',
                    ?, 'YOUTUBE_REPORTING_API', ?
                );
                """,
                (now_iso, now_iso),
            )
            conn.commit()

            reach_row = conn.execute("SELECT * FROM reach_observations WHERE id='reach-01';").fetchone()
            assert reach_row["thumbnail_impressions"] == 1500
            assert reach_row["thumbnail_impressions_ctr"] == 4.5
            assert reach_row["source"] == "YOUTUBE_REPORTING_API"


def test_v9_migration_is_idempotent():
    """Verify that calling migrate_database repeatedly on v9 causes no errors or state corruption."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "idempotent_v9.db"
        init_database(db_path)

        # Run multiple migration passes
        migrate_database(db_path)
        migrate_database(db_path)

        with sqlite3.connect(db_path) as conn:
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 9
            assert ver == SCHEMA_VERSION

            count = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='reach_observations';"
            ).fetchone()[0]
            assert count == 1
