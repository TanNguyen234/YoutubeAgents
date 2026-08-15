"""SQLite schema definition, DDL statements, migrations, and initialization."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA_V2_SQL = """
PRAGMA foreign_keys = ON;

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


def migrate_database(db_path: Path) -> None:
    """Migrate SQLite database to the current schema version using user_version pragma."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        current_version = cursor.fetchone()[0]

        if current_version == 0:
            # Brand new database: apply full v2 schema
            conn.executescript(SCHEMA_V2_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            conn.commit()
            return

        if current_version < 2:
            # Upgrade from v1 to v2:
            # 1. Upgrade idempotency_keys table to composite (scope, key) PK and add expires_at
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS idempotency_keys_v2 (
                    key TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scope, key)
                );

                INSERT INTO idempotency_keys_v2 (key, scope, status, response, expires_at, created_at, updated_at)
                SELECT key, scope, status, response, NULL, created_at, updated_at
                FROM idempotency_keys;

                DROP TABLE idempotency_keys;
                ALTER TABLE idempotency_keys_v2 RENAME TO idempotency_keys;
                """
            )
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            conn.commit()


def init_database(db_path: Path) -> None:
    """Initialize or migrate database to the current schema version."""
    migrate_database(db_path)
