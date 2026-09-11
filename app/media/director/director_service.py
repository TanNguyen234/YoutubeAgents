"""Auto Director Service coordinating multi-shot decomposition, modality dispatch, and timeline composition."""

import hashlib
from pathlib import Path
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
    VisualIntent,
    VisualModality,
)
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
    ):
        self.profile = profile or ChannelCreativeProfile()
        self.decomposer = decomposer or BeatDecomposer()
        self.router = router or VisualModalityRouter(profile=self.profile)
        self.planner = planner or StoryboardPlanner(router=self.router, profile=self.profile)
        self.visual_factory = visual_factory or VisualFactory()
        self.gflow_provider = gflow_provider

        # Renderers
        self.diagram_renderer = DiagramRenderer()
        self.chart_renderer = ChartRenderer()
        self.motion_renderer = MotionGraphicsRenderer()
        self.evidence_renderer = EvidenceRenderer()

        # Audit logs
        self.asset_attempts: List[AssetGenerationAttempt] = []

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
        """Execute full director workflow: Decompose -> Plan Storyboard -> Dispatch Renderers -> Assemble Timeline."""
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

        # 3. Render and assemble each shot on the timeline
        timeline_shots: List[TimelineShot] = []
        cur_time = 0.0

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
            target_path = output_dir / f"{shot_id}_chart.png"
            instr = shot.chart_instruction or shot.narration_segment
            try:
                p, h = self.chart_renderer.render_from_instruction(
                    instruction=instr,
                    output_path=target_path,
                    title=shot.headline_text or "Data Analysis",
                )
                self.asset_attempts.append(
                    AssetGenerationAttempt(
                        shot_id=shot_id,
                        provider="chart_renderer",
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
                        provider="chart_renderer",
                        modality=modality.value,
                        prompt=instr,
                        success=False,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        latency_ms=int((time.time() - t0) * 1000),
                    )
                )

        # Modality C: CODE_ANIMATION / UI_SIMULATION
        elif modality in (VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION):
            target_path = output_dir / f"{shot_id}_terminal.png"
            cmd_text = shot.code_instruction or shot.screen_instruction or shot.narration_segment
            # Clean command
            cmd_clean = re.sub(r"^[^:]+:\s*", "", cmd_text)
            try:
                p, h = self.motion_renderer.render_code_terminal(
                    command=cmd_clean[:40],
                    output_lines=[
                        "Resolving dependencies...",
                        "State verification: PASSED",
                        f"Target: {shot.subject or 'system'}",
                        "Executing pipeline stage...",
                        "Done in 14.2ms. Status: OK",
                    ],
                    output_path=target_path,
                    window_title=f"terminal — {shot.subject or 'engine'}",
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
            try:
                p, h = self.motion_renderer.render_before_after_comparison(
                    title=shot.subject or "Comparative Analysis",
                    before_label="Legacy Architecture",
                    before_points=["Global lock contention", "Blocking concurrent readers", "High latency spikes"],
                    after_label="Modern Solution",
                    after_points=["Lock-free concurrent reads", "Sequential log append (WAL)", "Sub-millisecond latency"],
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
            src_url = "https://official-documentation.org"
            src_title = script_title
            if dossier and dossier.sources:
                src_url = dossier.sources[0].url
                src_title = dossier.sources[0].title
            try:
                p, h = self.evidence_renderer.render_evidence_card(
                    source_title=src_title,
                    source_url=src_url,
                    highlighted_claim=shot.evidence_instruction or shot.narration_segment,
                    output_path=target_path,
                    benchmark_name="VERIFIED RESULT",
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
            try:
                p, h = self.motion_renderer.render_stat_callout(
                    big_stat=shot.headline_text or "10x FASTER",
                    label=shot.action or "Architecture Win",
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
        elif modality in (VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE, VisualModality.STOCK_VIDEO):
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

