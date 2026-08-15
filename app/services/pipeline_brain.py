"""Integrated pipeline orchestrator executing Stages 1-5 (Research to Verified Script)."""

from typing import List, Optional, Tuple
from app.db.repository import SQLiteRepository
from app.domain.enums import QualityStatus, VideoLifecycleState
from app.domain.models import (
    Channel,
    FactCheckReport,
    ResearchDossier,
    Script,
    ScriptSections,
    TopicCandidate,
    TopicScoreBreakdown,
    VideoProject,
)
from app.services.duplicate_detector import DuplicateDetector
from app.services.fact_checker import FactChecker
from app.services.research_agent import ResearchAgent
from app.services.script_writer import ScriptWriter
from app.services.topic_strategist import TopicStrategist


class BrainPipeline:
    """Orchestrates Stages 1-5 of the YouTube Autopilot pipeline."""

    def __init__(
        self,
        repository: SQLiteRepository,
        topic_strategist: Optional[TopicStrategist] = None,
        research_agent: Optional[ResearchAgent] = None,
        script_writer: Optional[ScriptWriter] = None,
        fact_checker: Optional[FactChecker] = None,
    ):
        self.repo = repository
        self.strategist = topic_strategist or TopicStrategist()
        self.researcher = research_agent or ResearchAgent()
        self.writer = script_writer or ScriptWriter()
        self.checker = fact_checker or FactChecker()

    def run_stage_1_to_5(
        self,
        project_id: str,
        channel: Channel,
        keyword: str,
        raw_scores: TopicScoreBreakdown,
        rationale: str,
        dossier: ResearchDossier,
        script_sections: ScriptSections,
        supported_claim_ids: List[str],
        recent_topics: Optional[List[str]] = None,
    ) -> Tuple[VideoProject, FactCheckReport]:
        """Execute Stage 1 (Select Topic) -> Stage 2 (Evidence) -> Stage 3 (Script) -> Stage 4 (Fact Check)."""
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

        # Stage 1: Topic Selection -> transition to RESEARCHING
        self.repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.RESEARCHING,
            reason="Evaluating topic candidate and starting research",
        )
        candidate = self.strategist.evaluate_candidate(
            topic_id=f"top-{project_id}",
            channel=channel,
            keyword=keyword,
            raw_scores=raw_scores,
            rationale=rationale,
            recent_channel_topics=recent_topics or [],
        )

        # Stage 2: Evidence & Planning -> transition to PLANNED
        self.repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.PLANNED,
            reason=f"Evidence dossier compiled with {len(dossier.sources)} source(s)",
        )

        # Stage 3: Script Generation -> transition to SCRIPTED
        script = self.writer.build_script(
            script_id=f"scr-{project_id}",
            title=keyword,
            sections=script_sections,
        )
        project.script = script
        self.repo.save_video_project(project)
        self.repo.update_project_state(
            project_id=project_id,
            to_state=VideoLifecycleState.SCRIPTED,
            reason="Script generated with structured scene segments",
        )

        # Stage 4: Fact Check
        evaluated_claims = []
        source_map = {s.id: s.url for s in dossier.sources}
        for claim in dossier.claims:
            is_supp = claim.id in supported_claim_ids
            conf = 0.95 if is_supp else 0.1
            ev_claim = self.checker.verify_claim(
                claim=claim,
                dossier=dossier,
                source_url_map=source_map,
                is_supported=is_supp,
                confidence=conf,
            )
            evaluated_claims.append(ev_claim)

        report = self.checker.build_audit_report(
            project_id=project_id,
            claims=evaluated_claims,
        )

        # Stage 5: Verification Gate
        if report.overall_verdict == QualityStatus.PASSED:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.VERIFIED,
                reason="Fact-checking passed with 100% verified claims and source URL citations",
            )
        else:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.FAILED,
                reason=f"Fact-checking failed: {report.audit_summary}",
            )

        updated_project = self.repo.get_video_project(project_id)
        return updated_project, report
