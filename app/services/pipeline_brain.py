"""BrainPipeline orchestrating Stages 1-5 from Topic Selection through Fact-Checking and Verification Gate."""

from typing import List, Optional, Tuple

from app.core.backend import AntigravityCLIBackend, ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import QualityStatus, VideoLifecycleState
from app.domain.models import (
    Channel,
    FactCheckReport,
    ResearchDossier,
    TopicCandidate,
    VideoProject,
)
from app.services.claim_extractor import ClaimExtractor
from app.services.fact_checker import FactChecker
from app.services.research_agent import ResearchAgent, ResearchFetchError
from app.services.script_generator import ScriptGenerator
from app.services.script_writer import ScriptWriter
from app.services.topic_evaluator import TopicEvaluator
from app.services.topic_strategist import TopicStrategist


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
            )
            self.repo.save_video_project(project)

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
            )
            script = self.writer.build_script(
                script_id=f"scr-{project_id}",
                title=keyword,
                sections=sections,
            )
            project.script = script
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
                )
                project.script = revised_script
                self.repo.save_video_project(project)
            else:
                break

        # 8. Stage 6: Authoritative Verification Gate
        if report.overall_verdict == QualityStatus.PASSED and report.failed_count == 0:
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

        updated_project = self.repo.get_video_project(project_id) or project
        return updated_project, report
