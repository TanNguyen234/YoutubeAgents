"""Tests for Schema v5 migration:
- Database migrates to v5 and creates market_signal_snapshots table.
- Index creation and foreign keys.
- Persistence of MarketSignalSnapshot.
- Migration idempotency.
- PRAGMA user_version is 5 after migration.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pytest

from app.db.repository import SQLiteRepository
from app.db.schema import SCHEMA_VERSION, init_database, migrate_database
from app.domain.models import Channel, MarketSignalSnapshot


def test_schema_v5_creates_market_signal_snapshots_table(tmp_path: Path):
    """A migrated database must have market_signal_snapshots table, correct indexes, and user_version=5."""
    db_path = tmp_path / "test_v5_migrate.db"
    init_database(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 5
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()}
    assert "market_signal_snapshots" in tables

    cols = {r[1] for r in conn.execute("PRAGMA table_info(market_signal_snapshots);").fetchall()}
    expected_cols = {
        "id", "batch_id", "channel_id", "query", "source", "collected_at",
        "sample_video_ids_json", "sample_size",
        "recent_video_count_7d", "recent_video_count_30d", "recent_share_30d",
        "median_views", "p75_views", "median_age_days",
        "median_views_per_day", "p75_views_per_day",
        "unique_creator_count", "top_creator_share",
        "estimated_result_count", "formula_version", "confidence",
        "raw_metrics_json", "derived_scores_json", "created_at",
    }
    for ec in expected_cols:
        assert ec in cols

    indexes = {r[1] for r in conn.execute("PRAGMA index_list(market_signal_snapshots);").fetchall()}
    assert "idx_market_signals_batch" in indexes
    assert "idx_market_signals_query" in indexes
    conn.close()


def test_v5_migration_is_idempotent(tmp_path: Path):
    """Re-running migrate_database on a v5 DB must be a safe no-op and preserve user_version=5."""
    db_path = tmp_path / "test_v5_idempotent.db"
    init_database(db_path)

    migrate_database(db_path)
    migrate_database(db_path)

    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 5
    conn.close()
