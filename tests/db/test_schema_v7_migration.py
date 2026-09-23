"""Comprehensive database schema migration tests verifying Schema v7 integrity."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import pytest

from app.db.schema import SCHEMA_VERSION, init_database, migrate_database

# Exact pre-Packaging v6 schema SQL without packaging_tournaments table (from commit a6a659a)
GENUINE_V6_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    handle TEXT NOT NULL,
    niche TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    persona_description TEXT,
    tone_guidelines_json TEXT,
    youtube_category_id TEXT NOT NULL DEFAULT '28',
    made_for_kids INTEGER NOT NULL DEFAULT 0,
    default_tags_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_projects (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT,
    format TEXT NOT NULL,
    state TEXT NOT NULL,
    metadata_tags TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    hook TEXT NOT NULL,
    scenes_json TEXT NOT NULL,
    sections_json TEXT,
    content_format TEXT NOT NULL DEFAULT 'EXPLAINER',
    total_word_count INTEGER NOT NULL,
    estimated_duration_seconds REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS topic_candidates (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_query TEXT NOT NULL,
    demand_score REAL NOT NULL,
    competition_score REAL NOT NULL,
    relevance_score REAL NOT NULL,
    freshness_score REAL NOT NULL,
    composite_score REAL NOT NULL,
    angle TEXT,
    selected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    score_breakdown_json TEXT,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT,
    statement TEXT NOT NULL,
    verified INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    cited_url TEXT,
    cited_excerpt TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fact_check_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    verified_count INTEGER NOT NULL,
    failed_count INTEGER NOT NULL,
    overall_verdict TEXT NOT NULL,
    audit_summary TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS seo_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    primary_keyword TEXT NOT NULL,
    title_variants_json TEXT NOT NULL,
    selected_title TEXT NOT NULL,
    description TEXT NOT NULL,
    pinned_comment TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS thumbnail_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    file_path_16_9 TEXT NOT NULL,
    file_path_9_16 TEXT NOT NULL,
    headline_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS publication_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    status TEXT NOT NULL,
    privacy_status TEXT NOT NULL,
    scheduled_publish_time TEXT,
    youtube_video_id TEXT,
    published_at TEXT,
    contains_synthetic_media INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    views INTEGER NOT NULL,
    watch_time_hours REAL NOT NULL,
    average_view_duration_seconds REAL NOT NULL,
    ctr REAL NOT NULL,
    retention_rate_30s REAL NOT NULL,
    impressions INTEGER NOT NULL,
    subscribers_gained INTEGER NOT NULL,
    retention_curve_json TEXT,
    retention_dropoff_points_json TEXT,
    snapshot_type TEXT NOT NULL DEFAULT 'REAL',
    is_simulated INTEGER NOT NULL DEFAULT 0,
    collected_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL,
    feedback TEXT,
    reviewed_at TEXT NOT NULL,
    approval_origin TEXT NOT NULL DEFAULT 'AUTOMATION',
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

CREATE TABLE IF NOT EXISTS market_signal_snapshots (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    query TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'YOUTUBE_DATA_API_V3',
    collected_at TEXT NOT NULL,
    sample_video_ids_json TEXT NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    recent_video_count_7d INTEGER NOT NULL DEFAULT 0,
    recent_video_count_30d INTEGER NOT NULL DEFAULT 0,
    recent_share_30d REAL NOT NULL DEFAULT 0.0,
    median_views REAL NOT NULL DEFAULT 0.0,
    p75_views REAL NOT NULL DEFAULT 0.0,
    median_age_days REAL NOT NULL DEFAULT 0.0,
    median_views_per_day REAL NOT NULL DEFAULT 0.0,
    p75_views_per_day REAL NOT NULL DEFAULT 0.0,
    unique_creator_count INTEGER NOT NULL DEFAULT 0,
    top_creator_share REAL NOT NULL DEFAULT 0.0,
    estimated_result_count INTEGER,
    formula_version TEXT NOT NULL DEFAULT 'v1.0',
    confidence TEXT NOT NULL DEFAULT 'HIGH',
    raw_metrics_json TEXT,
    derived_scores_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_market_signals_batch ON market_signal_snapshots(batch_id);
CREATE INDEX IF NOT EXISTS idx_market_signals_query ON market_signal_snapshots(channel_id, query);

CREATE TABLE IF NOT EXISTS opportunity_portfolios (
    batch_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    selected_topic_json TEXT,
    selection_reason TEXT,
    market_signal_ids_json TEXT NOT NULL,
    formula_version TEXT NOT NULL DEFAULT 'v1.0',
    created_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_opportunity_portfolios_channel ON opportunity_portfolios(channel_id);
"""


def test_fresh_db_initializes_v7():
    """Verify clean database initializes directly to Schema v7 with packaging_tournaments."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "fresh_v7.db"
        init_database(db_path)

        with sqlite3.connect(db_path) as conn:
            # 1. PRAGMA user_version == 7
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 7
            assert ver == SCHEMA_VERSION

            # 2. packaging_tournaments table exists
            t_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='packaging_tournaments';"
            ).fetchone()
            assert t_row is not None
            assert t_row[0] == "packaging_tournaments"

            # 3. idx_packaging_tournaments_project index exists
            i_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_packaging_tournaments_project';"
            ).fetchone()
            assert i_row is not None
            assert i_row[0] == "idx_packaging_tournaments_project"


def test_genuine_v6_to_v7_migration_preserves_data():
    """Construct genuine v6 database, verify absence of v7 tables, migrate, and verify data preservation."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "migrate_v6_to_v7.db"

        # 1. Create true v6 database
        with sqlite3.connect(db_path) as conn:
            conn.executescript(GENUINE_V6_SCHEMA_SQL)
            conn.execute("PRAGMA user_version = 6;")

            # Assert v7 table does NOT exist
            assert conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='packaging_tournaments';"
            ).fetchone() is None

            # Insert sample representative v6 data
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO channels (id, title, handle, niche, target_audience, youtube_category_id, made_for_kids, created_at)
                VALUES ('chan-v6-01', 'Database Engineering', '@DBEng', 'Storage Engines', 'Engineers', '28', 0, ?);
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO video_projects (id, channel_id, title, format, state, metadata_tags, created_at, updated_at)
                VALUES ('proj-v6-01', 'chan-v6-01', 'WAL Storage Engine', 'LONG_FORM_16_9', 'CREATED', '[]', ?, ?);
                """,
                (now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO scripts (id, project_id, title, hook, scenes_json, total_word_count, estimated_duration_seconds, created_at)
                VALUES ('scr-v6-01', 'proj-v6-01', 'WAL Storage Engine', 'Hook', '[]', 100, 60.0, ?);
                """,
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO opportunity_portfolios (batch_id, channel_id, generated_at, candidates_json, selected_topic_json, selection_reason, market_signal_ids_json, formula_version, created_at)
                VALUES ('port-v6-01', 'chan-v6-01', ?, '[]', NULL, 'Test selection', '[]', 'v1.0', ?);
                """,
                (now_iso, now_iso),
            )
            conn.commit()

        # 2. Run migration
        migrate_database(db_path)

        # 3. Assert upgraded state and data integrity
        with sqlite3.connect(db_path) as conn:
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 7

            # packaging_tournaments table now exists
            t_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='packaging_tournaments';"
            ).fetchone()
            assert t_row is not None

            # Index exists
            idx_row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_packaging_tournaments_project';"
            ).fetchone()
            assert idx_row is not None

            # Prior rows completely intact
            chan = conn.execute("SELECT title, handle FROM channels WHERE id='chan-v6-01';").fetchone()
            assert chan == ("Database Engineering", "@DBEng")

            proj = conn.execute("SELECT title, format, state FROM video_projects WHERE id='proj-v6-01';").fetchone()
            assert proj == ("WAL Storage Engine", "LONG_FORM_16_9", "CREATED")

            scr = conn.execute("SELECT title, total_word_count FROM scripts WHERE id='scr-v6-01';").fetchone()
            assert scr == ("WAL Storage Engine", 100)

            port = conn.execute("SELECT selection_reason FROM opportunity_portfolios WHERE batch_id='port-v6-01';").fetchone()
            assert port == ("Test selection",)

            # Foreign key works on packaging_tournaments
            conn.execute(
                """
                INSERT INTO packaging_tournaments (id, project_id, created_at, candidates_json, selected_candidate_id, selection_reason, native_ab_eligible, scoring_version, status)
                VALUES ('trn-01', 'proj-v6-01', ?, '[]', 'cand-1', 'Highest score', 1, 'v1.0', 'COMPLETED');
                """,
                (now_iso,),
            )
            t_res = conn.execute("SELECT selected_candidate_id FROM packaging_tournaments WHERE id='trn-01';").fetchone()
            assert t_res == ("cand-1",)


def test_migration_v7_idempotent():
    """Verify that calling migrate_database repeatedly on v7 causes no errors or state changes."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        db_path = Path(tmp_dir) / "idempotent_v7.db"
        init_database(db_path)

        # Run multiple migration passes
        migrate_database(db_path)
        migrate_database(db_path)

        with sqlite3.connect(db_path) as conn:
            ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            assert ver == 7

            count = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='packaging_tournaments';"
            ).fetchone()[0]
            assert count == 1
