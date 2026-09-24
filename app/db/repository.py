"""SQLite repository providing persistence operations for domain models."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, List, Optional

from app.db.schema import init_database
from app.domain.enums import (
    AnalyticsSource,
    ApprovalOrigin,
    AssetType,
    ClaimVerificationVerdict,
    ContentFormat,
    EditorialSlotStatus,
    PackagingTournamentStatus,
    PlatformFormat,
    PrivacyStatus,
    PublicationStatus,
    QualityStatus,
    ReviewAction,
    TitleVariantType,
    VideoLifecycleState,
)
from app.domain.state_machine import InvalidStateTransitionError, LifecycleStateMachine
from app.domain.models import (
    AnalyticsSnapshot,
    Asset,
    RetentionPoint,
    Channel,
    Chapter,
    Claim,
    ContentSeries,
    EditorialSlot,
    FactCheckReport,
    MarketSignalSnapshot,
    OpportunityPortfolio,
    PackagingCandidate,
    PackagingReachFeedback,
    PackagingTournament,
    PublicationJob,
    QualityResult,
    QuotaUsageRecord,
    ReachObservation,
    ReportingJobState,
    ReportingReportReceipt,
    ResearchDossier,
    ResearchSource,
    ReviewRecord,
    Scene,
    Script,
    ScriptSections,
    SEOPackage,
    ThumbnailPackage,
    TitleVariant,
    TopicCandidate,
    TopicOpportunity,
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
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    # --- Channel Operations ---
    def save_channel(self, channel: Channel) -> None:
        tags_json = json.dumps(channel.default_tags) if channel.default_tags else None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channels (id, title, handle, niche, target_audience, default_language, youtube_category_id, made_for_kids, default_tags_json, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    handle=excluded.handle,
                    niche=excluded.niche,
                    target_audience=excluded.target_audience,
                    default_language=excluded.default_language,
                    youtube_category_id=excluded.youtube_category_id,
                    made_for_kids=excluded.made_for_kids,
                    default_tags_json=excluded.default_tags_json,
                    is_active=excluded.is_active;
                """,
                (
                    channel.id,
                    channel.title,
                    channel.handle,
                    channel.niche,
                    channel.target_audience,
                    channel.default_language,
                    channel.youtube_category_id,
                    1 if channel.made_for_kids else 0,
                    tags_json,
                    1 if channel.is_active else 0,
                    channel.created_at.isoformat(),
                ),
            )

    def get_channel(self, channel_id: str) -> Optional[Channel]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
            if not row:
                return None
            keys = row.keys()
            cat_id = row["youtube_category_id"] if "youtube_category_id" in keys and row["youtube_category_id"] else "28"
            kids = bool(row["made_for_kids"]) if "made_for_kids" in keys and row["made_for_kids"] is not None else False
            tags = json.loads(row["default_tags_json"]) if "default_tags_json" in keys and row["default_tags_json"] else []
            return Channel(
                id=row["id"],
                title=row["title"],
                handle=row["handle"],
                niche=row["niche"],
                target_audience=row["target_audience"],
                default_language=row["default_language"],
                youtube_category_id=cat_id,
                made_for_kids=kids,
                default_tags=tags,
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
                if project.script.sections is None and (
                    project.script.retention_report is not None
                    or project.script.retention_blueprint is not None
                ):
                    project.script.sections = ScriptSections(
                        hook=project.script.hook,
                        intro="",
                        segments=project.script.scenes,
                        cta="",
                        estimated_duration=project.script.estimated_duration_seconds or 30.0,
                        retention_report=project.script.retention_report,
                        retention_blueprint=project.script.retention_blueprint,
                    )
                elif project.script.sections:
                    if project.script.retention_report is not None:
                        project.script.sections.retention_report = project.script.retention_report
                    elif project.script.sections.retention_report is not None:
                        project.script.retention_report = project.script.sections.retention_report

                    if project.script.retention_blueprint is not None:
                        project.script.sections.retention_blueprint = project.script.retention_blueprint
                    elif project.script.sections.retention_blueprint is not None:
                        project.script.retention_blueprint = project.script.sections.retention_blueprint

                scenes_json = json.dumps([s.model_dump() for s in project.script.scenes])
                sections_json = project.script.sections.model_dump_json() if project.script.sections else None
                fmt_val = getattr(project.script, "content_format", ContentFormat.EXPLAINER)
                fmt_str = fmt_val.value if hasattr(fmt_val, "value") else str(fmt_val)
                conn.execute(
                    """
                    INSERT INTO scripts (id, project_id, title, hook, scenes_json, sections_json, content_format, total_word_count, estimated_duration_seconds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        title=excluded.title,
                        hook=excluded.hook,
                        scenes_json=excluded.scenes_json,
                        sections_json=excluded.sections_json,
                        content_format=excluded.content_format,
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
                        fmt_str,
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

            # Persist nested Assets if present
            if project.assets:
                for asset in project.assets:
                    conn.execute(
                        """
                        INSERT INTO assets (id, project_id, asset_type, file_path, source_url, license_type, content_sha256, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            file_path=excluded.file_path,
                            source_url=excluded.source_url,
                            license_type=excluded.license_type,
                            content_sha256=excluded.content_sha256;
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

    def save_asset(self, asset: Asset) -> None:
        """Persist or update an individual Asset record."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO assets (id, project_id, asset_type, file_path, source_url, license_type, content_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    file_path=excluded.file_path,
                    source_url=excluded.source_url,
                    license_type=excluded.license_type,
                    content_sha256=excluded.content_sha256;
                """,
                (
                    asset.id,
                    asset.project_id,
                    asset.asset_type.value,
                    asset.file_path,
                    asset.source_url,
                    asset.license_type,
                    asset.content_sha256,
                    asset.created_at.isoformat(),
                ),
            )

    def save_assets(self, assets: List[Asset]) -> None:
        """Persist multiple Asset records in a single transaction."""
        with self._get_connection() as conn:
            for asset in assets:
                conn.execute(
                    """
                    INSERT INTO assets (id, project_id, asset_type, file_path, source_url, license_type, content_sha256, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        file_path=excluded.file_path,
                        source_url=excluded.source_url,
                        license_type=excluded.license_type,
                        content_sha256=excluded.content_sha256;
                    """,
                    (
                        asset.id,
                        asset.project_id,
                        asset.asset_type.value,
                        asset.file_path,
                        asset.source_url,
                        asset.license_type,
                        asset.content_sha256,
                        asset.created_at.isoformat(),
                    ),
                )

    def get_assets_by_project(self, project_id: str) -> List[Asset]:
        """Retrieve all persisted assets associated with a project."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM assets WHERE project_id = ? ORDER BY created_at ASC;", (project_id,)).fetchall()
            return [
                Asset(
                    id=r["id"],
                    project_id=r["project_id"],
                    asset_type=AssetType(r["asset_type"]),
                    file_path=r["file_path"],
                    source_url=r["source_url"],
                    license_type=r["license_type"],
                    content_sha256=r["content_sha256"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
                for r in rows
            ]

    def save_quality_result(self, quality: QualityResult) -> None:
        """Persist or update an individual QualityResult record."""
        issues_json = json.dumps(quality.issues)
        with self._get_connection() as conn:
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
                    quality.id,
                    quality.project_id,
                    quality.status.value,
                    quality.loudness_lufs,
                    quality.duration_seconds,
                    quality.sync_drift_ms,
                    issues_json,
                    quality.checked_at.isoformat(),
                ),
            )

    def get_quality_result(self, project_id: str) -> Optional[QualityResult]:
        """Retrieve quality result for a project."""
        with self._get_connection() as conn:
            q_row = conn.execute("SELECT * FROM quality_results WHERE project_id = ?", (project_id,)).fetchone()
            if not q_row:
                return None
            issues = json.loads(q_row["issues_json"]) if q_row["issues_json"] else []
            return QualityResult(
                id=q_row["id"],
                project_id=q_row["project_id"],
                status=QualityStatus(q_row["status"]),
                loudness_lufs=q_row["loudness_lufs"],
                duration_seconds=q_row["duration_seconds"],
                sync_drift_ms=q_row["sync_drift_ms"],
                issues=issues,
                checked_at=datetime.fromisoformat(q_row["checked_at"]),
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

                fmt_str = s_row["content_format"] if "content_format" in s_row.keys() and s_row["content_format"] else "EXPLAINER"
                try:
                    c_format = ContentFormat(fmt_str)
                except ValueError:
                    c_format = ContentFormat.EXPLAINER

                script = Script(
                    id=s_row["id"],
                    title=s_row["title"],
                    hook=s_row["hook"],
                    scenes=scenes,
                    sections=sections,
                    content_format=c_format,
                    total_word_count=s_row["total_word_count"],
                    estimated_duration_seconds=s_row["estimated_duration_seconds"],
                    retention_blueprint=sections.retention_blueprint if sections else None,
                    retention_report=sections.retention_report if sections else None,
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
            p_content_format = script.content_format if script and hasattr(script, "content_format") and script.content_format else ContentFormat.EXPLAINER

            return VideoProject(
                id=p_row["id"],
                channel_id=p_row["channel_id"],
                title=p_row["title"],
                format=PlatformFormat(p_row["format"]),
                state=VideoLifecycleState(p_row["state"]),
                content_format=p_content_format,
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

    def get_project(self, project_id: str) -> Optional[VideoProject]:
        """Convenience alias for get_video_project."""
        return self.get_video_project(project_id)

    def list_video_projects_by_channel(self, channel_id: str) -> List[VideoProject]:
        """Retrieve all video projects for a specific channel."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM video_projects WHERE channel_id = ? ORDER BY created_at DESC;",
                (channel_id,),
            ).fetchall()
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
                    id=excluded.id,
                    verified_count=excluded.verified_count,
                    failed_count=excluded.failed_count,
                    overall_verdict=excluded.overall_verdict,
                    audit_summary=excluded.audit_summary,
                    created_at=excluded.created_at;
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
            # Transactionally replace project's claims to guarantee exact parity with final report
            conn.execute("DELETE FROM claims WHERE project_id = ?", (report.project_id,))
            for claim in report.claims:
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO claims (id, project_id, source_id, statement, verified, verdict, confidence_score, cited_url, cited_excerpt, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def get_research_dossier(self, project_id: str) -> Optional[ResearchDossier]:
        with self._get_connection() as conn:
            d_row = conn.execute("SELECT * FROM research_dossiers WHERE project_id = ?", (project_id,)).fetchone()
            if not d_row:
                return None
            s_rows = conn.execute("SELECT * FROM research_sources WHERE dossier_id = ?", (d_row["id"],)).fetchall()
            sources = [
                ResearchSource(
                    id=s["id"],
                    url=s["url"],
                    final_url=s["final_url"],
                    http_status=s["http_status"],
                    title=s["title"],
                    content_sha256=s["content_sha256"],
                    content_snapshot=s["content_snapshot"],
                    content_snapshot_path=s["content_snapshot_path"],
                    license_type=s["license_type"],
                    fetched_at=datetime.fromisoformat(s["fetched_at"]),
                )
                for s in s_rows
            ]
            return ResearchDossier(
                id=d_row["id"],
                topic_id=d_row["topic_id"],
                sources=sources,
                summary=d_row["summary"],
                created_at=datetime.fromisoformat(d_row["created_at"]),
            )

    def get_fact_check_report(self, project_id: str) -> Optional[FactCheckReport]:
        with self._get_connection() as conn:
            r_row = conn.execute("SELECT * FROM fact_check_reports WHERE project_id = ?", (project_id,)).fetchone()
            if not r_row:
                return None
            c_rows = conn.execute("SELECT * FROM claims WHERE project_id = ?", (project_id,)).fetchall()
            claims = [
                Claim(
                    id=c["id"],
                    source_id=c["source_id"],
                    statement=c["statement"],
                    verified=bool(c["verified"]),
                    verdict=ClaimVerificationVerdict(c["verdict"]),
                    confidence_score=c["confidence_score"],
                    cited_url=c["cited_url"],
                    cited_excerpt=c["cited_excerpt"],
                    notes=c["notes"],
                )
                for c in c_rows
            ]
            return FactCheckReport(
                id=r_row["id"],
                project_id=r_row["project_id"],
                claims=claims,
                verified_count=r_row["verified_count"],
                failed_count=r_row["failed_count"],
                overall_verdict=QualityStatus(r_row["overall_verdict"]),
                audit_summary=r_row["audit_summary"],
                created_at=datetime.fromisoformat(r_row["created_at"]),
            )

    def get_claims_by_project(self, project_id: str) -> List[Claim]:
        with self._get_connection() as conn:
            c_rows = conn.execute("SELECT * FROM claims WHERE project_id = ?", (project_id,)).fetchall()
            return [
                Claim(
                    id=c["id"],
                    source_id=c["source_id"],
                    statement=c["statement"],
                    verified=bool(c["verified"]),
                    verdict=ClaimVerificationVerdict(c["verdict"]),
                    confidence_score=c["confidence_score"],
                    cited_url=c["cited_url"],
                    cited_excerpt=c["cited_excerpt"],
                    notes=c["notes"],
                )
                for c in c_rows
            ]

    # --- Publication & Queue Operations ---
    def save_publication_job(self, job: PublicationJob) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO publication_jobs (
                    id, project_id, channel_id, status, privacy_status, scheduled_publish_time,
                    youtube_video_id, published_at, contains_synthetic_media,
                    packaging_tournament_id, packaging_candidate_id, deployed_title,
                    deployed_thumbnail_sha256, packaging_fingerprint, packaging_attribution_status,
                    error_message, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    privacy_status=excluded.privacy_status,
                    scheduled_publish_time=excluded.scheduled_publish_time,
                    youtube_video_id=excluded.youtube_video_id,
                    published_at=excluded.published_at,
                    contains_synthetic_media=excluded.contains_synthetic_media,
                    packaging_tournament_id=excluded.packaging_tournament_id,
                    packaging_candidate_id=excluded.packaging_candidate_id,
                    deployed_title=excluded.deployed_title,
                    deployed_thumbnail_sha256=excluded.deployed_thumbnail_sha256,
                    packaging_fingerprint=excluded.packaging_fingerprint,
                    packaging_attribution_status=excluded.packaging_attribution_status,
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
                    1 if job.contains_synthetic_media else 0,
                    job.packaging_tournament_id,
                    job.packaging_candidate_id,
                    job.deployed_title,
                    job.deployed_thumbnail_sha256,
                    job.packaging_fingerprint,
                    job.packaging_attribution_status,
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
            return [self._row_to_publication_job(r) for r in rows]

    def get_publication_job_by_project(self, project_id: str) -> Optional[PublicationJob]:
        """Retrieve the most recent publication job for a project."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM publication_jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1;",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_publication_job(row)

    def get_publication_job_by_youtube_video_id(self, youtube_video_id: str) -> Optional[PublicationJob]:
        """Retrieve real/scheduled publication job by YouTube video ID (excluding dry-runs and pending)."""
        if not youtube_video_id or youtube_video_id.startswith("yt-dryrun-"):
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM publication_jobs
                WHERE youtube_video_id = ?
                  AND status IN ('COMPLETED', 'SCHEDULED')
                ORDER BY created_at DESC LIMIT 1;
                """,
                (youtube_video_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_publication_job(row)

    def _row_to_publication_job(self, r: sqlite3.Row) -> PublicationJob:
        keys = r.keys()
        return PublicationJob(
            id=r["id"],
            project_id=r["project_id"],
            channel_id=r["channel_id"],
            status=PublicationStatus(r["status"]),
            privacy_status=PrivacyStatus(r["privacy_status"]),
            scheduled_publish_time=datetime.fromisoformat(r["scheduled_publish_time"]) if r["scheduled_publish_time"] else None,
            youtube_video_id=r["youtube_video_id"],
            published_at=datetime.fromisoformat(r["published_at"]) if r["published_at"] else None,
            contains_synthetic_media=bool(r["contains_synthetic_media"]) if "contains_synthetic_media" in keys else False,
            packaging_tournament_id=r["packaging_tournament_id"] if "packaging_tournament_id" in keys else None,
            packaging_candidate_id=r["packaging_candidate_id"] if "packaging_candidate_id" in keys else None,
            deployed_title=r["deployed_title"] if "deployed_title" in keys else None,
            deployed_thumbnail_sha256=r["deployed_thumbnail_sha256"] if "deployed_thumbnail_sha256" in keys else None,
            packaging_fingerprint=r["packaging_fingerprint"] if "packaging_fingerprint" in keys else None,
            packaging_attribution_status=r["packaging_attribution_status"] if "packaging_attribution_status" in keys else None,
            error_message=r["error_message"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    # --- Review Gate Operations (Stage 12) ---
    def save_review_record(self, record: ReviewRecord) -> None:
        overrides_json = json.dumps(record.media_overrides) if record.media_overrides else None
        origin_val = record.approval_origin.value if hasattr(record.approval_origin, "value") else str(record.approval_origin)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO review_records (id, project_id, operator, action, notes, approved_privacy_status, approval_origin, media_overrides_json, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    operator=excluded.operator,
                    action=excluded.action,
                    notes=excluded.notes,
                    approved_privacy_status=excluded.approved_privacy_status,
                    approval_origin=excluded.approval_origin,
                    media_overrides_json=excluded.media_overrides_json,
                    reviewed_at=excluded.reviewed_at;
                """,
                (
                    record.id,
                    record.project_id,
                    record.operator,
                    record.action.value,
                    record.notes,
                    record.approved_privacy_status.value,
                    origin_val,
                    overrides_json,
                    record.reviewed_at.isoformat(),
                ),
            )

    def get_review_history(self, project_id: str) -> List[ReviewRecord]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM review_records WHERE project_id = ? ORDER BY reviewed_at ASC;",
                (project_id,),
            ).fetchall()
            results = []
            for r in rows:
                keys = r.keys()
                app_origin = (
                    ApprovalOrigin(r["approval_origin"])
                    if "approval_origin" in keys and r["approval_origin"]
                    else ApprovalOrigin.AUTOMATION
                )
                results.append(
                    ReviewRecord(
                        id=r["id"],
                        project_id=r["project_id"],
                        operator=r["operator"],
                        action=ReviewAction(r["action"]),
                        notes=r["notes"],
                        approved_privacy_status=PrivacyStatus(r["approved_privacy_status"]),
                        approval_origin=app_origin,
                        media_overrides=json.loads(r["media_overrides_json"]) if r["media_overrides_json"] else {},
                        reviewed_at=datetime.fromisoformat(r["reviewed_at"]),
                    )
                )
            return results

    def get_latest_review(self, project_id: str) -> Optional[ReviewRecord]:
        history = self.get_review_history(project_id)
        return history[-1] if history else None

    # --- Analytics Snapshot Operations (Stage 14 & 15) ---
    def _row_to_analytics_snapshot(self, r: Any) -> AnalyticsSnapshot:
        keys = r.keys()
        snap_type = r["snapshot_type"] if "snapshot_type" in keys and r["snapshot_type"] else "REAL"
        sim = bool(r["is_simulated"]) if "is_simulated" in keys and r["is_simulated"] is not None else False
        source = r["source"] if "source" in keys and r["source"] else (AnalyticsSource.SIMULATED.value if sim else AnalyticsSource.LEGACY_UNVERIFIED.value)
        retention_curve: List[RetentionPoint] = []
        if "retention_curve_json" in keys and r["retention_curve_json"]:
            try:
                curve_raw = json.loads(r["retention_curve_json"])
                retention_curve = [RetentionPoint(**p) for p in curve_raw]
            except Exception:
                retention_curve = []

        return AnalyticsSnapshot(
            id=r["id"],
            project_id=r["project_id"],
            youtube_video_id=r["youtube_video_id"] if "youtube_video_id" in keys else None,
            source=source,
            snapshot_type=snap_type,
            is_simulated=sim,
            report_start_date=r["report_start_date"] if "report_start_date" in keys else None,
            report_end_date=r["report_end_date"] if "report_end_date" in keys else None,
            views=r["views"],
            watch_time_hours=r["watch_time_hours"],
            average_view_duration_seconds=r["average_view_duration_seconds"],
            average_view_percentage=r["average_view_percentage"] if "average_view_percentage" in keys else None,
            impressions=r["impressions"] if "impressions" in keys else None,
            ctr_percent=r["ctr_percent"] if "ctr_percent" in keys and r["ctr_percent"] is not None else None,
            retention_at_3s_percent=r["retention_at_3s_percent"] if "retention_at_3s_percent" in keys else None,
            retention_curve=retention_curve,
            captured_at=datetime.fromisoformat(r["captured_at"]),
        )

    def save_analytics_snapshot(self, snapshot: AnalyticsSnapshot) -> None:
        retention_curve_json = (
            json.dumps([p.model_dump() for p in snapshot.retention_curve])
            if snapshot.retention_curve
            else None
        )
        source_val = snapshot.source.value if isinstance(snapshot.source, AnalyticsSource) else str(snapshot.source)

        with self._get_connection() as conn:
            existing_id = None
            if snapshot.report_end_date:
                row = conn.execute(
                    "SELECT id FROM analytics_snapshots WHERE project_id = ? AND source = ? AND report_end_date = ?;",
                    (snapshot.project_id, source_val, snapshot.report_end_date),
                ).fetchone()
                if row:
                    existing_id = row["id"]

            target_id = existing_id or snapshot.id

            conn.execute(
                """
                INSERT INTO analytics_snapshots (
                    id, project_id, youtube_video_id, source, snapshot_type, is_simulated,
                    report_start_date, report_end_date, views, watch_time_hours,
                    average_view_duration_seconds, average_view_percentage, impressions,
                    ctr_percent, retention_at_3s_percent, retention_curve_json, captured_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id=excluded.project_id,
                    youtube_video_id=excluded.youtube_video_id,
                    source=excluded.source,
                    snapshot_type=excluded.snapshot_type,
                    is_simulated=excluded.is_simulated,
                    report_start_date=excluded.report_start_date,
                    report_end_date=excluded.report_end_date,
                    views=excluded.views,
                    watch_time_hours=excluded.watch_time_hours,
                    average_view_duration_seconds=excluded.average_view_duration_seconds,
                    average_view_percentage=excluded.average_view_percentage,
                    impressions=excluded.impressions,
                    ctr_percent=excluded.ctr_percent,
                    retention_at_3s_percent=excluded.retention_at_3s_percent,
                    retention_curve_json=excluded.retention_curve_json,
                    captured_at=excluded.captured_at;
                """,
                (
                    target_id,
                    snapshot.project_id,
                    snapshot.youtube_video_id,
                    source_val,
                    snapshot.snapshot_type,
                    1 if snapshot.is_simulated else 0,
                    snapshot.report_start_date,
                    snapshot.report_end_date,
                    snapshot.views,
                    snapshot.watch_time_hours,
                    snapshot.average_view_duration_seconds,
                    snapshot.average_view_percentage,
                    snapshot.impressions,
                    snapshot.ctr_percent,
                    snapshot.retention_at_3s_percent,
                    retention_curve_json,
                    snapshot.captured_at.isoformat(),
                ),
            )

    def get_analytics_snapshots(self, project_id: str) -> List[AnalyticsSnapshot]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM analytics_snapshots WHERE project_id = ? ORDER BY captured_at ASC;",
                (project_id,),
            ).fetchall()
            return [self._row_to_analytics_snapshot(r) for r in rows]

    def get_channel_analytics(self, channel_id: str) -> List[AnalyticsSnapshot]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT a.* FROM analytics_snapshots a
                JOIN video_projects p ON a.project_id = p.id
                WHERE p.channel_id = ?
                ORDER BY a.captured_at ASC;
                """,
                (channel_id,),
            ).fetchall()
            return [self._row_to_analytics_snapshot(r) for r in rows]


    # --- Content Series Operations ---
    def save_content_series(self, series: ContentSeries) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO content_series (
                    id, channel_id, title, description, target_niche, default_format,
                    frequency_per_week, playlist_id, visual_style_preset,
                    next_episode_number, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    series.id,
                    series.channel_id,
                    series.title,
                    series.description,
                    series.target_niche,
                    series.default_format.value if hasattr(series.default_format, "value") else str(series.default_format),
                    series.frequency_per_week,
                    series.playlist_id,
                    series.visual_style_preset,
                    series.next_episode_number,
                    1 if series.is_active else 0,
                    series.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_content_series(self, series_id: str) -> Optional[ContentSeries]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM content_series WHERE id = ?;", (series_id,)).fetchone()
            if not row:
                return None
            return ContentSeries(
                id=row["id"],
                channel_id=row["channel_id"],
                title=row["title"],
                description=row["description"],
                target_niche=row["target_niche"],
                default_format=PlatformFormat(row["default_format"]),
                frequency_per_week=row["frequency_per_week"],
                playlist_id=row["playlist_id"],
                visual_style_preset=row["visual_style_preset"],
                next_episode_number=row["next_episode_number"],
                is_active=bool(row["is_active"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def list_content_series(self, channel_id: str, active_only: bool = True) -> List[ContentSeries]:
        with self._get_connection() as conn:
            query = "SELECT * FROM content_series WHERE channel_id = ?"
            params = [channel_id]
            if active_only:
                query += " AND is_active = 1"
            query += " ORDER BY created_at ASC;"
            rows = conn.execute(query, params).fetchall()
            return [
                ContentSeries(
                    id=r["id"],
                    channel_id=r["channel_id"],
                    title=r["title"],
                    description=r["description"],
                    target_niche=r["target_niche"],
                    default_format=PlatformFormat(r["default_format"]),
                    frequency_per_week=r["frequency_per_week"],
                    playlist_id=r["playlist_id"],
                    visual_style_preset=r["visual_style_preset"],
                    next_episode_number=r["next_episode_number"],
                    is_active=bool(r["is_active"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
                for r in rows
            ]

    def increment_series_episode(self, series_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE content_series SET next_episode_number = next_episode_number + 1 WHERE id = ?;", (series_id,))
            cursor.execute("SELECT next_episode_number FROM content_series WHERE id = ?;", (series_id,))
            row = cursor.fetchone()
            conn.commit()
            return row["next_episode_number"] if row else 1

    # --- Editorial Calendar Operations ---
    def save_editorial_slot(self, slot: EditorialSlot) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO editorial_calendar (
                    id, channel_id, series_id, project_id, slot_time,
                    status, target_topic, episode_number, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    slot.id,
                    slot.channel_id,
                    slot.series_id,
                    slot.project_id,
                    slot.slot_time.isoformat(),
                    slot.status.value if hasattr(slot.status, "value") else str(slot.status),
                    slot.target_topic,
                    slot.episode_number,
                    slot.notes,
                    slot.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_editorial_slot(self, slot_id: str) -> Optional[EditorialSlot]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM editorial_calendar WHERE id = ?;", (slot_id,)).fetchone()
            if not row:
                return None
            return EditorialSlot(
                id=row["id"],
                channel_id=row["channel_id"],
                series_id=row["series_id"],
                project_id=row["project_id"],
                slot_time=datetime.fromisoformat(row["slot_time"]),
                status=EditorialSlotStatus(row["status"]),
                target_topic=row["target_topic"],
                episode_number=row["episode_number"],
                notes=row["notes"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    def list_editorial_slots(
        self,
        channel_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[EditorialSlot]:
        with self._get_connection() as conn:
            query = "SELECT * FROM editorial_calendar WHERE channel_id = ?"
            params: List[Any] = [channel_id]
            if start_time:
                query += " AND slot_time >= ?"
                params.append(start_time.isoformat())
            if end_time:
                query += " AND slot_time <= ?"
                params.append(end_time.isoformat())
            query += " ORDER BY slot_time ASC;"
            rows = conn.execute(query, params).fetchall()
            return [
                EditorialSlot(
                    id=r["id"],
                    channel_id=r["channel_id"],
                    series_id=r["series_id"],
                    project_id=r["project_id"],
                    slot_time=datetime.fromisoformat(r["slot_time"]),
                    status=EditorialSlotStatus(r["status"]),
                    target_topic=r["target_topic"],
                    episode_number=r["episode_number"],
                    notes=r["notes"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                )
                for r in rows
            ]

    def update_editorial_slot_status(
        self, slot_id: str, status: EditorialSlotStatus, project_id: Optional[str] = None
    ) -> None:
        with self._get_connection() as conn:
            if project_id:
                conn.execute(
                    "UPDATE editorial_calendar SET status = ?, project_id = ? WHERE id = ?;",
                    (status.value, project_id, slot_id),
                )
            else:
                conn.execute(
                    "UPDATE editorial_calendar SET status = ? WHERE id = ?;",
                    (status.value, slot_id),
                )
            conn.commit()

    # --- SEO Package Operations ---
    def save_seo_package(self, pkg: SEOPackage) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO seo_packages (
                    id, project_id, primary_keyword, title_variants_json, selected_title,
                    description, chapters_json, tags_json, pinned_comment, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    pkg.id,
                    pkg.project_id,
                    pkg.primary_keyword,
                    json.dumps([v.model_dump() for v in pkg.title_variants]),
                    pkg.selected_title,
                    pkg.description,
                    json.dumps([c.model_dump() for c in pkg.chapters]),
                    json.dumps(pkg.tags),
                    pkg.pinned_comment,
                    pkg.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_seo_package(self, project_id: str) -> Optional[SEOPackage]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM seo_packages WHERE project_id = ?;", (project_id,)).fetchone()
            if not row:
                return None
            return SEOPackage(
                id=row["id"],
                project_id=row["project_id"],
                primary_keyword=row["primary_keyword"],
                title_variants=[TitleVariant(**v) for v in json.loads(row["title_variants_json"])],
                selected_title=row["selected_title"],
                description=row["description"],
                chapters=[Chapter(**c) for c in json.loads(row["chapters_json"])],
                tags=json.loads(row["tags_json"]),
                pinned_comment=row["pinned_comment"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    # --- Thumbnail Operations ---
    def save_thumbnail_package(self, pkg: ThumbnailPackage) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO thumbnails (
                    id, project_id, file_path_16_9, file_path_9_16, headline_text,
                    content_sha256, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    pkg.id,
                    pkg.project_id,
                    pkg.file_path_16_9,
                    pkg.file_path_9_16,
                    pkg.headline_text,
                    pkg.content_sha256,
                    json.dumps(pkg.provenance),
                    pkg.created_at.isoformat(),
                ),
            )
            conn.commit()

    def get_thumbnail_package(self, project_id: str) -> Optional[ThumbnailPackage]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM thumbnails WHERE project_id = ?;", (project_id,)).fetchone()
            if not row:
                return None
            return ThumbnailPackage(
                id=row["id"],
                project_id=row["project_id"],
                file_path_16_9=row["file_path_16_9"],
                file_path_9_16=row["file_path_9_16"],
                headline_text=row["headline_text"],
                content_sha256=row["content_sha256"],
                provenance=json.loads(row["provenance_json"]) if row["provenance_json"] else {},
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    # --- YouTube API Quota Tracking Operations ---
    def save_quota_usage_record(self, record: QuotaUsageRecord) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO quota_usage_records (
                    id, operation, units_consumed, daily_budget,
                    consumed_date, project_id, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.id,
                    record.operation,
                    record.units_consumed,
                    record.daily_budget,
                    record.consumed_date,
                    record.project_id,
                    record.timestamp.isoformat(),
                ),
            )
            conn.commit()

    def get_daily_quota_spent(self, consumed_date: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT SUM(units_consumed) as total_spent FROM quota_usage_records WHERE consumed_date = ?;",
                (consumed_date,),
            ).fetchone()
            return int(row["total_spent"]) if row and row["total_spent"] is not None else 0

    def get_daily_quota_spent_by_bucket(self, consumed_date: str, bucket: str) -> int:
        with self._get_connection() as conn:
            if bucket == "search":
                row = conn.execute(
                    "SELECT SUM(units_consumed) as total_spent FROM quota_usage_records WHERE consumed_date = ? AND operation = 'search.list';",
                    (consumed_date,),
                ).fetchone()
            elif bucket == "upload":
                row = conn.execute(
                    "SELECT SUM(units_consumed) as total_spent FROM quota_usage_records WHERE consumed_date = ? AND operation = 'videos.insert';",
                    (consumed_date,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT SUM(units_consumed) as total_spent FROM quota_usage_records WHERE consumed_date = ? AND operation NOT IN ('search.list', 'videos.insert');",
                    (consumed_date,),
                ).fetchone()
            return int(row["total_spent"]) if row and row["total_spent"] is not None else 0

    def get_quota_history(self, consumed_date: Optional[str] = None) -> List[QuotaUsageRecord]:
        with self._get_connection() as conn:
            if consumed_date:
                rows = conn.execute(
                    "SELECT * FROM quota_usage_records WHERE consumed_date = ? ORDER BY timestamp ASC;",
                    (consumed_date,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM quota_usage_records ORDER BY timestamp ASC;"
                ).fetchall()
            return [
                QuotaUsageRecord(
                    id=r["id"],
                    operation=r["operation"],
                    units_consumed=r["units_consumed"],
                    daily_budget=r["daily_budget"],
                    consumed_date=r["consumed_date"],
                    project_id=r["project_id"],
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                )
                for r in rows
            ]

    # --- Market Signal Snapshot Operations ---
    def save_market_signal_snapshot(self, snapshot: MarketSignalSnapshot) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_signal_snapshots (
                    id, batch_id, channel_id, query, source, collected_at,
                    sample_video_ids_json, sample_size,
                    recent_video_count_7d, recent_video_count_30d, recent_share_30d,
                    median_views, p75_views, median_age_days,
                    median_views_per_day, p75_views_per_day,
                    unique_creator_count, top_creator_share,
                    estimated_result_count, formula_version, confidence,
                    raw_metrics_json, derived_scores_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot.id,
                    snapshot.batch_id,
                    snapshot.channel_id,
                    snapshot.query,
                    snapshot.source,
                    snapshot.collected_at.isoformat(),
                    json.dumps(snapshot.sample_video_ids),
                    snapshot.sample_size,
                    snapshot.recent_video_count_7d,
                    snapshot.recent_video_count_30d,
                    snapshot.recent_share_30d,
                    snapshot.median_views,
                    snapshot.p75_views,
                    snapshot.median_age_days,
                    snapshot.median_views_per_day,
                    snapshot.p75_views_per_day,
                    snapshot.unique_creator_count,
                    snapshot.top_creator_share,
                    snapshot.estimated_result_count,
                    snapshot.formula_version,
                    snapshot.confidence,
                    json.dumps(snapshot.raw_metrics),
                    json.dumps(snapshot.derived_scores),
                    snapshot.created_at.isoformat(),
                ),
            )
            conn.commit()

    def _row_to_market_signal_snapshot(self, row: sqlite3.Row) -> MarketSignalSnapshot:
        return MarketSignalSnapshot(
            id=row["id"],
            batch_id=row["batch_id"],
            channel_id=row["channel_id"],
            query=row["query"],
            source=row["source"],
            collected_at=datetime.fromisoformat(row["collected_at"]),
            sample_video_ids=json.loads(row["sample_video_ids_json"]) if row["sample_video_ids_json"] else [],
            sample_size=row["sample_size"],
            recent_video_count_7d=row["recent_video_count_7d"],
            recent_video_count_30d=row["recent_video_count_30d"],
            recent_share_30d=row["recent_share_30d"],
            median_views=row["median_views"],
            p75_views=row["p75_views"],
            median_age_days=row["median_age_days"],
            median_views_per_day=row["median_views_per_day"],
            p75_views_per_day=row["p75_views_per_day"],
            unique_creator_count=row["unique_creator_count"],
            top_creator_share=row["top_creator_share"],
            estimated_result_count=row["estimated_result_count"],
            formula_version=row["formula_version"],
            confidence=row["confidence"],
            raw_metrics=json.loads(row["raw_metrics_json"]) if row["raw_metrics_json"] else {},
            derived_scores=json.loads(row["derived_scores_json"]) if row["derived_scores_json"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_market_signal_snapshot(self, snapshot_id: str) -> Optional[MarketSignalSnapshot]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM market_signal_snapshots WHERE id = ?;",
                (snapshot_id,),
            ).fetchone()
            return self._row_to_market_signal_snapshot(row) if row else None

    def get_market_signals_for_batch(self, batch_id: str) -> List[MarketSignalSnapshot]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM market_signal_snapshots WHERE batch_id = ? ORDER BY created_at ASC;",
                (batch_id,),
            ).fetchall()
            return [self._row_to_market_signal_snapshot(r) for r in rows]

    def get_latest_market_signal(
        self, channel_id: str, query: str, ttl_hours: float = 24.0
    ) -> Optional[MarketSignalSnapshot]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_signal_snapshots
                WHERE channel_id = ? AND query = ?
                ORDER BY collected_at DESC LIMIT 1;
                """,
                (channel_id, query),
            ).fetchone()
            if not row:
                return None
            snapshot = self._row_to_market_signal_snapshot(row)
            age_seconds = (datetime.now(timezone.utc) - snapshot.collected_at).total_seconds()
            if age_seconds <= (ttl_hours * 3600.0):
                return snapshot
            return None

    # --- Opportunity Portfolio Operations ---
    def save_opportunity_portfolio(self, portfolio: OpportunityPortfolio) -> None:
        signal_ids: List[str] = []
        for c in portfolio.candidates:
            if c.market_signal_id and c.market_signal_id not in signal_ids:
                signal_ids.append(c.market_signal_id)

        candidates_json = json.dumps([c.model_dump(mode="json") for c in portfolio.candidates])
        selected_json = (
            json.dumps(portfolio.selected_topic.model_dump(mode="json"))
            if portfolio.selected_topic
            else None
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        gen_iso = portfolio.generated_at.isoformat() if portfolio.generated_at else now_iso

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO opportunity_portfolios (
                    batch_id, channel_id, generated_at, candidates_json,
                    selected_topic_json, selection_reason, market_signal_ids_json,
                    formula_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    portfolio.batch_id,
                    portfolio.channel_id,
                    gen_iso,
                    candidates_json,
                    selected_json,
                    portfolio.selection_reason,
                    json.dumps(signal_ids),
                    "v1.0",
                    now_iso,
                ),
            )
            conn.commit()

    def _row_to_opportunity_portfolio(self, row: sqlite3.Row) -> OpportunityPortfolio:
        candidates_raw = json.loads(row["candidates_json"]) if row["candidates_json"] else []
        candidates = [TopicOpportunity.model_validate(c) for c in candidates_raw]
        selected_raw = json.loads(row["selected_topic_json"]) if row["selected_topic_json"] else None
        selected_topic = TopicOpportunity.model_validate(selected_raw) if selected_raw else None
        return OpportunityPortfolio(
            batch_id=row["batch_id"],
            channel_id=row["channel_id"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            candidates=candidates,
            selected_topic=selected_topic,
            selection_reason=row["selection_reason"],
        )

    def get_opportunity_portfolio(self, batch_id: str) -> Optional[OpportunityPortfolio]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM opportunity_portfolios WHERE batch_id = ?;",
                (batch_id,),
            ).fetchone()
            return self._row_to_opportunity_portfolio(row) if row else None

    def get_latest_opportunity_portfolio(self, channel_id: str) -> Optional[OpportunityPortfolio]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM opportunity_portfolios
                WHERE channel_id = ?
                ORDER BY generated_at DESC LIMIT 1;
                """,
                (channel_id,),
            ).fetchone()
            return self._row_to_opportunity_portfolio(row) if row else None

    def get_portfolio_market_signal_ids(self, batch_id: str) -> List[str]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT market_signal_ids_json FROM opportunity_portfolios WHERE batch_id = ?;",
                (batch_id,),
            ).fetchone()
            if row and row["market_signal_ids_json"]:
                return json.loads(row["market_signal_ids_json"])
            return []

    # --- Packaging Tournament Operations ---
    def save_packaging_tournament(self, tournament: PackagingTournament) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO packaging_tournaments (
                    id, project_id, created_at, candidates_json, selected_candidate_id,
                    selection_reason, native_ab_eligible, scoring_version, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    tournament.id,
                    tournament.project_id,
                    tournament.created_at.isoformat(),
                    json.dumps([c.model_dump() for c in tournament.candidates]),
                    tournament.selected_candidate_id,
                    tournament.selection_reason,
                    1 if tournament.native_ab_eligible else 0,
                    tournament.scoring_version,
                    tournament.status.value if hasattr(tournament.status, "value") else str(tournament.status),
                ),
            )
            conn.commit()

    def get_packaging_tournament(self, project_id: str) -> Optional[PackagingTournament]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM packaging_tournaments WHERE project_id = ? ORDER BY created_at DESC LIMIT 1;",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_packaging_tournament(row)

    def get_packaging_tournament_by_id(self, tournament_id: str) -> Optional[PackagingTournament]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM packaging_tournaments WHERE id = ?;",
                (tournament_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_packaging_tournament(row)

    def _row_to_packaging_tournament(self, row: sqlite3.Row) -> PackagingTournament:
        raw_candidates = json.loads(row["candidates_json"])
        candidates = [PackagingCandidate(**c) for c in raw_candidates]
        return PackagingTournament(
            id=row["id"],
            project_id=row["project_id"],
            candidates=candidates,
            selected_candidate_id=row["selected_candidate_id"],
            selection_reason=row["selection_reason"],
            native_ab_eligible=bool(row["native_ab_eligible"]),
            scoring_version=row["scoring_version"],
            status=PackagingTournamentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


    # --- YouTube Reporting & Reach Operations (Phase 1) ---
    def save_reporting_job(self, job_state: ReportingJobState) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO reporting_jobs (
                    job_id, report_type_id, remote_name, channel_id,
                    last_processed_create_time, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    report_type_id=excluded.report_type_id,
                    remote_name=excluded.remote_name,
                    channel_id=excluded.channel_id,
                    last_processed_create_time=excluded.last_processed_create_time,
                    updated_at=excluded.updated_at;
                """,
                (
                    job_state.job_id,
                    job_state.report_type_id,
                    job_state.remote_name,
                    job_state.channel_id,
                    job_state.last_processed_create_time.isoformat() if job_state.last_processed_create_time else None,
                    job_state.created_at.isoformat(),
                    job_state.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def get_reporting_job(self, report_type_id: str, channel_id: Optional[str] = None) -> Optional[ReportingJobState]:
        with self._get_connection() as conn:
            if channel_id:
                row = conn.execute(
                    "SELECT * FROM reporting_jobs WHERE report_type_id = ? AND channel_id = ? LIMIT 1;",
                    (report_type_id, channel_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM reporting_jobs WHERE report_type_id = ? LIMIT 1;",
                    (report_type_id,),
                ).fetchone()
            if not row:
                return None
            return self._row_to_reporting_job(row)

    def get_reporting_job_by_id(self, job_id: str) -> Optional[ReportingJobState]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM reporting_jobs WHERE job_id = ? LIMIT 1;",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_reporting_job(row)

    def _row_to_reporting_job(self, r: sqlite3.Row) -> ReportingJobState:
        return ReportingJobState(
            job_id=r["job_id"],
            report_type_id=r["report_type_id"],
            remote_name=r["remote_name"],
            channel_id=r["channel_id"],
            last_processed_create_time=datetime.fromisoformat(r["last_processed_create_time"]) if r["last_processed_create_time"] else None,
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    def save_report_receipt(self, receipt: ReportingReportReceipt) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO reporting_report_receipts (
                    report_id, job_id, report_type_id, start_time, end_time,
                    create_time, content_sha256, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    receipt.report_id,
                    receipt.job_id,
                    receipt.report_type_id,
                    receipt.start_time.isoformat(),
                    receipt.end_time.isoformat(),
                    receipt.create_time.isoformat(),
                    receipt.content_sha256,
                    receipt.processed_at.isoformat(),
                ),
            )
            conn.commit()

    def get_report_receipt(self, report_id: str) -> Optional[ReportingReportReceipt]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM reporting_report_receipts WHERE report_id = ? LIMIT 1;",
                (report_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_report_receipt(row)

    def list_report_receipts(self, job_id: Optional[str] = None) -> List[ReportingReportReceipt]:
        with self._get_connection() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM reporting_report_receipts WHERE job_id = ? ORDER BY create_time ASC;",
                    (job_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reporting_report_receipts ORDER BY create_time ASC;"
                ).fetchall()
            return [self._row_to_report_receipt(r) for r in rows]

    def _row_to_report_receipt(self, r: sqlite3.Row) -> ReportingReportReceipt:
        return ReportingReportReceipt(
            report_id=r["report_id"],
            job_id=r["job_id"],
            report_type_id=r["report_type_id"],
            start_time=datetime.fromisoformat(r["start_time"]),
            end_time=datetime.fromisoformat(r["end_time"]),
            create_time=datetime.fromisoformat(r["create_time"]),
            content_sha256=r["content_sha256"],
            processed_at=datetime.fromisoformat(r["processed_at"]),
        )

    def save_reach_observation(self, observation: ReachObservation) -> bool:
        """
        Persist a ReachObservation with backfill replacement semantics.
        If an observation for (publication_job_id, report_date) already exists,
        it is ONLY overwritten if incoming.source_report_create_time > existing.source_report_create_time.
        Returns True if inserted or updated, False if skipped as older or equal.
        """
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id, source_report_create_time FROM reach_observations WHERE publication_job_id = ? AND report_date = ?;",
                (observation.publication_job_id, observation.report_date),
            ).fetchone()

            if existing:
                existing_create_time = datetime.fromisoformat(existing["source_report_create_time"])
                inc_time = observation.source_report_create_time
                if inc_time.tzinfo is None and existing_create_time.tzinfo is not None:
                    inc_time = inc_time.replace(tzinfo=timezone.utc)
                elif inc_time.tzinfo is not None and existing_create_time.tzinfo is None:
                    existing_create_time = existing_create_time.replace(tzinfo=timezone.utc)

                if inc_time <= existing_create_time:
                    # Older or duplicate report: do NOT overwrite newer observation
                    return False

                # Newer report: update existing observation
                conn.execute(
                    """
                    UPDATE reach_observations SET
                        thumbnail_impressions = ?,
                        thumbnail_impressions_ctr = ?,
                        source_report_id = ?,
                        source_report_create_time = ?,
                        source = ?,
                        created_at = ?
                    WHERE id = ?;
                    """,
                    (
                        observation.thumbnail_impressions,
                        observation.thumbnail_impressions_ctr,
                        observation.source_report_id,
                        observation.source_report_create_time.isoformat(),
                        observation.source.value if hasattr(observation.source, "value") else str(observation.source),
                        observation.collected_at.isoformat(),
                        existing["id"],
                    ),
                )
                conn.commit()
                return True

            # Insert new observation
            conn.execute(
                """
                INSERT INTO reach_observations (
                    id, project_id, publication_job_id, youtube_video_id, report_date,
                    thumbnail_impressions, thumbnail_impressions_ctr,
                    source_report_id, source_report_create_time, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    observation.id,
                    observation.project_id,
                    observation.publication_job_id,
                    observation.youtube_video_id,
                    observation.report_date,
                    observation.thumbnail_impressions,
                    observation.thumbnail_impressions_ctr,
                    observation.source_report_id,
                    observation.source_report_create_time.isoformat(),
                    observation.source.value if hasattr(observation.source, "value") else str(observation.source),
                    observation.collected_at.isoformat(),
                ),
            )
            conn.commit()
            return True

    def get_reach_observations_by_project(self, project_id: str) -> List[ReachObservation]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reach_observations WHERE project_id = ? ORDER BY report_date ASC;",
                (project_id,),
            ).fetchall()
            return [self._row_to_reach_observation(r) for r in rows]

    def get_reach_observations_by_publication_job(self, publication_job_id: str) -> List[ReachObservation]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reach_observations WHERE publication_job_id = ? ORDER BY report_date ASC;",
                (publication_job_id,),
            ).fetchall()
            return [self._row_to_reach_observation(r) for r in rows]

    def get_reach_observations_by_video_id(self, youtube_video_id: str) -> List[ReachObservation]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reach_observations WHERE youtube_video_id = ? ORDER BY report_date ASC;",
                (youtube_video_id,),
            ).fetchall()
            return [self._row_to_reach_observation(r) for r in rows]

    def _row_to_reach_observation(self, r: sqlite3.Row) -> ReachObservation:
        return ReachObservation(
            id=r["id"],
            project_id=r["project_id"],
            publication_job_id=r["publication_job_id"],
            youtube_video_id=r["youtube_video_id"],
            report_date=r["report_date"],
            thumbnail_impressions=r["thumbnail_impressions"],
            thumbnail_impressions_ctr=r["thumbnail_impressions_ctr"],
            source_report_id=r["source_report_id"],
            source_report_create_time=datetime.fromisoformat(r["source_report_create_time"]),
            source=AnalyticsSource(r["source"]) if "source" in r.keys() and r["source"] else AnalyticsSource.LEGACY_UNVERIFIED,
            collected_at=datetime.fromisoformat(r["created_at"]),
        )


Repository = SQLiteRepository
