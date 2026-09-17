"""Auto Director Service coordinating multi-shot decomposition, modality dispatch, and timeline composition."""

import hashlib
import inspect
import logging
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from app.core.backend import ReasoningBackend
from app.domain.models import FactCheckReport, ResearchDossier, Script
from app.media.director.beat_decomposer import BeatDecomposer
from app.media.director.modality_router import VisualModalityRouter
from app.media.director.models import (
    ChannelCreativeProfile,
    ContentFormat,
    CreativeFallbackPolicy,
    NarrativeBeat,
    ShotAssetResult,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    TimedRetentionCue,
    VisualEvaluation,
    VisualIntent,
    VisualModality,
    VisualizationDataMode,
)
from app.media.semantic_qa.models import VisualSemanticQAError, VisualSemanticQAMode, VisualSemanticVerdict
from app.media.acquisition.models import VisualSourceType
from app.media.director.quality_evaluator import VisualShotEvaluator
from app.media.director.storyboard_planner import StoryboardPlanner
from app.media.gflow_provider import AssetGenerationAttempt
from app.media.renderers.chart_renderer import ChartRenderer
from app.media.renderers.diagram_renderer import DiagramRenderer
from app.media.renderers.evidence_renderer import EvidenceRenderer
from app.media.renderers.motion_graphics import MotionGraphicsRenderer
from app.media.visual_factory import VisualFactory


class DirectorError(RuntimeError):
    """Raised when director planning or asset generation encounters an unrecoverable error."""
    pass


class AutoDirectorService:
    """Automated video director transforming scripts into multi-shot visual timelines."""

    def __init__(
        self,
        profile: Optional[ChannelCreativeProfile] = None,
        decomposer: Optional[BeatDecomposer] = None,
        router: Optional[VisualModalityRouter] = None,
        planner: Optional[StoryboardPlanner] = None,
        visual_factory: Optional[VisualFactory] = None,
        gflow_provider: Optional[Any] = None,
        evaluator: Optional[VisualShotEvaluator] = None,
        reasoning_backend: Optional[ReasoningBackend] = None,
        acquisition_router: Optional[Any] = None,
    ):
        self.profile = profile or ChannelCreativeProfile()
        self.backend = reasoning_backend
        self.decomposer = decomposer or BeatDecomposer(backend=self.backend)
        self.router = router or VisualModalityRouter(profile=self.profile)
        self.planner = planner or StoryboardPlanner(router=self.router, profile=self.profile)
        self.visual_factory = visual_factory or VisualFactory()
        self.gflow_provider = gflow_provider
        self.evaluator = evaluator or VisualShotEvaluator(profile=self.profile)

        # Renderers
        self.diagram_renderer = DiagramRenderer()
        self.chart_renderer = ChartRenderer()
        self.motion_renderer = MotionGraphicsRenderer()
        self.evidence_renderer = EvidenceRenderer()

        # Visual Acquisition Router
        from app.media.acquisition.router import VisualAcquisitionRouter
        self.acquisition_router = acquisition_router or VisualAcquisitionRouter(
            gflow_provider=self.gflow_provider,
            diagram_renderer=self.diagram_renderer,
            chart_renderer=self.chart_renderer,
            motion_renderer=self.motion_renderer,
            visual_factory=self.visual_factory,
        )

        if getattr(self.acquisition_router, "semantic_judge", None) is None:
            from app.media.semantic_qa.backend import AntigravityVisualBackend
            from app.media.semantic_qa.evaluator import VisualSemanticEvaluator
            from app.media.semantic_qa.judge import VisualCandidateJudge

            if hasattr(self.backend, "evaluate_visual"):
                v_backend = self.backend
            else:
                v_backend = AntigravityVisualBackend()
            v_evaluator = VisualSemanticEvaluator(backend=v_backend)
            self.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=v_evaluator)

        # Audit logs & QA tracking
        self.asset_attempts: List[AssetGenerationAttempt] = []
        self.shot_evaluations: Dict[str, VisualEvaluation] = {}
        self.shot_attempt_counts: Dict[str, int] = {}

    def apply_profile(self, profile: ChannelCreativeProfile) -> None:
        """Propagate creative profile across all dependent director components."""
        self.profile = profile
        if hasattr(self, "router") and self.router:
            self.router.profile = profile
        if hasattr(self, "planner") and self.planner:
            self.planner.profile = profile
            if hasattr(self.planner, "router") and self.planner.router:
                self.planner.router.profile = profile
        if hasattr(self, "evaluator") and self.evaluator:
            self.evaluator.profile = profile
        if hasattr(self, "acquisition_router") and self.acquisition_router:
            mode_val = getattr(profile, "semantic_qa_mode", "ADVISORY")
            if isinstance(mode_val, VisualSemanticQAMode):
                self.acquisition_router.qa_mode = mode_val
            elif isinstance(mode_val, str):
                try:
                    self.acquisition_router.qa_mode = VisualSemanticQAMode(mode_val.upper())
                except Exception:
                    self.acquisition_router.qa_mode = VisualSemanticQAMode.ADVISORY

    def plan_and_render_timeline(
        self,
        project_id: str,
        script: Script,
        channel_name: str,
        total_audio_duration: float,
        output_dir: Path,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
        retention_cues: Optional[List[TimedRetentionCue]] = None,
    ) -> Tuple[ShotTimeline, Storyboard]:
        """Execute full director workflow: Decompose -> Plan Storyboard -> Dispatch Renderers -> QA Evaluate -> Selective Retry -> Assemble Timeline."""
        # Reset state between runs so project A state cannot leak into project B
        self.asset_attempts.clear()
        self.shot_evaluations.clear()
        self.shot_attempt_counts.clear()

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        shots_dir = output_dir / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)

        # Wire up per-project SemanticQACache sidecar
        from app.media.semantic_qa.cache import SemanticQACache
        manifest_dir = output_dir.parent / "manifests" if output_dir.name in ("director", "shots") else output_dir / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        proj_cache_path = manifest_dir / "semantic_qa_cache.json"
        proj_cache = SemanticQACache(cache_file_path=proj_cache_path)

        judge = getattr(self.acquisition_router, "semantic_judge", None)
        if judge and getattr(judge, "evaluator", None):
            judge.evaluator.cache = proj_cache

        claims = []
        if fact_report and fact_report.claims:
            claims.extend(fact_report.claims)
        if dossier and dossier.claims:
            for c in dossier.claims:
                if not any(ec.id == c.id for ec in claims):
                    claims.append(c)

        sources = dossier.sources if dossier else []

        # 1. Narrative Beat Decomposition with claims and sources propagation
        beats = self.decomposer.decompose_script(
            script=script,
            total_audio_duration=total_audio_duration,
            content_format=content_format,
            claims=claims,
            sources=sources,
        )

        # 2. Storyboard Planning
        storyboard = self.planner.plan_storyboard(
            project_id=project_id,
            script=script,
            beats=beats,
            total_audio_duration=total_audio_duration,
            content_format=content_format,
            dossier=dossier,
            fact_report=fact_report,
            retention_cues=retention_cues,
        )

        # Persist Storyboard Artifact
        storyboard_path = output_dir / f"storyboard_{project_id}.json"
        self.planner.save_storyboard_artifact(storyboard, storyboard_path)

        # 3. Render, evaluate, and assemble each shot on the timeline
        timeline_shots: List[TimelineShot] = []
        cur_time = 0.0
        max_creative_retries = 2

        for shot_idx, shot in enumerate(storyboard.shots):
            shot_start = cur_time
            shot_end = min(total_audio_duration, cur_time + shot.duration_seconds)
            actual_dur = round(shot_end - shot_start, 3)
            cur_time = shot_end

            asset_res = self._generate_shot_asset(
                shot=shot,
                shot_index=shot_idx,
                output_dir=shots_dir,
                script_title=script.title,
                channel_name=channel_name,
                dossier=dossier,
                fact_report=fact_report,
            )
            asset_path = Path(asset_res.path)
            asset_hash = asset_res.sha256

            # Correct fallback modality accounting
            shot.requested_modality = asset_res.requested_modality
            shot.visual_modality = asset_res.actual_modality

            # 14. Execute evaluate_shot() before final render
            asset_exists = Path(asset_path).exists() and Path(asset_path).stat().st_size > 0
            evaluation = self.evaluator.evaluate_shot(shot=shot, asset_exists=asset_exists)
            attempt_count = 1

            # 15. Limited selective regeneration loop
            while evaluation.recommendation == "REGENERATE" and attempt_count <= max_creative_retries:
                attempt_count += 1
                fallback_mod = None
                if any("duplication" in issue.lower() for issue in evaluation.issues):
                    shot.headline_text = None
                if any("STATIC_CARD" in issue for issue in evaluation.issues) or shot.visual_modality == VisualModality.STATIC_CARD:
                    fallback_mod = VisualModality.DIAGRAM
                elif not asset_exists:
                    fallback_mod = VisualModality.DIAGRAM

                if fallback_mod:
                    shot.visual_modality = fallback_mod

                asset_res = self._generate_shot_asset(
                    shot=shot,
                    shot_index=shot_idx,
                    output_dir=shots_dir,
                    script_title=script.title,
                    channel_name=channel_name,
                    dossier=dossier,
                    fact_report=fact_report,
                )
                asset_path = Path(asset_res.path)
                asset_hash = asset_res.sha256
                shot.requested_modality = asset_res.requested_modality
                shot.visual_modality = asset_res.actual_modality

                asset_exists = Path(asset_path).exists() and Path(asset_path).stat().st_size > 0
                evaluation = self.evaluator.evaluate_shot(shot=shot, asset_exists=asset_exists)

            # Safe semantic fallback if still failing after retries
            if evaluation.recommendation == "REGENERATE" and not asset_exists:
                shot.visual_modality = VisualModality.DIAGRAM
                asset_res = self._generate_shot_asset(
                    shot=shot,
                    shot_index=shot_idx,
                    output_dir=shots_dir,
                    script_title=script.title,
                    channel_name=channel_name,
                    dossier=dossier,
                    fact_report=fact_report,
                )
                asset_path = Path(asset_res.path)
                asset_hash = asset_res.sha256
                shot.requested_modality = asset_res.requested_modality
                shot.visual_modality = asset_res.actual_modality

                asset_exists = Path(asset_path).exists()
                evaluation = self.evaluator.evaluate_shot(shot=shot, asset_exists=asset_exists)

            self.shot_evaluations[shot.shot_id] = evaluation
            self.shot_attempt_counts[shot.shot_id] = attempt_count

            is_anim = Path(asset_path).suffix.lower() in [".mp4", ".mov", ".webm", ".mkv"]
            t_shot = TimelineShot(
                shot_id=shot.shot_id,
                scene_index=shot.scene_index,
                beat_id=shot.beat_id,
                start=round(shot_start, 3),
                end=round(shot_end, 3),
                duration=actual_dur,
                asset_path=str(asset_path),
                asset_sha256=asset_hash,
                modality=shot.visual_modality,
                is_animated=is_anim,
                transition_in="impact" if shot_idx == 0 else "fade",
                transition_out="fade",
                asset_source_type=getattr(asset_res, "source_type", None),
                asset_source_url=getattr(asset_res, "source_url", None),
                asset_source_ref=getattr(asset_res, "source_ref", None),
                asset_license=getattr(asset_res, "license_type", None),
                asset_attribution=getattr(asset_res, "attribution", None),
                asset_acquisition_method=getattr(asset_res, "acquisition_method", None),
                asset_is_synthetic=getattr(asset_res, "is_synthetic", False),
                asset_evidence_claim_ids=list(getattr(asset_res, "evidence_claim_ids", []) or []),
                asset_semantic_qa=getattr(asset_res, "semantic_audit", None),
            )
            timeline_shots.append(t_shot)

        # Adjust final shot to span exact audio duration
        if timeline_shots and total_audio_duration > 0.0:
            timeline_shots[-1].end = round(total_audio_duration, 3)
            timeline_shots[-1].duration = round(total_audio_duration - timeline_shots[-1].start, 3)

        timeline = ShotTimeline(shots=timeline_shots, total_duration=total_audio_duration)
        return timeline, storyboard

    def _dispatch_shot_asset(
        self,
        shot: ShotSpec,
        shot_index: int,
        output_dir: Path,
        script_title: str,
        channel_name: str,
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
    ) -> ShotAssetResult:
        """Dispatch asset generation to the best available renderer or provider for the shot modality."""
        t0 = time.time()
        requested_modality = shot.visual_modality
        modality = shot.visual_modality
        shot_id = shot.shot_id
        fallback_reason = None

        # Stock video check: route through acquisition_router
        if modality == VisualModality.STOCK_VIDEO:
            try:
                acq_req = self.acquisition_router.build_acquisition_request(
                    shot=shot,
                    project_id=dossier.topic_id if dossier else "proj",
                    dossier=dossier,
                    fact_report=fact_report,
                )
                acq_res = self.acquisition_router.acquire_visual(
                    request=acq_req,
                    output_dir=output_dir,
                    script_title=script_title,
                    channel_name=channel_name,
                    dossier=dossier,
                    fact_report=fact_report,
                    shot=shot,
                )
                selected = acq_res.selected_candidate
                if not selected and "SEMANTIC_QA_REJECTED_ALL" in acq_res.failure_reasons:
                    active_fallback = getattr(self.profile, "fallback_policy", CreativeFallbackPolicy.FAIL_CLOSED)
                    if active_fallback != CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW:
                        raise VisualSemanticQAError(
                            f"Visual Semantic QA rejected all candidates for shot '{shot_id}'. FAIL_CLOSED active."
                        )
                if selected and selected.source_type != VisualSourceType.FALLBACK_CARD:
                    provider_name = selected.acquisition_method
                    actual_mod = VisualModality.STOCK_VIDEO if selected.source_type == VisualSourceType.STOCK_MEDIA else VisualModality.DIAGRAM
                    self.asset_attempts.append(
                        AssetGenerationAttempt(
                            shot_id=shot_id,
                            provider=provider_name,
                            modality=actual_mod.value,
                            success=True,
                            output_path=selected.file_path,
                            latency_ms=int((time.time() - t0) * 1000),
                        )
                    )
                    return ShotAssetResult(
                        path=selected.file_path,
                        sha256=selected.content_sha256,
                        requested_modality=requested_modality,
                        actual_modality=actual_mod,
                        provider=provider_name,
                        source_type=selected.source_type.value,
                        source_url=selected.source_url,
                        source_ref=selected.source_ref,
                        license_type=selected.license_type,
                        attribution=selected.attribution,
                        acquisition_method=selected.acquisition_method,
                        is_synthetic=selected.is_synthetic,
                        evidence_claim_ids=selected.evidence_claim_ids or [],
                        fallback_reason=fallback_reason,
                        semantic_qa_performed=bool(acq_res.semantic_audit),
                        semantic_audit=acq_res.semantic_audit or None,
                    )
            except Exception as e:
                active_fallback = getattr(self.profile, "fallback_policy", CreativeFallbackPolicy.FAIL_CLOSED)
                if isinstance(e, VisualSemanticQAError) and active_fallback != CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW:
                    raise
                logger.warning(f"Stock video acquisition failed for {shot_id}: {e}")

            # Fallback if stock video acquisition produced no valid candidate
            fallback_modality = VisualModality.GENERATED_VIDEO if (self.gflow_provider and hasattr(self.gflow_provider, "generate_video")) else VisualModality.MOTION_GRAPHICS
            fallback_reason = f"STOCK_VIDEO acquisition unavailable or failed: routed to {fallback_modality.value}"
            modality = fallback_modality

        # Modality A: DIAGRAM
        if modality == VisualModality.DIAGRAM:
            target_path = output_dir / f"{shot_id}_diagram.png"
            instr = shot.diagram_instruction or shot.narration_segment
            try:
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=instr,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="diagram_renderer",
                        modality=modality.value,
                        prompt=instr,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.DIAGRAM,
                    provider="diagram_renderer",
                    fallback_reason=fallback_reason,
                )
            except Exception as e:
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="diagram_renderer",
                        modality=modality.value,
                        prompt=instr,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )

        # Modality B: DATA_VISUALIZATION
        elif modality == VisualModality.DATA_VISUALIZATION:
            instr = shot.chart_instruction or shot.narration_segment
            # Special dynamic fixture detection: LLM next-token autoregressive prediction
            is_llm_token = (
                "the capital of france is" in shot.narration_segment.lower()
                or ("predict" in shot.narration_segment.lower() and "token" in shot.narration_segment.lower())
                or any(getattr(c, "cue_type", "") in ("type_prompt", "reveal_candidates") for c in shot.motion_cues)
            )

            try:
                if is_llm_token:
                    shot.visual_data_mode = VisualizationDataMode.CONCEPTUAL
                    target_path = output_dir / f"{shot_id}_token_anim.mp4"
                    # Conceptual next-token distribution: normalized illustrative bar weights, no empirical percentages
                    p, h = self.motion_renderer.render_animated_token_prediction(
                        prompt_text="The capital of France is",
                        candidates=[("Paris", 0.82), ("London", 0.08), ("Berlin", 0.06), ("Rome", 0.04)],
                        selected_token="Paris",
                        output_path=target_path,
                        duration=shot.duration_seconds,
                        data_mode=VisualizationDataMode.CONCEPTUAL,
                    )
                elif shot.chart_data:
                    target_path = output_dir / f"{shot_id}_chart_anim.mp4"
                    p, h = self.motion_renderer.render_animated_bar_growth(
                        title=shot.headline_text or "Data Analysis",
                        categories=[d.label for d in shot.chart_data],
                        values=[d.value for d in shot.chart_data],
                        output_path=target_path,
                        duration=shot.duration_seconds,
                        unit=shot.chart_data[0].unit or "%",
                    )
                else:
                    target_path = output_dir / f"{shot_id}_chart.png"
                    p, h = self.chart_renderer.render_from_instruction(
                        instruction=instr,
                        output_path=target_path,
                        title=shot.headline_text or "Data Analysis",
                    )
                provider_name = "motion_renderer" if (is_llm_token or shot.chart_data) else "chart_renderer"
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider=provider_name,
                        modality=modality.value,
                        prompt=instr,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.DATA_VISUALIZATION,
                    provider=provider_name,
                    fallback_reason=fallback_reason,
                )
            except Exception as e:
                # If ungrounded or failed, gracefully fall back to Diagram
                target_path = output_dir / f"{shot_id}_diagram_fallback.png"
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=shot.narration_segment,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.DIAGRAM,
                    provider="diagram_renderer",
                    fallback_reason=f"DATA_VISUALIZATION failed ({e}), fell back to diagram",
                )

        # Modality C: CODE_ANIMATION / UI_SIMULATION
        elif modality in (VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION):
            cmd_text = shot.code_instruction or shot.screen_instruction or shot.narration_segment
            cmd_clean = re.sub(r"^[^:]+:\s*", "", cmd_text)

            # Distinguish REAL_TERMINAL vs ILLUSTRATIVE_TERMINAL: never invent fake execution latency or fake pass states
            if shot.terminal_mode == "REAL_TERMINAL" and shot.code_output_lines:
                output_lines = shot.code_output_lines
            else:
                # Illustrative terminal demonstrates syntax and conceptual structure without fake latency/timing
                output_lines = [
                    f"# Demonstrating syntax: {shot.subject or 'command sequence'}",
                    f"$ {cmd_clean[:42]}",
                    "# [Illustrative execution structure]",
                ]
            try:
                target_path = output_dir / f"{shot_id}_terminal.mp4"
                p, h = self.motion_renderer.render_animated_terminal_video(
                    command=cmd_clean[:40],
                    output_lines=output_lines,
                    output_path=target_path,
                    duration=shot.duration_seconds,
                    window_title=f"terminal — {shot.subject or 'syntax'}",
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer",
                        modality=modality.value,
                        prompt=cmd_clean,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=modality,
                    provider="motion_renderer",
                    fallback_reason=fallback_reason,
                )
            except Exception as e:
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer",
                        modality=modality.value,
                        prompt=cmd_clean,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )

        # Modality D: COMPARISON
        elif modality == VisualModality.COMPARISON:
            target_path = output_dir / f"{shot_id}_comparison.png"
            # Require grounded comparison columns; never use hardcoded domain-specific defaults
            if not shot.comparison_left or not shot.comparison_right or not shot.comparison_left.points or not shot.comparison_right.points:
                target_path = output_dir / f"{shot_id}_diagram_fallback.png"
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=shot.narration_segment,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.DIAGRAM,
                    provider="diagram_renderer",
                    fallback_reason="Incomplete comparison points, fell back to diagram",
                )

            try:
                p, h = self.motion_renderer.render_before_after_comparison(
                    title=shot.subject or "Comparative Analysis",
                    before_label=shot.comparison_left.label,
                    before_points=shot.comparison_left.points,
                    after_label=shot.comparison_right.label,
                    after_points=shot.comparison_right.points,
                    output_path=target_path,
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer",
                        modality=modality.value,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.COMPARISON,
                    provider="motion_renderer",
                    fallback_reason=fallback_reason,
                )
            except Exception as e:
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer",
                        modality=modality.value,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )

        # Modality: SCREEN_CAPTURE
        elif modality == VisualModality.SCREEN_CAPTURE:
            try:
                acq_req = self.acquisition_router.build_acquisition_request(
                    shot=shot,
                    project_id=dossier.topic_id if dossier else "proj",
                    dossier=dossier,
                    fact_report=fact_report,
                )
                acq_res = self.acquisition_router.acquire_visual(
                    request=acq_req,
                    output_dir=output_dir,
                    script_title=script_title,
                    channel_name=channel_name,
                    dossier=dossier,
                    fact_report=fact_report,
                    shot=shot,
                )
                selected = acq_res.selected_candidate
                if not selected and "SEMANTIC_QA_REJECTED_ALL" in acq_res.failure_reasons:
                    active_fallback = getattr(self.profile, "fallback_policy", CreativeFallbackPolicy.FAIL_CLOSED)
                    if active_fallback != CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW:
                        raise VisualSemanticQAError(
                            f"Visual Semantic QA rejected all candidates for shot '{shot_id}'. FAIL_CLOSED active."
                        )
                if selected and selected.source_type != VisualSourceType.FALLBACK_CARD:
                    actual_mod = VisualModality.SCREEN_CAPTURE if selected.source_type in (VisualSourceType.LOCAL_WEB_APP, VisualSourceType.WEB_PAGE, VisualSourceType.RESEARCH_SOURCE) else VisualModality.DIAGRAM
                    self.asset_attempts.append(
                        AssetGenerationAttempt(
                            shot_id=shot_id,
                            provider=selected.acquisition_method,
                            modality=actual_mod.value,
                            success=True,
                            output_path=selected.file_path,
                            latency_ms=int((time.time() - t0) * 1000),
                        )
                    )
                    return ShotAssetResult(
                        path=selected.file_path,
                        sha256=selected.content_sha256,
                        requested_modality=requested_modality,
                        actual_modality=actual_mod,
                        provider=selected.acquisition_method,
                        source_type=selected.source_type.value,
                        source_url=selected.source_url,
                        source_ref=selected.source_ref,
                        license_type=selected.license_type,
                        attribution=selected.attribution,
                        acquisition_method=selected.acquisition_method,
                        is_synthetic=selected.is_synthetic,
                        evidence_claim_ids=selected.evidence_claim_ids or [],
                        fallback_reason=fallback_reason,
                        semantic_qa_performed=bool(acq_res.semantic_audit),
                        semantic_audit=acq_res.semantic_audit or None,
                    )
            except Exception as e:
                active_fallback = getattr(self.profile, "fallback_policy", CreativeFallbackPolicy.FAIL_CLOSED)
                if isinstance(e, VisualSemanticQAError) and active_fallback != CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW:
                    raise
                logger.warning(f"Screen capture acquisition failed for {shot_id}: {e}")

            # Fallback to diagram
            target_path = output_dir / f"{shot_id}_screencap_fallback.png"
            p, h = self.diagram_renderer.render_from_instruction(
                instruction=shot.narration_segment,
                output_path=target_path,
                title=shot.subject or script_title,
            )
            return ShotAssetResult(
                path=str(p),
                sha256=h,
                requested_modality=requested_modality,
                actual_modality=VisualModality.DIAGRAM,
                provider="diagram_renderer",
                source_type="RENDERED",
                acquisition_method="diagram_screencap_fallback",
                fallback_reason="SCREEN_CAPTURE unavailable or failed, fell back to diagram",
            )

        # Modality E: DOCUMENT_EVIDENCE / SCREENSHOT
        elif modality in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT):
            target_path = output_dir / f"{shot_id}_evidence.png"

            # Resolve grounded evidence binding
            binding = shot.evidence_binding
            if not binding and (dossier or fact_report):
                from app.media.director.models import BeatPurpose, NarrativeBeat, VisualIntent
                binding = self.planner._resolve_evidence_binding(
                    beat=NarrativeBeat(
                        beat_id=shot.beat_id,
                        scene_index=shot.scene_index,
                        narration=shot.narration_segment,
                        duration_hint=shot.duration_seconds,
                        purpose=BeatPurpose.PROVE,
                        visual_intent=VisualIntent.SHOW_EVIDENCE,
                        source_refs=shot.source_refs,
                    ),
                    dossier=dossier,
                    fact_report=fact_report,
                )
                if binding:
                    shot.evidence_binding = binding

            if not binding or not binding.source_url or "official-documentation.org" in binding.source_url:
                # No grounded source binding: DOCUMENT_EVIDENCE must not be rendered. Route to Diagram.
                target_path = output_dir / f"{shot_id}_diagram_fallback.png"
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=shot.narration_segment,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.DIAGRAM,
                    provider="diagram_renderer",
                    source_type="RENDERED",
                    fallback_reason="Ungrounded evidence binding, fell back to diagram",
                )

            # Attempt Visual Acquisition via acquisition_router (Playwright web evidence capture)
            try:
                acq_req = self.acquisition_router.build_acquisition_request(
                    shot=shot,
                    project_id=dossier.topic_id if dossier else "proj",
                    dossier=dossier,
                    fact_report=fact_report,
                )
                acq_res = self.acquisition_router.acquire_visual(
                    request=acq_req,
                    output_dir=output_dir,
                    script_title=script_title,
                    channel_name=channel_name,
                    dossier=dossier,
                    fact_report=fact_report,
                    shot=shot,
                )
                selected = acq_res.selected_candidate
                if not selected and "SEMANTIC_QA_REJECTED_ALL" in acq_res.failure_reasons:
                    active_fallback = getattr(self.profile, "fallback_policy", CreativeFallbackPolicy.FAIL_CLOSED)
                    if active_fallback != CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW:
                        raise VisualSemanticQAError(
                            f"Visual Semantic QA rejected all candidates for shot '{shot_id}'. FAIL_CLOSED active."
                        )
                if selected and selected.source_type != VisualSourceType.FALLBACK_CARD:
                    provider_name = selected.acquisition_method
                    actual_mod = VisualModality.DOCUMENT_EVIDENCE if selected.source_type in (VisualSourceType.RESEARCH_SOURCE, VisualSourceType.DOCUMENT, VisualSourceType.WEB_PAGE) else VisualModality.DIAGRAM
                    self.asset_attempts.append(
                        AssetGenerationAttempt(
                            shot_id=shot_id,
                            provider=provider_name,
                            modality=actual_mod.value,
                            success=True,
                            output_path=selected.file_path,
                            latency_ms=int((time.time() - t0) * 1000),
                        )
                    )
                    return ShotAssetResult(
                        path=selected.file_path,
                        sha256=selected.content_sha256,
                        requested_modality=requested_modality,
                        actual_modality=actual_mod,
                        provider=provider_name,
                        source_type=selected.source_type.value,
                        source_url=selected.source_url or binding.source_url,
                        source_ref=selected.source_ref or (binding.source_ref if binding else None),
                        license_type=selected.license_type,
                        attribution=selected.attribution,
                        acquisition_method=selected.acquisition_method,
                        is_synthetic=selected.is_synthetic,
                        evidence_claim_ids=selected.evidence_claim_ids or ([binding.claim_id] if binding.claim_id else []),
                        fallback_reason=fallback_reason,
                        semantic_qa_performed=bool(acq_res.semantic_audit),
                        semantic_audit=acq_res.semantic_audit or None,
                    )
            except Exception as e:
                active_fallback = getattr(self.profile, "fallback_policy", CreativeFallbackPolicy.FAIL_CLOSED)
                if isinstance(e, VisualSemanticQAError) and active_fallback != CreativeFallbackPolicy.ALLOW_LEGACY_PREVIEW:
                    raise
                logger.warning(f"Acquisition router evidence capture failed: {e}")

            # Fallback to EvidenceRenderer card
            try:
                is_verbatim = bool(binding.source_excerpt and binding.excerpt_is_verbatim)
                display_text = binding.source_excerpt if is_verbatim else (binding.claim_text or shot.narration_segment)

                p, h = self.evidence_renderer.render_evidence_card(
                    source_title=binding.source_title,
                    source_url=binding.source_url,
                    highlighted_claim=display_text,
                    output_path=target_path,
                    benchmark_name="SOURCE CITATION",
                    is_verbatim=is_verbatim,
                    claim_verified=binding.claim_verified,
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="evidence_renderer",
                        modality=VisualModality.STATIC_CARD.value,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.STATIC_CARD,
                    provider="evidence_renderer",
                    source_type="FALLBACK_CARD",
                    source_url=binding.source_url,
                    source_ref=binding.source_ref,
                    license_type=None,
                    attribution=binding.source_title,
                    acquisition_method="evidence_summary_card",
                    is_synthetic=False,
                    evidence_claim_ids=[binding.claim_id] if binding.claim_id else [],
                    fallback_reason="Browser document capture unavailable or failed; fell back to evidence summary card",
                )
            except Exception as e:
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="evidence_renderer",
                        modality=modality.value,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )

        # Modality F: MOTION_GRAPHICS
        elif modality in (VisualModality.MOTION_GRAPHICS, VisualModality.TIMELINE):
            target_path = output_dir / f"{shot_id}_stat.png"
            stat_match = re.search(r"(\+?-?\d+(?:\.\d+)?(?:%|x|ms|s|GB|MB|K|M|B)?|\b[A-Z]{2,}\b)", shot.narration_segment)
            big_stat = shot.headline_text or (stat_match.group(1) if stat_match else None)
            if not big_stat:
                # No grounded stat metric: route to diagram rather than inventing fake 10x FASTER
                target_path = output_dir / f"{shot_id}_diagram_fallback.png"
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=shot.narration_segment,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.DIAGRAM,
                    provider="diagram_renderer",
                    fallback_reason="No grounded stat metric, fell back to diagram",
                )

            try:
                p, h = self.motion_renderer.render_stat_callout(
                    big_stat=big_stat,
                    label=shot.action or "System Insight",
                    context_detail=shot.narration_segment[:45],
                    output_path=target_path,
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer",
                        modality=modality.value,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return ShotAssetResult(
                    path=str(p),
                    sha256=h,
                    requested_modality=requested_modality,
                    actual_modality=VisualModality.MOTION_GRAPHICS,
                    provider="motion_renderer",
                    fallback_reason=fallback_reason,
                )
            except Exception as e:
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer",
                        modality=modality.value,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )

        # Modality G: GENERATED_VIDEO / GENERATED_IMAGE via GFlow
        elif modality in (VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE):
            if self.gflow_provider:
                prompt = shot.generation_prompt or shot.narration_segment
                if modality == VisualModality.GENERATED_VIDEO and hasattr(self.gflow_provider, "generate_video"):
                    target_vid = output_dir / f"{shot_id}_veo.mp4"
                    try:
                        import inspect
                        sig = inspect.signature(self.gflow_provider.generate_video)
                        kwargs = {"prompt": prompt, "output_path": target_vid}
                        dur = min(10, max(4, int(round(shot.duration_seconds))))
                        if "duration" in sig.parameters:
                            kwargs["duration"] = dur
                        if "duration_seconds" in sig.parameters:
                            kwargs["duration_seconds"] = dur
                        vid_p, vid_h, _ = self.gflow_provider.generate_video(**kwargs)
                        if Path(vid_p).exists() and Path(vid_p).stat().st_size > 0:
                            self.asset_attempts.append(
                                AssetGenerationAttempt(
                                    shot_id=shot_id,
                                    provider="gflow",
                                    modality=modality.value,
                                    prompt=prompt,
                                    success=True,
                                    output_path=str(vid_p),
                                    latency_ms=int((time.time() - t0) * 1000),
                                )
                            )
                            return ShotAssetResult(
                                path=str(vid_p),
                                sha256=vid_h,
                                requested_modality=requested_modality,
                                actual_modality=VisualModality.GENERATED_VIDEO,
                                provider="gflow",
                                source_type="GENERATED",
                                acquisition_method="gflow_veo_video",
                                is_synthetic=True,
                                fallback_reason=fallback_reason,
                            )
                    except Exception as e:
                        self.asset_attempts.append(
                            AssetGenerationAttempt(
                                shot_id=shot_id,
                                provider="gflow",
                                modality=modality.value,
                                prompt=prompt,
                                success=False,
                                error_type=type(e).__name__,
                                error_message=str(e),
                                latency_ms=int((time.time() - t0) * 1000),
                            )
                        )

                # Fallback to AI Image
                if hasattr(self.gflow_provider, "generate_image"):
                    target_img = output_dir / f"{shot_id}_art.png"
                    try:
                        img_p, img_h, _ = self.gflow_provider.generate_image(
                            prompt=prompt,
                            output_path=target_img,
                            aspect="9:16",
                        )
                        if Path(img_p).exists() and Path(img_p).stat().st_size > 0:
                            self.asset_attempts.append(
                                AssetGenerationAttempt(
                                    shot_id=shot_id,
                                    provider="gflow",
                                    modality="GENERATED_IMAGE",
                                    prompt=prompt,
                                    success=True,
                                    output_path=str(img_p),
                                    latency_ms=int((time.time() - t0) * 1000),
                                )
                            )
                            return ShotAssetResult(
                                path=str(img_p),
                                sha256=img_h,
                                requested_modality=requested_modality,
                                actual_modality=VisualModality.GENERATED_IMAGE,
                                provider="gflow",
                                source_type="GENERATED",
                                acquisition_method="gflow_imagen_image",
                                is_synthetic=True,
                                fallback_reason="Video generation failed, fell back to AI image" if modality == VisualModality.GENERATED_VIDEO else fallback_reason,
                            )
                    except Exception as e:
                        self.asset_attempts.append(
                            AssetGenerationAttempt(
                                shot_id=shot_id,
                                provider="gflow",
                                modality="GENERATED_IMAGE",
                                prompt=prompt,
                                success=False,
                                error_type=type(e).__name__,
                                error_message=str(e),
                                latency_ms=int((time.time() - t0) * 1000),
                            )
                        )

            # If GFlow not available or failed, fallback to Diagram or Motion Graphics instead of static card!
            target_path = output_dir / f"{shot_id}_diagram_fallback.png"
            p, h = self.diagram_renderer.render_from_instruction(
                instruction=shot.narration_segment,
                output_path=target_path,
                title=shot.subject or script_title,
            )
            return ShotAssetResult(
                path=str(p),
                sha256=h,
                requested_modality=requested_modality,
                actual_modality=VisualModality.DIAGRAM,
                provider="diagram_renderer",
                fallback_reason="GFlow generation unavailable or failed, fell back to diagram",
            )

        # Fallback of last resort: Static Card
        card_path = output_dir / f"{shot_id}_card.png"
        p, h = self.visual_factory.render_scene_card(
            scene_index=shot.scene_index,
            channel_name=channel_name,
            topic_title=script_title,
            scene_headline=shot.headline_text or shot.narration_segment,
            output_path=card_path,
        )
        self.asset_attempts.append(
            AssetGenerationAttempt(
                shot_id=shot_id,
                provider="visual_factory_static_card",
                modality=VisualModality.STATIC_CARD.value,
                success=True,
                output_path=str(p),
                latency_ms=int((time.time() - t0) * 1000),
            )
        )
        return ShotAssetResult(
            path=str(p),
            sha256=h,
            requested_modality=requested_modality,
            actual_modality=VisualModality.STATIC_CARD,
            provider="visual_factory_static_card",
            fallback_reason="All primary renderers failed, fell back to static card",
        )

    def _evaluate_final_shot_asset_if_needed(
        self,
        shot: ShotSpec,
        asset_res: ShotAssetResult,
        output_dir: Path,
    ) -> ShotAssetResult:
        """Execute semantic QA gate on final rendered asset if not already evaluated.

        Covers direct render modalities: DIAGRAM, DATA_VISUALIZATION, CODE_ANIMATION,
        UI_SIMULATION, COMPARISON, MOTION_GRAPHICS, TIMELINE, GENERATED_IMAGE, GENERATED_VIDEO, etc.
        Enforces fail-closed when semantic_qa_mode == 'REQUIRED'.
        """
        if getattr(asset_res, "semantic_qa_performed", False):
            return asset_res

        qa_mode_val = getattr(self.profile, "semantic_qa_mode", "ADVISORY")
        if isinstance(qa_mode_val, str):
            qa_mode_str = qa_mode_val.upper()
        else:
            qa_mode_str = getattr(qa_mode_val, "value", "ADVISORY").upper()

        if qa_mode_str == "DISABLED":
            return asset_res

        asset_path = Path(asset_res.path)
        if not asset_path.exists() or asset_path.stat().st_size == 0:
            if qa_mode_str == "REQUIRED":
                raise VisualSemanticQAError(
                    f"VISUAL_ASSET_MISSING: Asset file for shot '{shot.shot_id}' does not exist or is empty: {asset_path}"
                )
            return asset_res

        # Retrieve or initialize semantic evaluator
        judge = getattr(self.acquisition_router, "semantic_judge", None)
        evaluator = getattr(judge, "evaluator", None)
        if not evaluator:
            from app.media.semantic_qa.backend import AntigravityVisualBackend
            from app.media.semantic_qa.evaluator import VisualSemanticEvaluator
            v_backend = self.backend if hasattr(self.backend, "evaluate_visual") else AntigravityVisualBackend()
            evaluator = VisualSemanticEvaluator(backend=v_backend)

        # Build candidate object representing the final rendered asset
        from app.media.acquisition.models import VisualAssetCandidate, VisualSourceType
        cand_src = VisualSourceType.RENDERED
        if asset_res.source_type and asset_res.source_type in VisualSourceType.__members__:
            cand_src = VisualSourceType(asset_res.source_type)

        cand = VisualAssetCandidate(
            candidate_id=f"final_{shot.shot_id}",
            source_type=cand_src,
            file_path=str(asset_path),
            content_sha256=asset_res.sha256,
            acquisition_method=asset_res.provider or "direct_renderer",
            is_synthetic=asset_res.is_synthetic,
            evidence_claim_ids=list(asset_res.evidence_claim_ids or []),
            source_url=asset_res.source_url,
            source_ref=asset_res.source_ref,
            license_type=asset_res.license_type,
            attribution=asset_res.attribution,
        )

        # Evaluate against actual produced modality (e.g. if fallback resolved to DIAGRAM)
        eval_shot = shot.model_copy(update={"visual_modality": asset_res.actual_modality})

        try:
            assessment = evaluator.evaluate_candidate(candidate=cand, shot=eval_shot)
            audit_dict = {
                "candidate_id": cand.candidate_id,
                "shot_id": shot.shot_id,
                "verdict": assessment.verdict.value,
                "semantic_relevance": assessment.semantic_relevance,
                "visual_intent_match": assessment.visual_intent_match,
                "subject_match": assessment.subject_match,
                "readability": assessment.readability,
                "information_value": assessment.information_value,
                "generic_slop_score": assessment.generic_slop_score,
                "issues": [i.value for i in assessment.issues],
                "concise_reason": assessment.concise_reason,
                "evaluator_backend": assessment.evaluator_backend,
                "evaluator_model": assessment.evaluator_model,
                "evaluator_policy_version": assessment.evaluator_policy_version,
                "candidate_sha256": assessment.candidate_sha256,
                "semantic_input_hash": assessment.semantic_input_hash,
            }
            if assessment.evidence_visibility is not None:
                audit_dict["evidence_visibility"] = assessment.evidence_visibility
            if assessment.interface_state_match is not None:
                audit_dict["interface_state_match"] = assessment.interface_state_match
            if assessment.mechanism_clarity is not None:
                audit_dict["mechanism_clarity"] = assessment.mechanism_clarity
            if assessment.comparison_clarity is not None:
                audit_dict["comparison_clarity"] = assessment.comparison_clarity
            if assessment.data_readability is not None:
                audit_dict["data_readability"] = assessment.data_readability

            asset_res.semantic_qa_performed = True
            asset_res.semantic_audit = audit_dict

            if assessment.verdict != VisualSemanticVerdict.ACCEPT:
                if qa_mode_str == "REQUIRED":
                    raise VisualSemanticQAError(
                        f"Visual Semantic QA REJECTED final asset for shot '{shot.shot_id}' (modality={asset_res.actual_modality.value}): "
                        f"issues={[i.value for i in assessment.issues]}, reason='{assessment.concise_reason}'"
                    )
                else:
                    logger.warning(
                        "VISUAL_SEMANTIC_QA_ADVISORY_REJECT: Shot '%s' final asset rejected: issues=%s reason='%s'",
                        shot.shot_id,
                        [i.value for i in assessment.issues],
                        assessment.concise_reason,
                    )
            return asset_res
        except VisualSemanticQAError:
            if qa_mode_str == "REQUIRED":
                raise
            logger.warning("VISUAL_SEMANTIC_QA_ERROR in advisory mode for %s", shot.shot_id, exc_info=True)
            return asset_res
        except Exception as e:
            if qa_mode_str == "REQUIRED":
                raise VisualSemanticQAError(f"Visual Semantic QA backend failure on shot '{shot.shot_id}': {e}") from e
            logger.warning("VISUAL_SEMANTIC_QA_ERROR in advisory mode for %s: %s", shot.shot_id, e)
            return asset_res

    def _generate_shot_asset(
        self,
        shot: ShotSpec,
        shot_index: int,
        output_dir: Path,
        script_title: str,
        channel_name: str,
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
    ) -> ShotAssetResult:
        """Execute shot asset dispatch followed by universal semantic QA evaluation."""
        res = self._dispatch_shot_asset(
            shot=shot,
            shot_index=shot_index,
            output_dir=output_dir,
            script_title=script_title,
            channel_name=channel_name,
            dossier=dossier,
            fact_report=fact_report,
        )
        return self._evaluate_final_shot_asset_if_needed(shot=shot, asset_res=res, output_dir=output_dir)

    def regenerate_single_shot(
        self,
        project_id: str,
        shot_id: str,
        timeline: ShotTimeline,
        storyboard: Storyboard,
        output_dir: Path,
        script_title: str = "Video Topic",
        channel_name: str = "YouTube Channel",
        fallback_modality: Optional[VisualModality] = None,
        new_instruction: Optional[str] = None,
        dossier: Optional[ResearchDossier] = None,
    ) -> Tuple[ShotTimeline, Storyboard]:
        """Regenerate a single specific shot on an existing timeline without re-running the entire pipeline."""
        output_dir = Path(output_dir)
        shots_dir = output_dir / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)

        target_shot = None
        target_idx = -1
        for idx, shot in enumerate(storyboard.shots):
            if shot.shot_id == shot_id:
                target_shot = shot
                target_idx = idx
                break

        if not target_shot:
            raise DirectorError(f"Shot '{shot_id}' not found in storyboard for project '{project_id}'.")

        if fallback_modality:
            target_shot.visual_modality = fallback_modality
        if new_instruction:
            if target_shot.visual_modality == VisualModality.DIAGRAM:
                target_shot.diagram_instruction = new_instruction
            elif target_shot.visual_modality == VisualModality.DATA_VISUALIZATION:
                target_shot.chart_instruction = new_instruction
            elif target_shot.visual_modality == VisualModality.MOTION_GRAPHICS:
                target_shot.motion_graphic_instruction = new_instruction
            elif target_shot.visual_modality in (VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE):
                target_shot.generation_prompt = new_instruction

        asset_res = self._generate_shot_asset(
            shot=target_shot,
            shot_index=target_idx,
            output_dir=shots_dir,
            script_title=script_title,
            channel_name=channel_name,
            dossier=dossier,
            fact_report=None,
        )
        target_shot.requested_modality = asset_res.requested_modality
        target_shot.visual_modality = asset_res.actual_modality

        for t_shot in timeline.shots:
            if t_shot.shot_id == shot_id:
                t_shot.asset_path = str(asset_res.path)
                t_shot.asset_sha256 = asset_res.sha256
                t_shot.modality = asset_res.actual_modality
                t_shot.asset_semantic_qa = getattr(asset_res, "semantic_audit", None)
                break

        storyboard_path = output_dir / f"storyboard_{project_id}.json"
        self.planner.save_storyboard_artifact(storyboard, storyboard_path)

        return timeline, storyboard

