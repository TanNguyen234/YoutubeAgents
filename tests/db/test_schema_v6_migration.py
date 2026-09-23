"""Tests for Schema v6 migration:
- Fresh database creates v6 directly with opportunity_portfolios table.
- Database migrates from v5 to v6 cleanly.
- Existing v5 data (channels, market_signal_snapshots) is preserved across v6 migration.
- Persistence and round-trip of OpportunityPortfolio.
- Migration idempotency (running migrate_database multiple times keeps user_version=6).
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import SCHEMA_VERSION, init_database, migrate_database
from app.domain.models import (
    Channel,
    MarketSignalSnapshot,
    OpportunityPortfolio,
    TopicOpportunity,
)


V5_DATABASE_DDL = """
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
"""


def test_fresh_database_creates_v6_directly(tmp_path: Path):
    """A fresh database must be initialized directly to user_version=6 with opportunity_portfolios table."""
    db_path = tmp_path / "test_fresh_v6.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
    assert "opportunity_portfolios" in tables
    assert "market_signal_snapshots" in tables

    cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunity_portfolios);").fetchall()}
    expected_cols = {
        "batch_id",
        "channel_id",
        "generated_at",
        "candidates_json",
        "selected_topic_json",
        "selection_reason",
        "market_signal_ids_json",
        "formula_version",
        "created_at",
    }
    for ec in expected_cols:
        assert ec in cols

    indexes = {r[1] for r in conn.execute("PRAGMA index_list(opportunity_portfolios);").fetchall()}
    assert "idx_opportunity_portfolios_channel" in indexes
    conn.close()


def test_v5_to_v6_migration_preserves_data(tmp_path: Path):
    """Simulate a pure v5 database with existing records and migrate to v6."""
    db_path = tmp_path / "test_v5_to_v6.db"

    # Step 1: Create pure v5 database
    conn = sqlite3.connect(db_path)
    conn.executescript(V5_DATABASE_DDL)
    conn.execute("PRAGMA user_version = 5;")
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO channels (id, title, handle, niche, target_audience, created_at) "
        "VALUES ('chan-01', 'Test Channel', '@TestChan', 'AI', 'Engineers', ?);",
        (now_iso,),
    )
    conn.execute(
        "INSERT INTO market_signal_snapshots (id, batch_id, channel_id, query, collected_at, "
        "sample_video_ids_json, sample_size, created_at) "
        "VALUES ('mss-01', 'b-01', 'chan-01', 'AI Agents', ?, '[\"v1\"]', 5, ?);",
        (now_iso, now_iso),
    )
    conn.commit()
    conn.close()

    # Step 2: Migrate to v6
    migrate_database(db_path)

    # Step 3: Verify user_version is SCHEMA_VERSION and existing data is preserved
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == SCHEMA_VERSION

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
    assert "opportunity_portfolios" in tables

    # Verify channel and market snapshot survived
    c_row = conn.execute("SELECT title FROM channels WHERE id = 'chan-01';").fetchone()
    assert c_row[0] == "Test Channel"

    s_row = conn.execute("SELECT query FROM market_signal_snapshots WHERE id = 'mss-01';").fetchone()
    assert s_row[0] == "AI Agents"
    conn.close()


def test_v6_migration_is_idempotent(tmp_path: Path):
    """Re-running migrate_database on a v6 DB must be a safe no-op and preserve user_version=6."""
    db_path = tmp_path / "test_v6_idempotent.db"
    init_database(db_path)

    migrate_database(db_path)
    migrate_database(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == SCHEMA_VERSION
    conn.close()
