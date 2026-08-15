"""SQLite repository implementation providing transactional CRUD and restart persistence."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.domain.enums import (
    AssetType,
    ExperimentStatus,
    PlatformFormat,
    PublicationStatus,
    QualityStatus,
    VideoLifecycleState,
)
from app.domain.models import (
    AnalyticsSnapshot,
    Asset,
    Channel,
    Experiment,
    PublicationJob,
    QualityResult,
    Scene,
    Script,
    TopicCandidate,
    VideoProject,
)
from app.db.schema import init_database


class SQLiteRepository:
    """Production SQLite repository for YouTube Autopilot domain entities."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        init_database(self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    # --- Channel CRUD ---
    def save_channel(self, channel: Channel) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO channels (id, title, handle, niche, target_audience, default_language, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    handle = excluded.handle,
                    niche = excluded.niche,
                    target_audience = excluded.target_audience,
                    default_language = excluded.default_language,
                    is_active = excluded.is_active
                """,
                (
                    channel.id,
                    channel.title,
                    channel.handle,
                    channel.niche,
                    channel.target_audience,
                    channel.default_language,
                    1 if channel.is_active else 0,
                    channel.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_channel(self, channel_id: str) -> Optional[Channel]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return Channel(
                id=row["id"],
                title=row["title"],
                handle=row["handle"],
                niche=row["niche"],
                target_audience=row["target_audience"],
                default_language=row["default_language"],
                is_active=bool(row["is_active"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    # --- VideoProject CRUD ---
    def save_video_project(self, project: VideoProject) -> None:
        tags_json = json.dumps(project.metadata_tags)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO video_projects (id, channel_id, title, format, state, metadata_tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    title = excluded.title,
                    format = excluded.format,
                    state = excluded.state,
                    metadata_tags = excluded.metadata_tags,
                    updated_at = excluded.updated_at
                """,
                (
                    project.id,
                    project.channel_id,
                    project.title,
                    project.format.value,
                    project.state.value,
                    tags_json,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                ),
            )

            # Persist script if present
            if project.script:
                scenes_json = json.dumps([scene.model_dump() for scene in project.script.scenes])
                cursor.execute(
                    """
                    INSERT INTO scripts (id, project_id, title, hook, scenes_json, total_word_count, estimated_duration_seconds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        title = excluded.title,
                        hook = excluded.hook,
                        scenes_json = excluded.scenes_json,
                        total_word_count = excluded.total_word_count,
                        estimated_duration_seconds = excluded.estimated_duration_seconds
                    """,
                    (
                        project.script.id,
                        project.id,
                        project.script.title,
                        project.script.hook,
                        scenes_json,
                        project.script.total_word_count,
                        project.script.estimated_duration_seconds,
                        project.script.created_at.isoformat(),
                    ),
                )

            # Persist QA if present
            if project.quality:
                issues_json = json.dumps(project.quality.issues)
                cursor.execute(
                    """
                    INSERT INTO quality_results (id, project_id, status, loudness_lufs, duration_seconds, sync_drift_ms, issues_json, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        status = excluded.status,
                        loudness_lufs = excluded.loudness_lufs,
                        duration_seconds = excluded.duration_seconds,
                        sync_drift_ms = excluded.sync_drift_ms,
                        issues_json = excluded.issues_json,
                        checked_at = excluded.checked_at
                    """,
                    (
                        project.quality.id,
                        project.id,
                        project.quality.status.value,
                        project.quality.loudness_lufs,
                        project.quality.duration_seconds,
                        project.quality.sync_drift_ms,
                        issues_json,
                        project.quality.checked_at.isoformat(),
                    ),
                )

            # Persist Assets
            for asset in project.assets:
                cursor.execute(
                    """
                    INSERT INTO assets (id, project_id, asset_type, file_path, source_url, license_type, content_sha256, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        asset.id,
                        project.id,
                        asset.asset_type.value,
                        asset.file_path,
                        asset.source_url,
                        asset.license_type,
                        asset.content_sha256,
                        asset.created_at.isoformat(),
                    ),
                )

            conn.commit()

    def get_video_project(self, project_id: str) -> Optional[VideoProject]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM video_projects WHERE id = ?", (project_id,))
            p_row = cursor.fetchone()
            if not p_row:
                return None

            tags = json.loads(p_row["metadata_tags"]) if p_row["metadata_tags"] else []

            # Load Script
            cursor.execute("SELECT * FROM scripts WHERE project_id = ?", (project_id,))
            s_row = cursor.fetchone()
            script = None
            if s_row:
                raw_scenes = json.loads(s_row["scenes_json"])
                scenes = [Scene(**item) for item in raw_scenes]
                script = Script(
                    id=s_row["id"],
                    title=s_row["title"],
                    hook=s_row["hook"],
                    scenes=scenes,
                    total_word_count=s_row["total_word_count"],
                    estimated_duration_seconds=s_row["estimated_duration_seconds"],
                    created_at=datetime.fromisoformat(s_row["created_at"]),
                )

            # Load Assets
            cursor.execute("SELECT * FROM assets WHERE project_id = ?", (project_id,))
            assets = []
            for a_row in cursor.fetchall():
                assets.append(
                    Asset(
                        id=a_row["id"],
                        project_id=a_row["project_id"],
                        asset_type=AssetType(a_row["asset_type"]),
                        file_path=a_row["file_path"],
                        source_url=a_row["source_url"],
                        license_type=a_row["license_type"],
                        content_sha256=a_row["content_sha256"],
                        created_at=datetime.fromisoformat(a_row["created_at"]),
                    )
                )

            # Load Quality
            cursor.execute("SELECT * FROM quality_results WHERE project_id = ?", (project_id,))
            q_row = cursor.fetchone()
            quality = None
            if q_row:
                issues = json.loads(q_row["issues_json"]) if q_row["issues_json"] else []
                quality = QualityResult(
                    id=q_row["id"],
                    project_id=q_row["project_id"],
                    status=QualityStatus(q_row["status"]),
                    loudness_lufs=q_row["loudness_lufs"],
                    duration_seconds=q_row["duration_seconds"],
                    sync_drift_ms=q_row["sync_drift_ms"],
                    issues=issues,
                    checked_at=datetime.fromisoformat(q_row["checked_at"]),
                )

            return VideoProject(
                id=p_row["id"],
                channel_id=p_row["channel_id"],
                title=p_row["title"],
                format=PlatformFormat(p_row["format"]),
                state=VideoLifecycleState(p_row["state"]),
                script=script,
                assets=assets,
                quality=quality,
                metadata_tags=tags,
                created_at=datetime.fromisoformat(p_row["created_at"]),
                updated_at=datetime.fromisoformat(p_row["updated_at"]),
            )

    def list_video_projects(self, state: Optional[VideoLifecycleState] = None) -> List[VideoProject]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if state:
                cursor.execute("SELECT id FROM video_projects WHERE state = ? ORDER BY created_at DESC", (state.value,))
            else:
                cursor.execute("SELECT id FROM video_projects ORDER BY created_at DESC")
            ids = [row["id"] for row in cursor.fetchall()]

        results = []
        for pid in ids:
            proj = self.get_video_project(pid)
            if proj:
                results.append(proj)
        return results

    def update_project_state(
        self, project_id: str, from_state: VideoLifecycleState, to_state: VideoLifecycleState, reason: str = ""
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE video_projects SET state = ?, updated_at = ? WHERE id = ?",
                (to_state.value, now_iso, project_id),
            )
            cursor.execute(
                """
                INSERT INTO state_transitions (project_id, from_state, to_state, reason, transitioned_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, from_state.value, to_state.value, reason, now_iso),
            )
            conn.commit()

    def get_state_history(self, project_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT from_state, to_state, reason, transitioned_at FROM state_transitions WHERE project_id = ? ORDER BY id ASC",
                (project_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # --- Publication Jobs & Queue ---
    def save_publication_job(self, job: PublicationJob) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO publication_jobs (id, project_id, channel_id, status, privacy_status, scheduled_publish_time, youtube_video_id, published_at, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    privacy_status = excluded.privacy_status,
                    scheduled_publish_time = excluded.scheduled_publish_time,
                    youtube_video_id = excluded.youtube_video_id,
                    published_at = excluded.published_at,
                    error_message = excluded.error_message
                """,
                (
                    job.id,
                    job.project_id,
                    job.channel_id,
                    job.status.value,
                    job.privacy_status,
                    job.scheduled_publish_time.isoformat() if job.scheduled_publish_time else None,
                    job.youtube_video_id,
                    job.published_at.isoformat() if job.published_at else None,
                    job.error_message,
                    job.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_publication_queue(self, status: Optional[PublicationStatus] = None) -> List[PublicationJob]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM publication_jobs WHERE status = ? ORDER BY created_at ASC", (status.value,))
            else:
                cursor.execute("SELECT * FROM publication_jobs ORDER BY created_at ASC")
            rows = cursor.fetchall()

        jobs = []
        for r in rows:
            sched = datetime.fromisoformat(r["scheduled_publish_time"]) if r["scheduled_publish_time"] else None
            pub = datetime.fromisoformat(r["published_at"]) if r["published_at"] else None
            jobs.append(
                PublicationJob(
                    id=r["id"],
                    project_id=r["project_id"],
                    channel_id=r["channel_id"],
                    status=PublicationStatus(r["status"]),
                    privacy_status=r["privacy_status"],
                    scheduled_publish_time=sched,
                    youtube_video_id=r["youtube_video_id"],
                    published_at=pub,
                    error_message=r["error_message"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
            )
        return jobs
