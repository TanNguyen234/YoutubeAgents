from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import (
    ApprovalOrigin,
    AssetType,
    ContentFormat,
    EditorialSlotStatus,
    PrivacyStatus,
    QualityStatus,
    ReviewAction,
    VideoLifecycleState,
)
from app.domain.models import (
    AnalyticsSnapshot,
    Channel,
    ContentSeries,
    EditorialSlot,
    FactCheckReport,
    PublicationJob,
    ResearchDossier,
    ReviewRecord,
    SEOPackage,
    ThumbnailPackage,
    TopicCandidate,
    VideoProject,
)
from app.media.gflow_provider import GFlowMediaProvider
from app.media.models import MediaQAResult, RenderManifest
from app.media.pipeline import MediaProductionPipeline
from app.media.scene_planner import ScenePlanner
from app.services.analytics_tracker import YouTubeAnalyticsTracker
from app.services.claim_extractor import ClaimExtractor
from app.services.editorial_calendar import EditorialCalendarService
from app.services.fact_checker import FactChecker
from app.services.quota_manager import QuotaBudgetManager
from app.services.research_agent import ResearchAgent, ResearchFetchError
from app.services.review_gate import HumanReviewGateService
from app.services.script_generator import ScriptGenerator
from app.services.script_writer import ScriptWriter
from app.services.seo_optimizer import SEOOptimizerService
from app.services.strategy_feedback import StrategyFeedbackLoop
from app.services.thumbnail_designer import ThumbnailDesignerService
from app.services.topic_evaluator import TopicEvaluator
from app.services.topic_strategist import TopicStrategist
from app.services.youtube_publisher import YouTubePublisherService


class BrainPipeline:
    """End-to-end intelligence pipeline executing Stages 1-5 with strict grounding and checkpoint persistence."""

    def __init__(
        self,
        repo: Optional[SQLiteRepository] = None,
        repository: Optional[SQLiteRepository] = None,
        backend: Optional[ReasoningBackend] = None,
        research_agent: Optional[ResearchAgent] = None,
        strategist: Optional[TopicStrategist] = None,
        evaluator: Optional[TopicEvaluator] = None,
        generator: Optional[ScriptGenerator] = None,
        writer: Optional[ScriptWriter] = None,
        extractor: Optional[ClaimExtractor] = None,
        checker: Optional[FactChecker] = None,
    ):
        self.repo = repo or repository
        if not self.repo:
            raise ValueError("SQLiteRepository instance is required for BrainPipeline.")
        self.backend = backend or AntigravityCLIBackend()
        self.research_agent = research_agent or ResearchAgent()
        self.strategist = strategist or TopicStrategist()
        self.evaluator = evaluator or TopicEvaluator(backend=self.backend)
        self.generator = generator or ScriptGenerator(backend=self.backend)
        self.writer = writer or ScriptWriter()
        self.extractor = extractor or ClaimExtractor(backend=self.backend)
        self.checker = checker or FactChecker(backend=self.backend)

    def run_stage_1_to_5(
        self,
        project_id: str,
        channel: Channel,
        keyword: str,
        seed_urls: List[str],
        recent_topics: Optional[List[str]] = None,
        max_rewrite_attempts: int = 2,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        series_continuity: Optional[Dict[str, Any]] = None,
    ) -> Tuple[VideoProject, FactCheckReport]:
        """Execute Stage 1 (Topic Selection) -> Stage 2 (Research) -> Stage 3 (Script) -> Stage 4 (Fact Check) -> Stage 5 (Verification Gate)."""
        recent = recent_topics or []

        # 1. Initialize or load project (must start in CREATED)
        project = self.repo.get_video_project(project_id)
        if not project:
            project = VideoProject(
                id=project_id,
                channel_id=channel.id,
                title=keyword,
                state=VideoLifecycleState.CREATED,
                content_format=content_format,
            )
            self.repo.save_video_project(project)
        else:
            project.content_format = content_format

        # 2. Stage 1: Deterministic Duplicate Check BEFORE network research
        is_dup, dup_score, matched = self.strategist.duplicate_detector.check_duplicate(keyword, recent)
        if is_dup:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Duplicate topic detected: matches '{matched}' with similarity {dup_score:.2f}",
            )
            raise ValueError(f"Duplicate topic detected: candidate '{keyword}' conflicts with '{matched}'")

        # 3. Transition: CREATED -> RESEARCHING
        self.repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.RESEARCHING,
            reason="Starting live evidence collection from seed sources",
        )

        # 4. Stage 2: Live Network Research & Dossier Compilation
        try:
            dossier = self.research_agent.build_dossier_from_urls(
                urls=seed_urls,
                topic_id=f"top-{project_id}",
                summary_prompt=f"Comprehensive research summary on '{keyword}' for channel {channel.title}",
            )
            # Durable Checkpoint: Save research dossier
            self.repo.save_research_dossier(project_id, dossier)
        except ResearchFetchError as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.BLOCKED,
                reason=f"Live research fetch failed: {str(e)}",
            )
            raise
        except Exception as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Research dossier creation failed: {str(e)}",
            )
            raise

        # 5. Stage 3: Evidence-Aware Topic Evaluation & Strategy Scoring
        try:
            # Build compact real evidence context from downloaded sources
            evidence_context = "\n\n".join(
                f"[{s.title}] {s.final_url or s.url}\n{(s.content_snapshot or '')[:2000]}"
                for s in dossier.sources
            )

            scores_dict, rationale, score_reasons = self.evaluator.evaluate_topic_with_reasoning(
                channel=channel,
                keyword=keyword,
                evidence_summary=evidence_context,
            )

            candidate = self.strategist.evaluate_candidate(
                topic_id=f"top-{project_id}",
                channel=channel,
                keyword=keyword,
                raw_scores=scores_dict,
                rationale=rationale,
                score_reasons=score_reasons,
                recent_channel_topics=recent,
            )
            # Durable Checkpoint: Save topic candidate
            self.repo.save_topic_candidate(candidate)

            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.PLANNED,
                reason=f"Evidence-aware topic evaluation completed with composite score {candidate.opportunity_score:.2f}",
            )
        except Exception as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Topic evaluation failed: {str(e)}",
            )
            raise

        # 6. Stage 4: Script Generation -> transition to SCRIPTED
        try:
            sections = self.generator.generate_script_sections(
                channel=channel,
                keyword=keyword,
                dossier=dossier,
                content_format=content_format,
                series_continuity=series_continuity,
            )
            script = self.writer.build_script(
                script_id=f"scr-{project_id}",
                title=keyword,
                sections=sections,
                content_format=content_format,
            )
            project.script = script
            project.content_format = content_format
            # Durable Checkpoint: Save script & video project
            self.repo.save_video_project(project)

            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.SCRIPTED,
                reason="Script generated with structured scene segments",
            )
        except Exception as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Script generation failed: {str(e)}",
            )
            raise

        # 7. Stage 5: Claim Extraction & Fact Checking Rewrite Loop
        try:
            for attempt in range(max_rewrite_attempts + 1):
                extracted_claims = self.extractor.extract_from_script(project.script)
                dossier.claims = extracted_claims

                report = self.checker.verify_all_claims(
                    claims=extracted_claims,
                    dossier=dossier,
                    project_id=project_id,
                )

                # Durable Checkpoint: Save fact check report after each audit pass
                self.repo.save_fact_check_report(report)

                if report.overall_verdict == QualityStatus.PASSED:
                    break

                rewrite_needed = any(c.verdict.value in ["REWRITE_REQUIRED", "REMOVE", "UNVERIFIABLE"] for c in report.claims)
                if rewrite_needed and attempt < max_rewrite_attempts:
                    flagged = [c for c in report.claims if c.verdict.value != "VERIFIED"]
                    revised_sections = self.generator.rewrite_script_sections(
                        channel=channel,
                        original_sections=project.script.sections,
                        flagged_claims=flagged,
                        dossier=dossier,
                    )
                    revised_script = self.writer.build_script(
                        script_id=f"scr-{project_id}-v{attempt+2}",
                        title=keyword,
                        sections=revised_sections,
                        content_format=content_format,
                    )
                    project.script = revised_script
                    project.content_format = content_format
                    self.repo.save_video_project(project)
                else:
                    break

            # 8. Stage 6: Authoritative Verification Gate
            if report.overall_verdict == QualityStatus.PASSED and report.failed_count == 0 and len(report.claims) > 0:
                self.repo.update_project_state(
                    project_id=project_id,
                    to_state=VideoLifecycleState.VERIFIED,
                    reason=f"All {report.verified_count} factual claims verified against source evidence",
                )
            else:
                self.repo.update_project_state(
                    project_id=project_id,
                    to_state=VideoLifecycleState.FAILED,
                    reason=f"Fact check verification failed with {report.failed_count} unverified claim(s)",
                )
        except Exception as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Stage 5 fact-check/extraction failure: {str(e)}",
            )
            raise

        updated_project = self.repo.get_video_project(project_id) or project
        return updated_project, report

    def run_full_autonomous_lifecycle(
        self,
        project_id: str,
        channel: Channel,
        keyword: str,
        seed_urls: List[str],
        recent_topics: Optional[List[str]] = None,
        series_id: Optional[str] = None,
        series_title: Optional[str] = None,
        slot_id: Optional[str] = None,
        enable_gflow: bool = True,
        auto_approve: bool = True,
        approved_privacy: PrivacyStatus = PrivacyStatus.PRIVATE,
        operator_name: str = "AutonomousOperator",
        approval_origin: ApprovalOrigin = ApprovalOrigin.AUTOMATION,
        allow_autonomous_public: bool = False,
        voice: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        scheduled_time: Optional[datetime] = None,
        simulate_analytics_views: Optional[int] = None,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
    ) -> Dict[str, Any]:
        """Execute the complete 15-stage YouTube Autopilot lifecycle from Topic Selection to Strategy Feedback."""
        # 0. Editorial Series & Calendar Context
        cal_service = EditorialCalendarService(self.repo)
        series = None
        if series_id:
            series = cal_service.get_series(series_id)
        elif series_title:
            series = cal_service.register_series(
                channel_id=channel.id,
                title=series_title,
                target_niche=channel.niche,
            )
        continuity = None
        if series:
            continuity = cal_service.get_episodic_continuity_context(
                series.id, series.next_episode_number
            )
        # 1. Stages 1-5: Research, Topic Evaluation, Scriptwriting, Fact Checking
        project, fact_report = self.run_stage_1_to_5(
            project_id=project_id,
            channel=channel,
            keyword=keyword,
            seed_urls=seed_urls,
            recent_topics=recent_topics,
            series_continuity=continuity,
            content_format=content_format,
        )

        if slot_id:
            cal_service.book_slot(slot_id, project_id)

        # 2. Stages 6-10: Multimodal Production (MediaProductionPipeline with AutoDirector)
        # Canonical GFlow provider passed cleanly
        gflow_prov = GFlowMediaProvider() if enable_gflow else None
        scene_planner = ScenePlanner(gflow_provider=gflow_prov)
        media_pipeline = MediaProductionPipeline(
            repository=self.repo,
            scene_planner=scene_planner,
            gflow_provider=gflow_prov,
            reasoning_backend=self.backend,
        )

        project, qa_result, render_manifest = media_pipeline.run_production(
            project_id=project_id,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        project = self.repo.get_video_project(project_id) or project

        # 4. Stage 11.5: High-Impact Thumbnail Generation & SEO Packaging
        seo_service = SEOOptimizerService(self.repo)
        dossier = self.repo.get_research_dossier(project_id)
        sources_summary = "\n".join(f"- {s.title}: {s.url}" for s in dossier.sources) if dossier else ""
        seo_pkg = seo_service.generate_and_save_seo_package(
            project_id=project_id,
            primary_keyword=keyword,
            series_context=continuity,
            sources_summary=sources_summary,
        )

        thumb_output_dir = Path("output/projects") / project_id / "thumbnails"
        thumb_service = ThumbnailDesignerService(self.repo, thumb_output_dir)
        bg_asset = None
        for a in project.assets:
            if a.asset_type in (AssetType.IMAGE, AssetType.SCENE_CARD) and Path(a.file_path).exists():
                bg_asset = Path(a.file_path)
                break

        headline_words = " ".join(keyword.split()[:4]).upper()
        thumb_pkg = thumb_service.create_thumbnail_package(
            project_id=project_id,
            headline_text=headline_words,
            background_image_path=str(bg_asset) if bg_asset else None,
            series_badge=continuity.get("display_badge") if continuity else None,
        )

        # 5. Stage 12: Human Review Gate
        review_service = HumanReviewGateService(self.repo)
        review_record = None
        if auto_approve:
            review_record = review_service.submit_review(
                project_id=project_id,
                operator=operator_name,
                action=ReviewAction.APPROVE,
                notes="Automated lifecycle verification approval.",
                approved_privacy_status=approved_privacy,
                approval_origin=approval_origin,
                allow_autonomous_public=allow_autonomous_public,
            )
            project = self.repo.get_video_project(project_id) or project

        # 6. Stage 12.5: YouTube API Quota Pre-flight & Budget Check
        quota_mgr = QuotaBudgetManager(self.repo)
        quota_mgr.ensure_budget("videos.insert")
        quota_mgr.ensure_budget("thumbnails.set")

        # 7. Stage 13: YouTube Upload and Scheduling
        publisher = YouTubePublisherService(self.repo)
        pub_job = None
        pub_mode = "NOT_RUN"
        if project.state == VideoLifecycleState.APPROVED:
            pub_job, pub_mode = publisher.publish_project(
                project_id=project_id,
                scheduled_time=scheduled_time,
                enforce_approved_privacy=True,
            )
            project = self.repo.get_video_project(project_id) or project
            # Only record spent quota units if the API was actually called (REAL mode)
            if pub_mode == "REAL":
                quota_mgr.record_spend("videos.insert", project_id=project_id)
                quota_mgr.record_spend("thumbnails.set", project_id=project_id)

            if slot_id:
                slot_stat = (
                    EditorialSlotStatus.PUBLISHED
                    if project.state == VideoLifecycleState.PUBLISHED
                    else (EditorialSlotStatus.SCHEDULED if project.state == VideoLifecycleState.SCHEDULED else EditorialSlotStatus.PLANNED)
                )
                self.repo.update_editorial_slot_status(
                    slot_id=slot_id,
                    status=slot_stat,
                    project_id=project_id,
                )

        # 8. Stage 14: YouTube Analytics Tracking
        analytics_tracker = YouTubeAnalyticsTracker(self.repo)
        analytics_snapshot = None
        # Never record analytics for BLOCKED projects. Only track for PUBLISHED or SCHEDULED.
        if project.state in (VideoLifecycleState.PUBLISHED, VideoLifecycleState.SCHEDULED):
            yt_id = pub_job.youtube_video_id if pub_job else None
            if not yt_id:
                jobs = self.repo.get_publication_queue()
                for j in jobs:
                    if j.project_id == project_id and j.youtube_video_id:
                        yt_id = j.youtube_video_id
                        break

            if simulate_analytics_views is not None:
                # Explicitly requested simulation
                analytics_snapshot = analytics_tracker.record_snapshot(
                    project_id=project_id,
                    views=simulate_analytics_views,
                    watch_time_hours=round(simulate_analytics_views * 0.035, 2),
                    ctr_percent=7.8 if simulate_analytics_views > 0 else 0.0,
                    average_view_duration_seconds=round(project.quality.duration_seconds * 0.65, 1) if project.quality else 20.0,
                    retention_at_3s_percent=68.5 if simulate_analytics_views > 0 else 0.0,
                    youtube_video_id=yt_id,
                    is_simulated=True,
                )
            elif yt_id and not yt_id.startswith("yt-dryrun-"):
                # Initial production baseline capture with real YouTube video ID
                analytics_snapshot = analytics_tracker.record_snapshot(
                    project_id=project_id,
                    views=0,
                    watch_time_hours=0.0,
                    ctr_percent=0.0,
                    average_view_duration_seconds=0.0,
                    retention_at_3s_percent=None,
                    youtube_video_id=yt_id,
                    is_simulated=False,
                )
            else:
                # No verified YouTube video ID yet; do not invent fake analytics
                analytics_snapshot = None
        elif project.state == VideoLifecycleState.BLOCKED:
            # Cleanly skip analytics on BLOCKED projects
            analytics_snapshot = None

        # 9. Stage 15: Strategy Feedback Loop
        strategy_feedback = StrategyFeedbackLoop(self.repo)
        strategy_analysis = strategy_feedback.analyze_channel_performance(channel.id)

        return {
            "project_id": project.id,
            "final_state": project.state.value,
            "channel_title": channel.title,
            "keyword": keyword,
            "editorial_continuity": continuity,
            "fact_report": {
                "verified_count": fact_report.verified_count,
                "failed_count": fact_report.failed_count,
                "overall_verdict": fact_report.overall_verdict.value,
            },
            "qa_result": {
                "status": "PASSED" if qa_result.passed else "FAILED",
                "loudness_lufs": qa_result.loudness_lufs,
                "duration_seconds": qa_result.video_duration,
                "issues": qa_result.issues,
            },
            "render_manifest": {
                "final_video_path": str(render_manifest.final_video_path),
                "final_video_sha256": render_manifest.final_video_sha256,
                "production_fingerprint": render_manifest.production_fingerprint,
                "scene_count": render_manifest.scene_count,
            },
            "seo_package": {
                "selected_title": seo_pkg.selected_title,
                "title_variants_count": len(seo_pkg.title_variants),
                "chapters_count": len(seo_pkg.chapters),
                "tags_count": len(seo_pkg.tags),
                "pinned_comment": seo_pkg.pinned_comment,
            },
            "thumbnail_package": {
                "headline_text": thumb_pkg.headline_text,
                "file_path_16_9": thumb_pkg.file_path_16_9,
                "file_path_9_16": thumb_pkg.file_path_9_16,
                "sha256": thumb_pkg.content_sha256,
            },
            "review_record": {
                "operator": review_record.operator if review_record else None,
                "action": review_record.action.value if review_record else None,
                "privacy": review_record.approved_privacy_status.value if review_record else None,
            } if review_record else None,
            "quota_status": {
                "daily_limit": quota_mgr.daily_limit,
                "spent_today": quota_mgr.get_spent_units(),
                "remaining_today": quota_mgr.get_remaining_units(),
            },
            "publication_job": {
                "job_id": pub_job.id if pub_job else None,
                "status": pub_job.status.value if pub_job else None,
                "youtube_video_id": pub_job.youtube_video_id if pub_job else None,
                "mode": pub_mode,
            } if pub_job else None,
            "analytics_snapshot": {
                "views": analytics_snapshot.views if analytics_snapshot else None,
                "watch_time_hours": analytics_snapshot.watch_time_hours if analytics_snapshot else None,
                "ctr_percent": analytics_snapshot.ctr_percent if analytics_snapshot else None,
            } if analytics_snapshot else None,
            "strategy_feedback": {
                "total_snapshots": strategy_analysis.get("total_snapshots", 0),
                "mean_views": strategy_analysis.get("mean_views", 0.0),
                "recommendations": strategy_analysis.get("recommendations", []),
            },
        }

