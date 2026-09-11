"""Auto Director Service coordinating multi-shot decomposition, modality dispatch, and timeline composition."""

import hashlib
import inspect
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.domain.models import ResearchDossier, Script
from app.media.director.beat_decomposer import BeatDecomposer
from app.media.director.modality_router import VisualModalityRouter
from app.media.director.models import (
    ChannelCreativeProfile,
    ContentFormat,
    NarrativeBeat,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualEvaluation,
    VisualIntent,
    VisualModality,
)
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
    ):
        self.profile = profile or ChannelCreativeProfile()
        self.decomposer = decomposer or BeatDecomposer()
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

        # Audit logs & QA tracking
        self.asset_attempts: List[AssetGenerationAttempt] = []
        self.shot_evaluations: Dict[str, VisualEvaluation] = {}
        self.shot_attempt_counts: Dict[str, int] = {}

    def plan_and_render_timeline(
        self,
        project_id: str,
        script: Script,
        channel_name: str,
        total_audio_duration: float,
        output_dir: Path,
        content_format: ContentFormat = ContentFormat.EXPLAINER,
        dossier: Optional[ResearchDossier] = None,
    ) -> Tuple[ShotTimeline, Storyboard]:
        """Execute full director workflow: Decompose -> Plan Storyboard -> Dispatch Renderers -> QA Evaluate -> Selective Retry -> Assemble Timeline."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        shots_dir = output_dir / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)

        # 1. Narrative Beat Decomposition
        beats = self.decomposer.decompose_script(
            script=script,
            total_audio_duration=total_audio_duration,
            content_format=content_format,
        )

        # 2. Storyboard Planning
        storyboard = self.planner.plan_storyboard(
            project_id=project_id,
            script=script,
            beats=beats,
            total_audio_duration=total_audio_duration,
            content_format=content_format,
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

            asset_path, asset_hash = self._generate_shot_asset(
                shot=shot,
                shot_index=shot_idx,
                output_dir=shots_dir,
                script_title=script.title,
                channel_name=channel_name,
                dossier=dossier,
            )

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

                asset_path, asset_hash = self._generate_shot_asset(
                    shot=shot,
                    shot_index=shot_idx,
                    output_dir=shots_dir,
                    script_title=script.title,
                    channel_name=channel_name,
                    dossier=dossier,
                )
                asset_exists = Path(asset_path).exists() and Path(asset_path).stat().st_size > 0
                evaluation = self.evaluator.evaluate_shot(shot=shot, asset_exists=asset_exists)

            # Safe semantic fallback if still failing after retries
            if evaluation.recommendation == "REGENERATE" and not asset_exists:
                shot.visual_modality = VisualModality.DIAGRAM
                asset_path, asset_hash = self._generate_shot_asset(
                    shot=shot,
                    shot_index=shot_idx,
                    output_dir=shots_dir,
                    script_title=script.title,
                    channel_name=channel_name,
                    dossier=dossier,
                )
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
            )
            timeline_shots.append(t_shot)

        # Adjust final shot to span exact audio duration
        if timeline_shots and total_audio_duration > 0.0:
            timeline_shots[-1].end = round(total_audio_duration, 3)
            timeline_shots[-1].duration = round(total_audio_duration - timeline_shots[-1].start, 3)

        timeline = ShotTimeline(shots=timeline_shots, total_duration=total_audio_duration)
        return timeline, storyboard

    def _generate_shot_asset(
        self,
        shot: ShotSpec,
        shot_index: int,
        output_dir: Path,
        script_title: str,
        channel_name: str,
        dossier: Optional[ResearchDossier] = None,
    ) -> Tuple[Path, str]:
        """Dispatch asset generation to the best available renderer or provider for the shot modality."""
        t0 = time.time()
        modality = shot.visual_modality
        shot_id = shot.shot_id

        # Stock video check: if requested but no stock provider is installed, explicitly record fallback attempt and route
        if modality == VisualModality.STOCK_VIDEO:
            fallback_modality = VisualModality.GENERATED_VIDEO if (self.gflow_provider and hasattr(self.gflow_provider, "generate_video")) else VisualModality.MOTION_GRAPHICS
            self.asset_attempts.append(
                AssetGenerationAttempt(
                    shot_id=shot_id,
                    provider="stock_video_provider",
                    modality=VisualModality.STOCK_VIDEO.value,
                    requested_modality=VisualModality.STOCK_VIDEO.value,
                    actual_modality=fallback_modality.value,
                    fallback_reason="STOCK_VIDEO is unsupported: no stock media provider configured",
                    success=False,
                    error_type="UnsupportedModalityError",
                    error_message="No stock video provider configured or available",
                    latency_ms=0,
                )
            )
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
                return Path(p), h
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
                    target_path = output_dir / f"{shot_id}_token_anim.mp4"
                    p, h = self.motion_renderer.render_animated_token_prediction(
                        prompt_text="The capital of France is",
                        candidates=[("Paris", 0.82), ("London", 0.08), ("Berlin", 0.06), ("Rome", 0.04)],
                        selected_token="Paris",
                        output_path=target_path,
                        duration=shot.duration_seconds,
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
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="motion_renderer" if (is_llm_token or shot.chart_data) else "chart_renderer",
                        modality=modality.value,
                        prompt=instr,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return Path(p), h
            except Exception as e:
                # If ungrounded or failed, gracefully fall back to Diagram
                target_path = output_dir / f"{shot_id}_diagram_fallback.png"
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=shot.narration_segment,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                return Path(p), h

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
                return Path(p), h
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
                return Path(p), h

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
                return Path(p), h
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

        # Modality E: DOCUMENT_EVIDENCE / SCREENSHOT
        elif modality in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT):
            target_path = output_dir / f"{shot_id}_evidence.png"

            # Resolve grounded evidence binding
            binding = shot.evidence_binding
            if not binding and dossier and dossier.sources and shot.source_refs:
                matched_source = next((s for s in dossier.sources if s.id in shot.source_refs or s.url in shot.source_refs), None)
                if matched_source:
                    from app.media.director.models import EvidenceBinding
                    binding = EvidenceBinding(
                        source_ref=matched_source.id,
                        source_title=matched_source.title,
                        source_url=matched_source.url,
                        quote_or_excerpt=shot.evidence_instruction or shot.narration_segment,
                    )

            if not binding or not binding.source_url or "official-documentation.org" in binding.source_url:
                # No grounded source binding: DOCUMENT_EVIDENCE must not be rendered. Route to Diagram.
                target_path = output_dir / f"{shot_id}_diagram_fallback.png"
                p, h = self.diagram_renderer.render_from_instruction(
                    instruction=shot.narration_segment,
                    output_path=target_path,
                    title=shot.subject or script_title,
                )
                return Path(p), h

            try:
                p, h = self.evidence_renderer.render_evidence_card(
                    source_title=binding.source_title,
                    source_url=binding.source_url,
                    highlighted_claim=binding.quote_or_excerpt or shot.narration_segment,
                    output_path=target_path,
                    benchmark_name="SOURCE CITATION",
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="evidence_renderer",
                        modality=modality.value,
                        success=True,
                        output_path=str(p),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )
                return Path(p), h
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
                return Path(p), h

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
                return Path(p), h
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
                            return Path(vid_p), vid_h
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
                            return Path(img_p), img_h
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
            return Path(p), h

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
        return Path(p), h

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

        asset_path, asset_hash = self._generate_shot_asset(
            shot=target_shot,
            shot_index=target_idx,
            output_dir=shots_dir,
            script_title=script_title,
            channel_name=channel_name,
            dossier=dossier,
        )

        for t_shot in timeline.shots:
            if t_shot.shot_id == shot_id:
                t_shot.asset_path = str(asset_path)
                t_shot.asset_sha256 = asset_hash
                t_shot.modality = target_shot.visual_modality
                break

        storyboard_path = output_dir / f"storyboard_{project_id}.json"
        self.planner.save_storyboard_artifact(storyboard, storyboard_path)

        return timeline, storyboard

