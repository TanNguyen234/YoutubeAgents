"""SQLite schema definition, DDL statements, migrations, and initialization."""

from pathlib import Path
import sqlite3

SCHEMA_VERSION = 8

SCHEMA_V8_SQL = """

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

CREATE TABLE IF NOT EXISTS topic_candidates (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    keyword TEXT NOT NULL,
    opportunity_score REAL NOT NULL,
    authority_score REAL NOT NULL,
    estimated_cpm REAL,
    rationale TEXT,
    score_breakdown_json TEXT,
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
    sections_json TEXT,
    content_format TEXT NOT NULL DEFAULT 'EXPLAINER',
    total_word_count INTEGER NOT NULL,
    estimated_duration_seconds REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_dossiers (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_sources (
    id TEXT PRIMARY KEY,
    dossier_id TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT,
    http_status INTEGER,
    title TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_snapshot TEXT,
    content_snapshot_path TEXT,
    license_type TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    FOREIGN KEY (dossier_id) REFERENCES research_dossiers(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT,
    statement TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    verdict TEXT NOT NULL,
    confidence_score REAL,
    cited_url TEXT,
    cited_excerpt TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fact_check_reports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    verified_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS review_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    operator TEXT NOT NULL,
    action TEXT NOT NULL,
    notes TEXT,
    approved_privacy_status TEXT NOT NULL DEFAULT 'private',
    approval_origin TEXT NOT NULL DEFAULT 'AUTOMATION',
    media_overrides_json TEXT,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS content_series (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    target_niche TEXT NOT NULL,
    default_format TEXT NOT NULL DEFAULT 'shorts_9_16',
    frequency_per_week INTEGER NOT NULL DEFAULT 3,
    playlist_id TEXT,
    visual_style_preset TEXT NOT NULL DEFAULT 'modern_tech',
    next_episode_number INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS editorial_calendar (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    series_id TEXT,
    project_id TEXT,
    slot_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PLANNED',
    target_topic TEXT NOT NULL,
    episode_number INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
    FOREIGN KEY (series_id) REFERENCES content_series(id) ON DELETE SET NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS seo_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,
    primary_keyword TEXT NOT NULL,
    title_variants_json TEXT NOT NULL,
    selected_title TEXT NOT NULL,
    description TEXT NOT NULL,
    chapters_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    pinned_comment TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS thumbnails (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    file_path_16_9 TEXT,
    file_path_9_16 TEXT,
    headline_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    provenance_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quota_usage_records (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    units_consumed INTEGER NOT NULL,
    daily_budget INTEGER NOT NULL DEFAULT 10000,
    consumed_date TEXT NOT NULL,
    project_id TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES video_projects(id) ON DELETE SET NULL
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

# Backwards compatibility v7 schema (prior to v8 analytics_snapshots evolution)
SCHEMA_V7_SQL = SCHEMA_V8_SQL.replace(
    """CREATE TABLE IF NOT EXISTS analytics_snapshots (
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_snapshots_window ON analytics_snapshots(project_id, source, report_end_date) WHERE report_end_date IS NOT NULL;""",
    """CREATE TABLE IF NOT EXISTS analytics_snapshots (
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
);"""
)

SCHEMA_V6_SQL = SCHEMA_V7_SQL
SCHEMA_V5_SQL = SCHEMA_V7_SQL
SCHEMA_V4_SQL = SCHEMA_V7_SQL
SCHEMA_V3_SQL = SCHEMA_V7_SQL
SCHEMA_V2_SQL = SCHEMA_V7_SQL



def migrate_database(db_path: Path) -> None:
    """Migrate SQLite database to the current schema version (v8).

    Handles:
    - Truly empty database -> direct v8 initialization.
    - Legacy Phase-3 database (user_version == 0 with tables or user_version == 1) -> migrate to v2 structure then v3 then v4.
    - Pure Phase-3.7 v2 or Phase-4.1 pseudo-v2 database (user_version == 2) -> shape-aware migration to v3 then v4.
    - Phase-4 v3 database (user_version == 3) -> v4 migration (contains_synthetic_media).
    - Phase-5 v4 database (user_version == 4) -> v5 migration (market_signal_snapshots).
    - Phase-6 v5 database (user_version == 5) -> v6 migration (opportunity_portfolios).
    - Phase-7 v6 database (user_version == 6) -> v7 migration (packaging_tournaments).
    - Phase-8 v7 database (user_version == 7) -> v8 migration (analytics_snapshots evolution: nullable CTR, retention curve, source).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path, timeout=30.0) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version;")
        current_version = cursor.fetchone()[0]

        # Check existing table count
        cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        user_table_count = cursor.fetchone()[0]

        if current_version == 0 and user_table_count == 0:
            # Truly empty/new database: apply full v8 schema directly
            conn.executescript(SCHEMA_V8_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            conn.commit()
            return


        # 1. Migrate v0/v1 legacy databases to v2 structure
        if current_version < 2:
            conn.execute("PRAGMA foreign_keys = OFF;")

            # Migrate topic_candidates: remove NOT NULL / DEFAULT 10.0 on estimated_cpm
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='topic_candidates';")
            if cursor.fetchone():
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS topic_candidates_v2 (
                        id TEXT PRIMARY KEY,
                        channel_id TEXT NOT NULL,
                        keyword TEXT NOT NULL,
                        opportunity_score REAL NOT NULL,
                        authority_score REAL NOT NULL,
                        estimated_cpm REAL,
                        rationale TEXT,
                        score_breakdown_json TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE
                    );

                    INSERT INTO topic_candidates_v2 (id, channel_id, keyword, opportunity_score, authority_score, estimated_cpm, rationale, score_breakdown_json, created_at)
                    SELECT id, channel_id, keyword, opportunity_score, authority_score, estimated_cpm, rationale, NULL, created_at
                    FROM topic_candidates;

                    DROP TABLE topic_candidates;
                    ALTER TABLE topic_candidates_v2 RENAME TO topic_candidates;
                    """
                )

            # Migrate idempotency_keys: composite (scope, key) PK and expires_at column
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='idempotency_keys';")
            if cursor.fetchone():
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
            current_version = 2

        # 2. Migrate v2 (or legacy upgraded to v2) to v3
        if current_version < 3:
            # Shape-aware column evolution for existing tables
            cursor.execute("PRAGMA table_info(topic_candidates);")
            tc_cols = {row[1] for row in cursor.fetchall()}
            if tc_cols and "score_breakdown_json" not in tc_cols:
                conn.execute("ALTER TABLE topic_candidates ADD COLUMN score_breakdown_json TEXT;")

            cursor.execute("PRAGMA table_info(scripts);")
            script_cols = {row[1] for row in cursor.fetchall()}
            if script_cols and "sections_json" not in script_cols:
                conn.execute("ALTER TABLE scripts ADD COLUMN sections_json TEXT;")
            if script_cols and "content_format" not in script_cols:
                conn.execute("ALTER TABLE scripts ADD COLUMN content_format TEXT NOT NULL DEFAULT 'EXPLAINER';")

            cursor.execute("PRAGMA table_info(channels);")
            chan_cols = {row[1] for row in cursor.fetchall()}
            if chan_cols and "youtube_category_id" not in chan_cols:
                conn.execute("ALTER TABLE channels ADD COLUMN youtube_category_id TEXT NOT NULL DEFAULT '28';")
            if chan_cols and "made_for_kids" not in chan_cols:
                conn.execute("ALTER TABLE channels ADD COLUMN made_for_kids INTEGER NOT NULL DEFAULT 0;")
            if chan_cols and "default_tags_json" not in chan_cols:
                conn.execute("ALTER TABLE channels ADD COLUMN default_tags_json TEXT;")

            cursor.execute("PRAGMA table_info(review_records);")
            rev_cols = {row[1] for row in cursor.fetchall()}
            if rev_cols and "approval_origin" not in rev_cols:
                conn.execute("ALTER TABLE review_records ADD COLUMN approval_origin TEXT NOT NULL DEFAULT 'AUTOMATION';")

            cursor.execute("PRAGMA table_info(analytics_snapshots);")
            snap_cols = {row[1] for row in cursor.fetchall()}
            if snap_cols and "snapshot_type" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN snapshot_type TEXT NOT NULL DEFAULT 'REAL';")
            if snap_cols and "is_simulated" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0;")

            # Ensure all intelligence tables exist
            conn.executescript(SCHEMA_V4_SQL)
            conn.execute("PRAGMA user_version = 3;")
            conn.commit()
            current_version = 3

        # 3. Migrate v3 to v4: publication_jobs.contains_synthetic_media
        if current_version < 4:
            cursor.execute("PRAGMA table_info(publication_jobs);")
            pub_cols = {row[1] for row in cursor.fetchall()}
            if pub_cols and "contains_synthetic_media" not in pub_cols:
                conn.execute(
                    "ALTER TABLE publication_jobs "
                    "ADD COLUMN contains_synthetic_media INTEGER NOT NULL DEFAULT 0;"
                )
            conn.executescript(SCHEMA_V5_SQL)
            conn.execute(f"PRAGMA user_version = 4;")
            conn.commit()
            current_version = 4

        # 4. Migrate v4 to v5: market_signal_snapshots
        if current_version < 5:
            conn.executescript(
                """
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
            )
            conn.execute("PRAGMA user_version = 5;")
            conn.commit()
            current_version = 5

        # 5. Migrate v5 to v6: opportunity_portfolios
        if current_version < 6:
            conn.executescript(
                """
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
            )
            conn.execute("PRAGMA user_version = 6;")
            conn.commit()
            current_version = 6

        # 6. Migrate v6 to v7: packaging_tournaments
        if current_version < 7:
            conn.executescript(
                """
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
            )
            conn.execute("PRAGMA user_version = 7;")
            conn.commit()
            current_version = 7

        # 7. Migrate v7 to v8: analytics_snapshots evolution (nullable CTR, impressions, source, window index)
        if current_version < 8:
            conn.execute("PRAGMA foreign_keys = OFF;")
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='analytics_snapshots';")
            if cursor.fetchone():
                cursor.execute("PRAGMA table_info(analytics_snapshots);")
                existing_cols = {row[1] for row in cursor.fetchall()}

                source_expr = (
                    "CASE WHEN is_simulated = 1 OR snapshot_type = 'SIMULATED' THEN 'SIMULATED' ELSE 'LEGACY_UNVERIFIED' END"
                    if "snapshot_type" in existing_cols and "is_simulated" in existing_cols
                    else "'LEGACY_UNVERIFIED'"
                )
                snap_type_expr = "snapshot_type" if "snapshot_type" in existing_cols else "'REAL'"
                is_sim_expr = "is_simulated" if "is_simulated" in existing_cols else "0"
                yt_id_expr = "youtube_video_id" if "youtube_video_id" in existing_cols else "NULL"
                avd_expr = "average_view_duration_seconds" if "average_view_duration_seconds" in existing_cols else "0.0"
                ret_3s_expr = "retention_at_3s_percent" if "retention_at_3s_percent" in existing_cols else "NULL"
                ret_curve_expr = "retention_curve_json" if "retention_curve_json" in existing_cols else "NULL"
                avp_expr = "average_view_percentage" if "average_view_percentage" in existing_cols else "NULL"
                imp_expr = "impressions" if "impressions" in existing_cols else "NULL"
                start_date_expr = "report_start_date" if "report_start_date" in existing_cols else "NULL"
                end_date_expr = "report_end_date" if "report_end_date" in existing_cols else "NULL"
                ctr_expr = "ctr_percent" if "ctr_percent" in existing_cols else ("ctr" if "ctr" in existing_cols else "NULL")
                captured_expr = "captured_at" if "captured_at" in existing_cols else ("collected_at" if "collected_at" in existing_cols else "datetime('now')")

                conn.executescript(
                    f"""
                    CREATE TABLE IF NOT EXISTS analytics_snapshots_v8 (
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

                    INSERT INTO analytics_snapshots_v8 (
                        id, project_id, youtube_video_id, source, snapshot_type, is_simulated,
                        report_start_date, report_end_date, views, watch_time_hours,
                        average_view_duration_seconds, average_view_percentage, impressions,
                        ctr_percent, retention_at_3s_percent, retention_curve_json, captured_at
                    )
                    SELECT
                        id, project_id, {yt_id_expr}, {source_expr}, {snap_type_expr}, {is_sim_expr},
                        {start_date_expr}, {end_date_expr}, views, watch_time_hours,
                        {avd_expr}, {avp_expr}, {imp_expr},
                        {ctr_expr}, {ret_3s_expr}, {ret_curve_expr}, {captured_expr}
                    FROM analytics_snapshots;

                    DROP TABLE analytics_snapshots;
                    ALTER TABLE analytics_snapshots_v8 RENAME TO analytics_snapshots;
                    CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_project ON analytics_snapshots(project_id);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_snapshots_window ON analytics_snapshots(project_id, source, report_end_date) WHERE report_end_date IS NOT NULL;
                    """
                )
            conn.executescript(SCHEMA_V8_SQL)
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            conn.commit()
            current_version = 8
        else:
            # Current v8 idempotent check
            conn.executescript(SCHEMA_V8_SQL)
            cursor.execute("PRAGMA table_info(topic_candidates);")
            tc_cols = {row[1] for row in cursor.fetchall()}
            if tc_cols and "score_breakdown_json" not in tc_cols:
                conn.execute("ALTER TABLE topic_candidates ADD COLUMN score_breakdown_json TEXT;")

            cursor.execute("PRAGMA table_info(scripts);")
            script_cols = {row[1] for row in cursor.fetchall()}
            if script_cols and "sections_json" not in script_cols:
                conn.execute("ALTER TABLE scripts ADD COLUMN sections_json TEXT;")
            if script_cols and "content_format" not in script_cols:
                conn.execute("ALTER TABLE scripts ADD COLUMN content_format TEXT NOT NULL DEFAULT 'EXPLAINER';")

            cursor.execute("PRAGMA table_info(channels);")
            chan_cols = {row[1] for row in cursor.fetchall()}
            if chan_cols and "youtube_category_id" not in chan_cols:
                conn.execute("ALTER TABLE channels ADD COLUMN youtube_category_id TEXT NOT NULL DEFAULT '28';")
            if chan_cols and "made_for_kids" not in chan_cols:
                conn.execute("ALTER TABLE channels ADD COLUMN made_for_kids INTEGER NOT NULL DEFAULT 0;")
            if chan_cols and "default_tags_json" not in chan_cols:
                conn.execute("ALTER TABLE channels ADD COLUMN default_tags_json TEXT;")

            cursor.execute("PRAGMA table_info(review_records);")
            rev_cols = {row[1] for row in cursor.fetchall()}
            if rev_cols and "approval_origin" not in rev_cols:
                conn.execute("ALTER TABLE review_records ADD COLUMN approval_origin TEXT NOT NULL DEFAULT 'AUTOMATION';")

            cursor.execute("PRAGMA table_info(analytics_snapshots);")
            snap_cols = {row[1] for row in cursor.fetchall()}
            if snap_cols and "snapshot_type" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN snapshot_type TEXT NOT NULL DEFAULT 'REAL';")
            if snap_cols and "is_simulated" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 0;")
            if snap_cols and "source" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN source TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED';")
            if snap_cols and "report_start_date" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN report_start_date TEXT;")
            if snap_cols and "report_end_date" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN report_end_date TEXT;")
            if snap_cols and "average_view_percentage" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN average_view_percentage REAL;")
            if snap_cols and "impressions" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN impressions INTEGER;")
            if snap_cols and "retention_curve_json" not in snap_cols:
                conn.execute("ALTER TABLE analytics_snapshots ADD COLUMN retention_curve_json TEXT;")

            cursor.execute("PRAGMA table_info(publication_jobs);")
            pub_cols = {row[1] for row in cursor.fetchall()}
            if pub_cols and "contains_synthetic_media" not in pub_cols:
                conn.execute(
                    "ALTER TABLE publication_jobs "
                    "ADD COLUMN contains_synthetic_media INTEGER NOT NULL DEFAULT 0;"
                )

            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            conn.commit()



def init_database(db_path: Path) -> None:
    """Initialize or migrate database to the current schema version."""
    migrate_database(db_path)

