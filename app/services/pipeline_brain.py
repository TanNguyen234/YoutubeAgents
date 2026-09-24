from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

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
    AnalyticsCollectionResult,
    AnalyticsSnapshot,
    Channel,
    ContentSeries,
    EditorialSlot,
    FactCheckReport,
    OpportunityPortfolio,
    PackagingReachFeedback,
    PublicationJob,
    ReachSyncResult,
    ResearchDossier,
    ReviewRecord,
    ScriptSections,
    SEOPackage,
    ThumbnailPackage,
    TopicCandidate,
    TopicOpportunity,
    VideoCreativeBrief,
    VideoProject,
)
from app.media.gflow_provider import GFlowMediaProvider
from app.media.models import MediaQAResult, RenderManifest
from app.media.pipeline import MediaProductionPipeline
from app.media.scene_planner import ScenePlanner
from app.media.tts.base import TTSBackend
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
from app.domain.models import resolve_default_creative_brief
from app.services.hook_strategy import HookTournamentService
from app.services.opportunity_engine import OpportunityEngine
from app.services.packaging_engine import PackagingEngineService
from app.services.retention_planner import RetentionPlanner

from app.services.script_retention import ScriptRetentionEvaluator
from app.services.strategy_feedback import StrategyFeedbackLoop
from app.services.thumbnail_designer import ThumbnailDesignerService
from app.services.topic_evaluator import TopicEvaluator
from app.services.topic_strategist import TopicStrategist
from app.services.packaging_feedback import compute_packaging_reach_feedback
from app.services.youtube_analytics_ingestion import YouTubeAnalyticsIngestionService
from app.services.youtube_oauth import YouTubeOAuthManager
from app.services.youtube_publisher import YouTubePublisherService
from app.services.youtube_reach_reporting import YouTubeReachReportingService


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
        tts_backend: Optional[TTSBackend] = None,
        opportunity_engine: Optional[OpportunityEngine] = None,
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
        self.tts_backend = tts_backend
        self.opportunity_engine = opportunity_engine or OpportunityEngine(
            repository=self.repo,
            strategist=self.strategist,
            evaluator=self.evaluator,
            backend=self.backend,
        )
        self.last_retention_report: Optional[Any] = None

    def discover_opportunities(
        self,
        channel: Channel,
        seed_queries: Optional[List[str]] = None,
        recent_topics: Optional[List[str]] = None,
        max_candidates: int = 5,
        force_refresh: bool = False,
    ) -> OpportunityPortfolio:
        """Stage 0 / Autonomous Discovery: Discover and rank candidate topic opportunities using real market evidence."""
        return self.opportunity_engine.discover_opportunities(
            channel=channel,
            seed_queries=seed_queries,
            recent_topics=recent_topics,
            max_candidates=max_candidates,
            force_refresh=force_refresh,
        )

    def select_topic_opportunity(
        self,
        channel: Channel,
        seed_queries: Optional[List[str]] = None,
        recent_topics: Optional[List[str]] = None,
        max_candidates: int = 5,
    ) -> Optional[TopicOpportunity]:
        """Discover opportunities and return top-ranked candidate passing evidence gates (or None if blocked)."""
        portfolio = self.discover_opportunities(
            channel=channel,
            seed_queries=seed_queries,
            recent_topics=recent_topics,
            max_candidates=max_candidates,
        )
        return portfolio.selected_topic

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
        creative_brief: Optional[VideoCreativeBrief] = None,
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

        # 6. Stage 4: Creative Brief -> Hook Tournament -> Retention Blueprint -> Script -> Retention QA
        try:
            if creative_brief is None:
                brief = resolve_default_creative_brief(
                    platform_format=project.format,
                    content_format=content_format,
                    dossier=dossier,
                )
            else:
                brief = resolve_default_creative_brief(
                    platform_format=project.format,
                    content_format=content_format,
                    requested_duration=creative_brief.target_duration_seconds,
                    primary_goal=creative_brief.primary_goal,
                    dossier=dossier,
                    common_misconception=creative_brief.common_misconception,
                    common_failure=creative_brief.common_failure,
                )
                if creative_brief.tone is not None:
                    brief.tone = creative_brief.tone
                if creative_brief.desired_viewer_emotion is not None:
                    brief.desired_viewer_emotion = creative_brief.desired_viewer_emotion

            hook_service = HookTournamentService(backend=self.backend)
            hook_candidates = hook_service.generate_hook_candidates(
                topic=keyword,
                dossier=dossier,
                brief=brief,
                content_format=content_format,
                channel=channel,
            )
            winner_hook, _, _ = hook_service.run_tournament(
                candidates=hook_candidates,
                topic=keyword,
                dossier=dossier,
                brief=brief,
            )

            retention_planner = RetentionPlanner()
            blueprint = retention_planner.build_blueprint(
                hook=winner_hook,
                brief=brief,
                content_format=content_format,
                topic=keyword,
                dossier=dossier,
            )

            sections = self.generator.generate_script_sections(
                channel=channel,
                keyword=keyword,
                dossier=dossier,
                content_format=content_format,
                series_continuity=series_continuity,
                brief=brief,
                hook=winner_hook,
                blueprint=blueprint,
            )
            script = self.writer.build_script(
                script_id=f"scr-{project_id}",
                title=keyword,
                sections=sections,
                content_format=content_format,
                retention_blueprint=blueprint,
            )

            # Initial Script Retention QA
            retention_evaluator = ScriptRetentionEvaluator()
            ret_report = retention_evaluator.evaluate(script, blueprint=blueprint, dossier=dossier)

            # Bounded retention rewrite if severe retention drop risk detected
            if not ret_report.passed and len(ret_report.rewrite_instructions) > 0:
                revised_sections = self.generator.rewrite_for_retention(
                    channel=channel,
                    original_sections=script.sections,
                    retention_report=ret_report,
                    blueprint=blueprint,
                    dossier=dossier,
                )
                if isinstance(revised_sections, ScriptSections):
                    script = self.writer.build_script(
                        script_id=f"scr-{project_id}-ret1",
                        title=keyword,
                        sections=revised_sections,
                        content_format=content_format,
                        retention_blueprint=blueprint,
                    )

            project.script = script
            project.content_format = content_format
            # Durable Checkpoint: Save script & video project
            self.repo.save_video_project(project)

            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.SCRIPTED,
                reason="Script generated with structured scene segments and retention blueprint",
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
                        retention_blueprint=blueprint,
                    )
                    project.script = revised_script
                    project.content_format = content_format
                    self.repo.save_video_project(project)
                else:
                    break

            # 7b. Post-Fact-Check Final Retention QA (fact-check rewrite can alter pacing)
            final_ret_report = retention_evaluator.evaluate(
                project.script, blueprint=blueprint, dossier=dossier, fact_report=report
            )

            # Option (a): If FactCheck passed, but retention QA failed for non-factual creative defects:
            # Perform at most ONE bounded retention rewrite, then RE-RUN claim extraction + fact checking, then retention QA again.
            if (
                report.overall_verdict == QualityStatus.PASSED
                and report.failed_count == 0
                and len(report.claims) > 0
                and not final_ret_report.passed
                and len(final_ret_report.rewrite_instructions) > 0
                and project.script.sections is not None
            ):
                revised_sections = self.generator.rewrite_for_retention(
                    channel=channel,
                    original_sections=project.script.sections,
                    retention_report=final_ret_report,
                    blueprint=blueprint,
                    dossier=dossier,
                )
                if isinstance(revised_sections, ScriptSections):
                    revised_script = self.writer.build_script(
                        script_id=f"scr-{project_id}-ret-final",
                        title=keyword,
                        sections=revised_sections,
                        content_format=content_format,
                        retention_blueprint=blueprint,
                    )
                    project.script = revised_script
                    project.content_format = content_format
                    self.repo.save_video_project(project)

                    # CRITICAL INVARIANT: NEVER modify script after fact check without re-running fact check!
                    extracted_claims = self.extractor.extract_from_script(project.script)
                    dossier.claims = extracted_claims
                    report = self.checker.verify_all_claims(
                        claims=extracted_claims,
                        dossier=dossier,
                        project_id=project_id,
                    )
                    self.repo.save_fact_check_report(report)

                    # Re-evaluate final retention report after re-verification
                    final_ret_report = retention_evaluator.evaluate(
                        project.script, blueprint=blueprint, dossier=dossier, fact_report=report
                    )

            # Persist final retention report onto script and pipeline instance
            if project.script:
                project.script.retention_report = final_ret_report
                if project.script.sections:
                    project.script.sections.retention_report = final_ret_report
            self.last_retention_report = final_ret_report
            self.repo.save_video_project(project)

            # 8. Stage 6: Authoritative Verification Gate
            if report.overall_verdict == QualityStatus.PASSED and report.failed_count == 0 and len(report.claims) > 0:
                if final_ret_report.passed:
                    self.repo.update_project_state(
                        project_id=project_id,
                        to_state=VideoLifecycleState.VERIFIED,
                        reason=f"All {report.verified_count} factual claims verified against source evidence and retention QA passed",
                    )
                else:
                    self.repo.update_project_state(
                        project_id=project_id,
                        to_state=VideoLifecycleState.BLOCKED,
                        reason="RETENTION_QA_FAILED_AFTER_BOUNDED_REPAIR",
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
        if updated_project and updated_project.script and project and project.script:
            if not updated_project.script.retention_blueprint and project.script.retention_blueprint:
                updated_project.script.retention_blueprint = project.script.retention_blueprint
            if not getattr(updated_project.script, "retention_report", None) and getattr(project.script, "retention_report", None):
                updated_project.script.retention_report = project.script.retention_report
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
        tts_backend: Optional[TTSBackend] = None,
        creative_brief: Optional[VideoCreativeBrief] = None,
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
            creative_brief=creative_brief,
        )

        if project.state in (VideoLifecycleState.BLOCKED, VideoLifecycleState.FAILED):
            return {
                "project_id": project_id,
                "lifecycle_state": project.state.value,
                "fact_check_report": {
                    "id": fact_report.id,
                    "overall_verdict": fact_report.overall_verdict.value,
                    "verified_count": fact_report.verified_count,
                    "failed_count": fact_report.failed_count,
                },
                "status": project.state.value,
                "halted_reason": f"Project entered {project.state.value} at Stage 5, halting before media production.",
            }

        if slot_id:
            cal_service.book_slot(slot_id, project_id)

        # 2. Stages 6-10: Multimodal Production (MediaProductionPipeline with AutoDirector)
        # Canonical GFlow provider passed cleanly
        gflow_prov = GFlowMediaProvider() if enable_gflow else None
        scene_planner = ScenePlanner(gflow_provider=gflow_prov)
        active_tts = tts_backend or self.tts_backend
        media_pipeline = MediaProductionPipeline(
            repository=self.repo,
            scene_planner=scene_planner,
            gflow_provider=gflow_prov,
            reasoning_backend=self.backend,
            tts_backend=active_tts,
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

        packaging_service = PackagingEngineService(
            repository=self.repo,
            thumbnail_designer=thumb_service,
            backend=self.backend,
            output_dir=Path("output/projects"),
        )
        tournament = packaging_service.run_tournament(
            project_id=project_id,
            primary_keyword=keyword,
            series_context=continuity,
        )

        # Refresh seo_pkg and thumb_pkg to reflect tournament selection
        seo_pkg = self.repo.get_seo_package(project_id) or seo_pkg
        thumb_pkg = self.repo.get_thumbnail_package(project_id)
        if not thumb_pkg:
            bg_asset = None
            for a in project.assets:
                if (
                    a.asset_type in (AssetType.IMAGE, AssetType.SCENE_CARD)
                    and Path(a.file_path).exists()
                    and Path(a.file_path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
                ):
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
                analytics_status = "COLLECTED"
            else:
                # Post-upload: real performance data is not available immediately from YouTube Analytics API (24-48h lag).
                # Do NOT invent fake zero baseline. Await genuine ingestion via refresh_project_analytics.
                analytics_snapshot = None
                analytics_status = "PENDING_REAL_DATA"
        elif project.state == VideoLifecycleState.BLOCKED:
            # Cleanly skip analytics on BLOCKED projects
            analytics_snapshot = None
            analytics_status = "SKIPPED"
        else:
            analytics_snapshot = None
            analytics_status = "SKIPPED"

        # 9. Stage 15: Strategy Feedback Loop
        strategy_feedback = StrategyFeedbackLoop(self.repo)
        strategy_analysis = strategy_feedback.analyze_channel_performance(channel.id)

        return {
            "project_id": project.id,
            "project": project,
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
                "passed": qa_result.passed,
                "loudness_lufs": qa_result.loudness_lufs,
                "duration_seconds": qa_result.video_duration,
                "issues": qa_result.issues,
            },
            "render_manifest": {
                "final_video_path": str(render_manifest.final_video_path),
                "final_video_sha256": render_manifest.final_video_sha256,
                "production_fingerprint": render_manifest.production_fingerprint,
                "scene_count": render_manifest.scene_count,
                "qa_verdict": render_manifest.qa_verdict,
                "creative_profile": render_manifest.creative_profile,
                "contains_synthetic_media": render_manifest.contains_synthetic_media,
            },
            "packaging_tournament": {
                "id": tournament.id,
                "selected_candidate_id": tournament.selected_candidate_id,
                "selection_reason": tournament.selection_reason,
                "candidates_count": len(tournament.candidates),
                "native_ab_eligible": tournament.native_ab_eligible,
                "status": tournament.status.value,
            } if tournament else None,
            "seo_package": {
                "selected_title": seo_pkg.selected_title,
                "primary_keyword": seo_pkg.primary_keyword,
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
                "approved_privacy_status": review_record.approved_privacy_status.value if review_record else None,
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
                "contains_synthetic_media": pub_job.contains_synthetic_media if pub_job else False,
                "error_message": pub_job.error_message if pub_job else None,
                "mode": pub_mode,
            } if pub_job else None,
            "analytics_status": analytics_status,
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
            "strategy_analysis": strategy_analysis,
        }

    def refresh_project_analytics(
        self,
        project_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> AnalyticsCollectionResult:
        """Ingest authentic playback & retention observations for a published project."""
        ingestion_service = YouTubeAnalyticsIngestionService(
            oauth_manager=oauth_manager,
            repository=self.repo,
            http_client=http_client,
        )
        return ingestion_service.ingest_project_analytics(
            project_id=project_id,
            start_date=start_date,
            end_date=end_date,
        )

    def refresh_channel_analytics(
        self,
        channel_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> List[AnalyticsCollectionResult]:
        """Ingest authentic playback & retention observations for all published projects of a channel."""
        projects = self.repo.list_video_projects_by_channel(channel_id)
        results: List[AnalyticsCollectionResult] = []
        ingestion_service = YouTubeAnalyticsIngestionService(
            oauth_manager=oauth_manager,
            repository=self.repo,
            http_client=http_client,
        )
        for proj in projects:
            if proj.state in (VideoLifecycleState.PUBLISHED, VideoLifecycleState.SCHEDULED):
                res = ingestion_service.ingest_project_analytics(
                    project_id=proj.id,
                    start_date=start_date,
                    end_date=end_date,
                )
                results.append(res)
        return results

    def refresh_reach_reports(
        self,
        channel_id: Optional[str] = None,
        oauth_manager: Optional[YouTubeOAuthManager] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> ReachSyncResult:
        """Ingest authentic post-publication thumbnail reach observations from YouTube Reporting API."""
        reach_service = YouTubeReachReportingService(
            repository=self.repo,
            oauth_manager=oauth_manager,
            http_client=http_client,
        )
        return reach_service.sync_reach(channel_id=channel_id)

    def get_packaging_reach_feedback(
        self,
        project_id: str,
    ) -> Optional[PackagingReachFeedback]:
        """Compute post-publication packaging reach feedback with impression-weighted CTR."""
        return compute_packaging_reach_feedback(repo=self.repo, project_id=project_id)


PipelineBrain = BrainPipeline
