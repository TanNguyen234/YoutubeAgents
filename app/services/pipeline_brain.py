"""Integrated pipeline orchestrator executing Stages 1-5 (Research to Verified Script)."""

from typing import List, Optional, Tuple
from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import QualityStatus, VideoLifecycleState
from app.domain.models import (
    Channel,
    FactCheckReport,
    ResearchDossier,
    Script,
    ScriptSections,
    TopicCandidate,
    VideoProject,
)
from app.services.claim_extractor import ClaimExtractor
from app.services.duplicate_detector import DuplicateDetector
from app.services.fact_checker import FactChecker
from app.services.research_agent import ResearchAgent, ResearchFetchError
from app.services.script_generator import ScriptGenerator
from app.services.script_writer import ScriptWriter
from app.services.topic_evaluator import TopicEvaluator
from app.services.topic_strategist import TopicStrategist


class BrainPipeline:
    """Orchestrates Stages 1-5 of the YouTube Autopilot pipeline."""

    def __init__(
        self,
        repository: SQLiteRepository,
        backend: Optional[ReasoningBackend] = None,
        topic_strategist: Optional[TopicStrategist] = None,
        topic_evaluator: Optional[TopicEvaluator] = None,
        research_agent: Optional[ResearchAgent] = None,
        script_generator: Optional[ScriptGenerator] = None,
        script_writer: Optional[ScriptWriter] = None,
        claim_extractor: Optional[ClaimExtractor] = None,
        fact_checker: Optional[FactChecker] = None,
    ):
        self.repo = repository
        self.backend = backend or AntigravityCLIBackend()
        self.strategist = topic_strategist or TopicStrategist()
        self.evaluator = topic_evaluator or TopicEvaluator(backend=self.backend)
        self.researcher = research_agent or ResearchAgent()
        self.generator = script_generator or ScriptGenerator(backend=self.backend)
        self.writer = script_writer or ScriptWriter()
        self.extractor = claim_extractor or ClaimExtractor(backend=self.backend)
        self.checker = fact_checker or FactChecker(backend=self.backend)

    def run_stage_1_to_5(
        self,
        project_id: str,
        channel: Channel,
        keyword: str,
        seed_urls: List[str],
        recent_topics: Optional[List[str]] = None,
        max_rewrite_attempts: int = 2,
    ) -> Tuple[VideoProject, FactCheckReport]:
        """Execute Stage 1 (Select Topic) -> Stage 2 (Evidence) -> Stage 3 (Script) -> Stage 4 (Fact Check) -> Stage 5 (Verification Gate)."""
        # Ensure project exists in CREATED state
        project = self.repo.get_video_project(project_id)
        if not project:
            project = VideoProject(
                id=project_id,
                channel_id=channel.id,
                title=keyword,
                state=VideoLifecycleState.CREATED,
            )
            self.repo.save_video_project(project)

        # 1. Duplicate check before advancing
        is_dup, dup_score, matched = self.strategist.duplicate_detector.check_duplicate(keyword, recent_topics or [])
        if is_dup:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Duplicate topic rejected: conflicts with '{matched}' (similarity: {dup_score:.2f})",
            )
            raise ValueError(f"Duplicate topic detected: '{keyword}' conflicts with '{matched}'")

        # Stage 1: Topic Selection -> transition to RESEARCHING
        self.repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.RESEARCHING,
            reason="Starting topic evaluation and evidence collection",
        )

        # 2. Topic Evaluation via Antigravity reasoning
        try:
            scores_breakdown, rationale = self.evaluator.evaluate_topic_with_reasoning(
                channel=channel,
                keyword=keyword,
                evidence_summary=f"Target sources: {', '.join(seed_urls)}",
            )
            candidate = self.strategist.evaluate_candidate(
                topic_id=f"top-{project_id}",
                channel=channel,
                keyword=keyword,
                raw_scores=scores_breakdown,
                rationale=rationale,
                recent_channel_topics=recent_topics or [],
            )
        except Exception as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Topic evaluation reasoning failed: {str(e)}",
            )
            raise

        # Stage 2: Evidence Collection -> transition to PLANNED
        try:
            dossier = self.researcher.build_dossier_from_urls(
                topic_id=candidate.id,
                urls=seed_urls,
                summary=f"Evidence for '{keyword}' compiled from {len(seed_urls)} source(s)",
            )
        except ResearchFetchError as e:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.BLOCKED,
                reason=f"Research evidence collection BLOCKED: {str(e)}",
            )
            raise

        self.repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.PLANNED,
            reason=f"Evidence dossier compiled with {len(dossier.sources)} verified source(s)",
        )

        # Stage 3: Script Generation -> transition to SCRIPTED
        try:
            sections = self.generator.generate_script_sections(
                channel=channel,
                keyword=keyword,
                dossier=dossier,
            )
            script = self.writer.build_script(
                script_id=f"scr-{project_id}",
                title=keyword,
                sections=sections,
            )
            project.script = script
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

        # Stage 4: Claim Extraction & Fact Checking
        for attempt in range(max_rewrite_attempts + 1):
            # Extract claims from current script voiceover
            extracted_claims = self.extractor.extract_from_script(project.script)
            dossier.claims = extracted_claims

            # Verify claims against dossier source text
            report = self.checker.verify_all_claims(
                claims=extracted_claims,
                dossier=dossier,
                project_id=project_id,
            )

            # If passed, break out
            if report.overall_verdict == QualityStatus.PASSED:
                break

            # If rewrite required and attempts remain, rewrite script
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
                )
                project.script = revised_script
                self.repo.save_video_project(project)
            else:
                break

        # Stage 5: Verification Gate Decision
        if report.overall_verdict == QualityStatus.PASSED:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.VERIFIED,
                reason=f"Fact-checking passed: {report.verified_count} claim(s) verified with source URL citations",
            )
        else:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Fact-checking failed: {report.audit_summary}",
            )

        updated_project = self.repo.get_video_project(project_id)
        return updated_project, report
