"""SQLite repository providing persistence operations for domain models."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import List, Optional

from app.db.schema import init_database
from app.domain.enums import (
    AssetType,
    ClaimVerificationVerdict,
    PlatformFormat,
    PrivacyStatus,
    PublicationStatus,
    QualityStatus,
    VideoLifecycleState,
)
from app.domain.state_machine import InvalidStateTransitionError, LifecycleStateMachine
from app.domain.models import (
    Asset,
    Channel,
    Claim,
    FactCheckReport,
    PublicationJob,
    QualityResult,
    ResearchDossier,
    ResearchSource,
    Scene,
    Script,
    ScriptSections,
    TopicCandidate,
    TopicScoreBreakdown,
    VideoProject,
)


class StateConcurrencyError(RuntimeError):
    """Raised when concurrent state modification is detected during compare-and-set."""
    pass


class SQLiteRepository:
    """Handles CRUD and query operations on the SQLite database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        init_database(self.db_path)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    # --- Channel Operations ---
    def save_channel(self, channel: Channel) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channels (id, title, handle, niche, target_audience, default_language, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    handle=excluded.handle,
                    niche=excluded.niche,
                    target_audience=excluded.target_audience,
                    default_language=excluded.default_language,
                    is_active=excluded.is_active;
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

    def get_channel(self, channel_id: str) -> Optional[Channel]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
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

    # --- Topic Candidate Operations ---
    def save_topic_candidate(self, candidate: TopicCandidate) -> None:
        breakdown_json = candidate.score_breakdown.model_dump_json() if candidate.score_breakdown else None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO topic_candidates (id, channel_id, keyword, opportunity_score, authority_score, estimated_cpm, rationale, score_breakdown_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    keyword=excluded.keyword,
                    opportunity_score=excluded.opportunity_score,
                    authority_score=excluded.authority_score,
                    estimated_cpm=excluded.estimated_cpm,
                    rationale=excluded.rationale,
                    score_breakdown_json=excluded.score_breakdown_json;
                """,
                (
                    candidate.id,
                    candidate.channel_id,
                    candidate.keyword,
                    candidate.opportunity_score,
                    candidate.authority_score,
                    candidate.estimated_cpm,
                    candidate.rationale,
                    breakdown_json,
                    candidate.created_at.isoformat(),
                ),
            )

    # --- Video Project & Lifecycle Operations ---
    def save_video_project(self, project: VideoProject) -> None:
        """Persist or update video project while strictly preventing lifecycle state machine bypass."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT state FROM video_projects WHERE id = ?", (project.id,)).fetchone()
            tags_json = json.dumps(project.metadata_tags)
            now_iso = datetime.now(timezone.utc).isoformat()

            if row is None:
                if project.state != VideoLifecycleState.CREATED:
                    raise ValueError(f"New video project must start in CREATED state. Cannot persist as '{project.state.value}'.")
                initial_state = VideoLifecycleState.CREATED
                conn.execute(
                    """
                    INSERT INTO video_projects (id, channel_id, title, format, state, metadata_tags, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        project.id,
                        project.channel_id,
                        project.title,
                        project.format.value,
                        initial_state.value,
                        tags_json,
                        project.created_at.isoformat(),
                        now_iso,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE video_projects
                    SET channel_id = ?, title = ?, format = ?, metadata_tags = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (
                        project.channel_id,
                        project.title,
                        project.format.value,
                        tags_json,
                        now_iso,
                        project.id,
                    ),
                )

            # Persist nested Script if present
            if project.script:
                scenes_json = json.dumps([s.model_dump() for s in project.script.scenes])
                sections_json = project.script.sections.model_dump_json() if project.script.sections else None
                conn.execute(
                    """
                    INSERT INTO scripts (id, project_id, title, hook, scenes_json, sections_json, total_word_count, estimated_duration_seconds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        title=excluded.title,
                        hook=excluded.hook,
                        scenes_json=excluded.scenes_json,
                        sections_json=excluded.sections_json,
                        total_word_count=excluded.total_word_count,
                        estimated_duration_seconds=excluded.estimated_duration_seconds;
                    """,
                    (
                        project.script.id,
                        project.id,
                        project.script.title,
                        project.script.hook,
                        scenes_json,
                        sections_json,
                        project.script.total_word_count,
                        project.script.estimated_duration_seconds,
                        project.script.created_at.isoformat(),
                    ),
                )

            # Persist nested QualityResult if present
            if project.quality:
                issues_json = json.dumps(project.quality.issues)
                conn.execute(
                    """
                    INSERT INTO quality_results (id, project_id, status, loudness_lufs, duration_seconds, sync_drift_ms, issues_json, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        status=excluded.status,
                        loudness_lufs=excluded.loudness_lufs,
                        duration_seconds=excluded.duration_seconds,
                        sync_drift_ms=excluded.sync_drift_ms,
                        issues_json=excluded.issues_json,
                        checked_at=excluded.checked_at;
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

    def update_project_state(
        self,
        project_id: str,
        to_state: VideoLifecycleState,
        reason: Optional[str] = None,
        expected_from_state: Optional[VideoLifecycleState] = None,
        expected_current_state: Optional[VideoLifecycleState] = None,
    ) -> None:
        """Sole legal lifecycle mutation path with compare-and-set atomic verification."""
        expected_state = expected_from_state or expected_current_state
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT state FROM video_projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                raise ValueError(f"Project '{project_id}' not found.")

            actual_current_state = VideoLifecycleState(row["state"])

            if expected_state is not None and actual_current_state != expected_state:
                raise StateConcurrencyError(
                    f"Expected state {expected_state.value} does not match current state {actual_current_state.value} for project '{project_id}'."
                )

            sm = LifecycleStateMachine(current_state=actual_current_state)
            sm.transition_to(to_state=to_state, reason=reason or "")

            now_iso = datetime.now(timezone.utc).isoformat()
            update_cursor = conn.execute(
                """
                UPDATE video_projects
                SET state = ?, updated_at = ?
                WHERE id = ? AND state = ?;
                """,
                (to_state.value, now_iso, project_id, actual_current_state.value),
            )

            if update_cursor.rowcount != 1:
                raise StateConcurrencyError(
                    f"CAS failed on project '{project_id}': concurrent modification detected."
                )

            conn.execute(
                """
                INSERT INTO state_transitions (project_id, from_state, to_state, reason, transitioned_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (project_id, actual_current_state.value, to_state.value, reason, now_iso),
            )

    def get_state_history(self, project_id: str) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM state_transitions WHERE project_id = ? ORDER BY id ASC;",
                (project_id,),
            ).fetchall()
            return [
                {
                    "from_state": r["from_state"],
                    "to_state": r["to_state"],
                    "reason": r["reason"],
                    "transitioned_at": r["transitioned_at"],
                }
                for r in rows
            ]

    def get_video_project(self, project_id: str) -> Optional[VideoProject]:
        with self._get_connection() as conn:
            p_row = conn.execute("SELECT * FROM video_projects WHERE id = ?", (project_id,)).fetchone()
            if not p_row:
                return None

            s_row = conn.execute("SELECT * FROM scripts WHERE project_id = ?", (project_id,)).fetchone()
            script = None
            if s_row:
                scenes_data = json.loads(s_row["scenes_json"])
                scenes = [Scene.model_validate(s) for s in scenes_data]
                sections = None
                if "sections_json" in s_row.keys() and s_row["sections_json"]:
                    sections = ScriptSections.model_validate(json.loads(s_row["sections_json"]))
                script = Script(
                    id=s_row["id"],
                    title=s_row["title"],
                    hook=s_row["hook"],
                    scenes=scenes,
                    sections=sections,
                    total_word_count=s_row["total_word_count"],
                    estimated_duration_seconds=s_row["estimated_duration_seconds"],
                    created_at=datetime.fromisoformat(s_row["created_at"]),
                )

            q_row = conn.execute("SELECT * FROM quality_results WHERE project_id = ?", (project_id,)).fetchone()
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

            a_rows = conn.execute("SELECT * FROM assets WHERE project_id = ?", (project_id,)).fetchall()
            assets = [
                Asset(
                    id=a["id"],
                    project_id=a["project_id"],
                    asset_type=AssetType(a["asset_type"]),
                    file_path=a["file_path"],
                    source_url=a["source_url"],
                    license_type=a["license_type"],
                    content_sha256=a["content_sha256"],
                    created_at=datetime.fromisoformat(a["created_at"]),
                )
                for a in a_rows
            ]

            tags = json.loads(p_row["metadata_tags"]) if p_row["metadata_tags"] else []

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
            if state:
                rows = conn.execute("SELECT id FROM video_projects WHERE state = ? ORDER BY created_at DESC;", (state.value,)).fetchall()
            else:
                rows = conn.execute("SELECT id FROM video_projects ORDER BY created_at DESC;").fetchall()
            projects = []
            for r in rows:
                proj = self.get_video_project(r["id"])
                if proj:
                    projects.append(proj)
            return projects

    # --- Phase 4 Evidence & Fact-Checking Persistence ---
    def save_research_dossier(self, project_id: str, dossier: ResearchDossier) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO research_dossiers (id, project_id, topic_id, summary, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET summary=excluded.summary;
                """,
                (dossier.id, project_id, dossier.topic_id, dossier.summary, dossier.created_at.isoformat()),
            )
            for src in dossier.sources:
                conn.execute(
                    """
                    INSERT INTO research_sources (id, dossier_id, url, final_url, http_status, title, content_sha256, content_snapshot, content_snapshot_path, license_type, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content_sha256=excluded.content_sha256,
                        content_snapshot=excluded.content_snapshot,
                        content_snapshot_path=excluded.content_snapshot_path;
                    """,
                    (
                        src.id,
                        dossier.id,
                        src.url,
                        src.final_url,
                        src.http_status,
                        src.title,
                        src.content_sha256,
                        src.content_snapshot,
                        src.content_snapshot_path,
                        src.license_type,
                        src.fetched_at.isoformat(),
                    ),
                )

    def save_fact_check_report(self, report: FactCheckReport) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO fact_check_reports (id, project_id, verified_count, failed_count, overall_verdict, audit_summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    verified_count=excluded.verified_count,
                    failed_count=excluded.failed_count,
                    overall_verdict=excluded.overall_verdict,
                    audit_summary=excluded.audit_summary;
                """,
                (
                    report.id,
                    report.project_id,
                    report.verified_count,
                    report.failed_count,
                    report.overall_verdict.value,
                    report.audit_summary,
                    report.created_at.isoformat(),
                ),
            )
            for claim in report.claims:
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO claims (id, project_id, source_id, statement, verified, verdict, confidence_score, cited_url, cited_excerpt, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        verified=excluded.verified,
                        verdict=excluded.verdict,
                        confidence_score=excluded.confidence_score,
                        cited_url=excluded.cited_url,
                        cited_excerpt=excluded.cited_excerpt,
                        notes=excluded.notes;
                    """,
                    (
                        claim.id,
                        report.project_id,
                        claim.source_id,
                        claim.statement,
                        1 if claim.verified else 0,
                        claim.verdict.value,
                        claim.confidence_score,
                        claim.cited_url,
                        claim.cited_excerpt,
                        claim.notes,
                        now_iso,
                    ),
                )

    # --- Publication & Queue Operations ---
    def save_publication_job(self, job: PublicationJob) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO publication_jobs (id, project_id, channel_id, status, privacy_status, scheduled_publish_time, youtube_video_id, published_at, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    privacy_status=excluded.privacy_status,
                    scheduled_publish_time=excluded.scheduled_publish_time,
                    youtube_video_id=excluded.youtube_video_id,
                    published_at=excluded.published_at,
                    error_message=excluded.error_message;
                """,
                (
                    job.id,
                    job.project_id,
                    job.channel_id,
                    job.status.value,
                    job.privacy_status.value,
                    job.scheduled_publish_time.isoformat() if job.scheduled_publish_time else None,
                    job.youtube_video_id,
                    job.published_at.isoformat() if job.published_at else None,
                    job.error_message,
                    job.created_at.isoformat(),
                ),
            )

    def get_publication_queue(self, status: Optional[PublicationStatus] = None) -> List[PublicationJob]:
        with self._get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM publication_jobs WHERE status = ? ORDER BY created_at ASC;",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM publication_jobs WHERE status IN ('PENDING', 'SCHEDULED') ORDER BY created_at ASC;"
                ).fetchall()
            return [
                PublicationJob(
                    id=r["id"],
                    project_id=r["project_id"],
                    channel_id=r["channel_id"],
                    status=PublicationStatus(r["status"]),
                    privacy_status=PrivacyStatus(r["privacy_status"]),
                    scheduled_publish_time=datetime.fromisoformat(r["scheduled_publish_time"]) if r["scheduled_publish_time"] else None,
                    youtube_video_id=r["youtube_video_id"],
                    published_at=datetime.fromisoformat(r["published_at"]) if r["published_at"] else None,
                    error_message=r["error_message"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
                for r in rows
            ]
