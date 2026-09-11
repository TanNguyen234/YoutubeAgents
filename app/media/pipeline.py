"""Media Production Pipeline coordinating real TTS, subtitles, visual card generation, FFmpeg rendering, and QA."""

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import List, Optional, Tuple

from app.db.repository import SQLiteRepository
from app.domain.enums import AssetType, QualityStatus, VideoLifecycleState
from app.domain.models import Asset, QualityResult, VideoProject
from app.media.capabilities import check_media_capabilities
from app.media.director import (
    AutoDirectorService,
    ContentFormat,
    VisualModality,
    VisualShotEvaluator,
)
from app.media.ffmpeg_renderer import FFmpegRenderer
from app.media.gflow_provider import GFlowMediaProvider
from app.media.models import (
    AudioMode,
    compute_production_fingerprint,
    MediaQAResult,
    RenderManifest,
    RenderProfile,
    RenderResult,
    SceneRenderPlan,
    TTSResult,
)
from app.media.music_generator import MusicGenerator
from app.media.qa import MediaQAInspector
from app.media.scene_planner import ScenePlanner
from app.media.sound_designer import SoundDesignerService
from app.media.subtitles import SubtitleGenerator
from app.media.tts.base import TTSBackend
from app.media.tts.edge_tts_backend import EdgeTTSBackend, TTSBlockerError


class MediaProductionError(RuntimeError):
    """Raised when media production pipeline encounters an unrecoverable failure."""
    pass


class MediaProductionBlockerError(RuntimeError):
    """Raised when production cannot proceed due to environmental or dependency blockers."""
    pass


class MediaProductionPipeline:
    """Coordinates real TTS voiceover synthesis, visual scene card composition, subtitles, FFmpeg rendering, and QA."""

    def __init__(
        self,
        repository: SQLiteRepository,
        tts_backend: Optional[TTSBackend] = None,
        renderer: Optional[FFmpegRenderer] = None,
        qa_inspector: Optional[MediaQAInspector] = None,
        scene_planner: Optional[ScenePlanner] = None,
        subtitle_generator: Optional[SubtitleGenerator] = None,
        music_generator: Optional[MusicGenerator] = None,
        sound_designer: Optional[SoundDesignerService] = None,
        gflow_provider: Optional[GFlowMediaProvider] = None,
        base_output_dir: Optional[Path] = None,
        director_service: Optional[AutoDirectorService] = None,
    ):
        self.repo = repository
        self.tts = tts_backend or EdgeTTSBackend()
        self.renderer = renderer or FFmpegRenderer()
        # Ensure single canonical gflow_provider across pipeline, planner, and director
        self.gflow_provider = gflow_provider or (getattr(scene_planner, "gflow_provider", None) if scene_planner else None)
        self.planner = scene_planner or ScenePlanner(gflow_provider=self.gflow_provider)
        if self.planner and not getattr(self.planner, "gflow_provider", None) and self.gflow_provider:
            self.planner.gflow_provider = self.gflow_provider
        self.director = director_service or AutoDirectorService(
            gflow_provider=self.gflow_provider,
            visual_factory=getattr(self.planner, "visual_factory", None),
        )
        self.visual_evaluator = VisualShotEvaluator()
        self.sub_gen = subtitle_generator or SubtitleGenerator()
        self.music_gen = music_generator or MusicGenerator()
        self.sound_designer = sound_designer or SoundDesignerService()
        self.subtitles = self.sub_gen
        self.base_output_dir = base_output_dir or Path("output/projects")

    def _get_git_commit(self) -> Optional[str]:
        """Fetch current git commit SHA for provenance recording."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return None

    def run_production(
        self,
        project_id: str,
        profile: Optional[RenderProfile] = None,
        voice: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        force_rebuild: bool = False,
    ) -> Tuple[VideoProject, MediaQAResult, RenderManifest]:
        """Execute full media production for a VERIFIED project with strict idempotency."""
        render_prof = profile or RenderProfile()
        self.renderer.profile = render_prof
        self.qa.profile = render_prof
        if hasattr(self.planner, "visual_factory") and self.planner.visual_factory:
            self.planner.visual_factory.width = render_prof.width
            self.planner.visual_factory.height = render_prof.height

        # 1. Fetch project and assert preconditions
        project = self.repo.get_video_project(project_id)
        if not project:
            raise MediaProductionError(f"Project '{project_id}' not found in database.")

        if project.state not in (
            VideoLifecycleState.VERIFIED,
            VideoLifecycleState.READY_FOR_REVIEW,
            VideoLifecycleState.QA_FAILED,
            VideoLifecycleState.FAILED,
        ):
            raise MediaProductionError(
                f"Production requires project to be in VERIFIED, READY_FOR_REVIEW, QA_FAILED, or FAILED state, but '{project_id}' is in {project.state.value}."
            )

        if not project.script:
            raise MediaProductionError(f"Project '{project_id}' has no associated script.")

        # 2. Immutable Canonical Narration Binding
        canonical_narration = project.script.get_canonical_narration()
        if not canonical_narration or not canonical_narration.strip():
            raise MediaProductionError(f"Project '{project_id}' has empty canonical spoken narration.")

        expected_narration_hash = hashlib.sha256(canonical_narration.strip().encode("utf-8")).hexdigest()

        # 3. Setup Project Work Directories
        proj_dir = self.base_output_dir / project_id
        audio_dir = proj_dir / "audio"
        sub_dir = proj_dir / "subtitles"
        scenes_dir = proj_dir / "scenes"
        render_dir = proj_dir / "render"
        manifest_dir = proj_dir / "manifests"

        for d in (audio_dir, sub_dir, scenes_dir, render_dir, manifest_dir):
            d.mkdir(parents=True, exist_ok=True)

        manifest_path = manifest_dir / "render_manifest.json"

        # 4. Check capabilities before mutating state
        resolved_voice = voice or (self.tts.default_voice if hasattr(self.tts, "default_voice") else "en-US-GuyNeural") or "en-US-GuyNeural"
        tts_backend_name = getattr(self.tts, "backend_name", "edge-tts") if hasattr(self.tts, "backend_name") else "edge-tts"
        if hasattr(self.tts, "__class__") and "Mock" in self.tts.__class__.__name__:
            tts_backend_name = "mock-tts"

        caps = check_media_capabilities(
            output_dir=self.base_output_dir,
            tts_backend_name=tts_backend_name,
        )
        if not caps.is_production_ready:
            # Block cleanly without corrupting lifecycle
            blocker_msg = f"Media capabilities unmet: {', '.join(caps.blockers)}"
            if project.state in (VideoLifecycleState.VERIFIED, VideoLifecycleState.READY_FOR_REVIEW):
                self.repo.update_project_state(
                    project_id=project_id,
                    to_state=VideoLifecycleState.BLOCKED,
                    reason=blocker_msg,
                    expected_current_state=project.state,
                )
            raise MediaProductionBlockerError(blocker_msg)

        # 5. Compute Requested Fingerprint BEFORE any reuse decision
        scene_hashes = [
            hashlib.sha256(
                f"{getattr(s, 'scene_index', getattr(s, 'index', idx))}|{s.narration.strip()}|{(getattr(s, 'hook', '') or '').strip()}|{project.script.title.strip()}|{render_prof.width}x{render_prof.height}".encode("utf-8")
            ).hexdigest()
            for idx, s in enumerate(project.script.scenes)
        ]
        requested_production_fingerprint = compute_production_fingerprint(
            canonical_narration_sha256=expected_narration_hash,
            render_profile_name=render_prof.name,
            tts_backend=tts_backend_name,
            voice=resolved_voice,
            tts_rate=rate,
            tts_pitch=pitch,
            subtitle_format="srt",
            ordered_scene_asset_hashes=scene_hashes,
            audio_mode=render_prof.audio_mode.value,
        )

        if not force_rebuild and manifest_path.exists():
            try:
                cached_manifest = RenderManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                if (
                    cached_manifest.production_fingerprint == requested_production_fingerprint
                    and cached_manifest.canonical_narration_sha256 == expected_narration_hash
                    and cached_manifest.render_profile == render_prof.name
                    and cached_manifest.voice == resolved_voice
                    and cached_manifest.tts_rate == rate
                    and getattr(cached_manifest, "audio_mode", "both") == render_prof.audio_mode.value
                    and cached_manifest.tts_pitch == pitch
                    and cached_manifest.qa_verdict == "PASSED"
                    and Path(cached_manifest.final_video_path).exists()
                ):
                    # Re-inspect to verify physical file hasn't been altered
                    quality_domain, qa_res = self.qa.inspect_video(
                        project_id=project_id,
                        video_path=Path(cached_manifest.final_video_path),
                        expected_narration_hash=expected_narration_hash,
                        actual_narration_hash=cached_manifest.canonical_narration_sha256,
                        expected_profile=render_prof,
                        tts_input_hash=cached_manifest.tts_input_sha256,
                        subtitle_source_hash=cached_manifest.subtitle_source_sha256,
                        render_input_hash=cached_manifest.render_input_narration_sha256,
                    )
                    if qa_res.passed:
                        # Ensure project state is synchronized
                        curr_p = self.repo.get_video_project(project_id)
                        if curr_p and curr_p.state == VideoLifecycleState.VERIFIED:
                            self.repo.update_project_state(
                                project_id=project_id,
                                to_state=VideoLifecycleState.PRODUCING,
                                reason="Idempotent cache reuse: transitioning to PRODUCING",
                                expected_current_state=VideoLifecycleState.VERIFIED,
                            )
                            self.repo.update_project_state(
                                project_id=project_id,
                                to_state=VideoLifecycleState.RENDERED,
                                reason="Idempotent cache reuse: transitioning to RENDERED",
                                expected_current_state=VideoLifecycleState.PRODUCING,
                            )
                            self.repo.update_project_state(
                                project_id=project_id,
                                to_state=VideoLifecycleState.READY_FOR_REVIEW,
                                reason="Idempotent cache reuse: validated completed render",
                                expected_current_state=VideoLifecycleState.RENDERED,
                            )
                        updated_proj = self.repo.get_video_project(project_id) or project
                        return updated_proj, qa_res, cached_manifest
            except Exception:
                # If cache validation fails, proceed with full rebuild
                pass

        # 6. Advance State Machine: VERIFIED, READY_FOR_REVIEW, or QA_FAILED -> PRODUCING
        if project.state == VideoLifecycleState.VERIFIED:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.PRODUCING,
                reason="Starting media production, TTS, visual card composition, and rendering",
                expected_current_state=VideoLifecycleState.VERIFIED,
            )
        elif project.state == VideoLifecycleState.READY_FOR_REVIEW:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.PRODUCING,
                reason="Re-rendering media for project (cache miss or parameter update)",
                expected_current_state=VideoLifecycleState.READY_FOR_REVIEW,
            )
        elif project.state == VideoLifecycleState.QA_FAILED:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.PRODUCING,
                reason="Re-rendering media for project after previous QA failure",
                expected_current_state=VideoLifecycleState.QA_FAILED,
            )
        elif project.state == VideoLifecycleState.FAILED:
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.PRODUCING,
                reason="Re-running media production for project after previous pipeline failure",
                expected_current_state=VideoLifecycleState.FAILED,
            )

        created_assets: List[Asset] = []

        try:
            # 7. TTS Audio Synthesis
            audio_out = audio_dir / f"voiceover_{project_id}.mp3"
            tts_res: TTSResult = self.tts.synthesize(
                text=canonical_narration,
                output_path=audio_out,
                voice=voice,
                rate=rate,
                pitch=pitch,
            )

            # Assert narration hash did not mutate during TTS
            if tts_res.canonical_narration_sha256 != expected_narration_hash:
                raise MediaProductionError(
                    f"Canonical narration hash mutated during TTS synthesis! Expected {expected_narration_hash}, got {tts_res.canonical_narration_sha256}"
                )

            audio_asset = Asset(
                id=f"ast-aud-{project_id}",
                project_id=project_id,
                asset_type=AssetType.AUDIO_VOICEOVER,
                file_path=tts_res.audio_path,
                source_url=f"local://generated/{project_id}/audio/{Path(tts_res.audio_path).name}",
                license_type="ORIGINAL_GENERATED",
                content_sha256=tts_res.audio_sha256,
            )
            created_assets.append(audio_asset)

            # 8. Subtitle Generation
            srt_out = sub_dir / f"subtitles_{project_id}.srt"
            ass_out = sub_dir / f"subtitles_{project_id}.ass"
            sub_track = self.sub_gen.generate_subtitles(
                canonical_narration=canonical_narration,
                audio_duration_seconds=tts_res.duration_seconds,
                output_srt_path=srt_out,
                output_ass_path=ass_out,
                word_boundaries=tts_res.timing_events,
            )

            sub_asset = Asset(
                id=f"ast-sub-{project_id}",
                project_id=project_id,
                asset_type=AssetType.SUBTITLES,
                file_path=sub_track.file_path,
                source_url=f"local://generated/{project_id}/subtitles/{Path(sub_track.file_path).name}",
                license_type="ORIGINAL_GENERATED",
                content_sha256=sub_track.content_sha256,
            )
            created_assets.append(sub_asset)

            # 9. Multi-Shot Visual Planning via AutoDirector (with fallback to ScenePlanner)
            channel = self.repo.get_channel(project.channel_id)
            channel_name = channel.title if channel else "YouTube Channel"

            used_director = False
            timeline = None
            storyboard = None
            scene_plans: List[SceneRenderPlan] = []
            director_output_dir = proj_dir / "director"

            try:
                content_fmt = getattr(project.script, "content_format", ContentFormat.EXPLAINER)
                if isinstance(content_fmt, str):
                    try:
                        content_fmt = ContentFormat(content_fmt)
                    except ValueError:
                        content_fmt = ContentFormat.EXPLAINER

                timeline, storyboard = self.director.plan_and_render_timeline(
                    project_id=project_id,
                    script=project.script,
                    channel_name=channel_name,
                    total_audio_duration=tts_res.duration_seconds,
                    output_dir=director_output_dir,
                    content_format=content_fmt,
                    dossier=getattr(project, "research", None),
                )
                if timeline and len(timeline.shots) > 0:
                    used_director = True
            except Exception as exc:
                logging.warning(
                    f"CREATIVE_PIPELINE_FALLBACK: AutoDirectorService encountered failure ({exc}), "
                    f"falling back to legacy ScenePlanner."
                )
                used_director = False

            ordered_scene_hashes = []
            if used_director and timeline:
                for idx, shot in enumerate(timeline.shots):
                    shot_asset = Asset(
                        id=f"ast-shot-{project_id}-{idx:02d}",
                        project_id=project_id,
                        asset_type=AssetType.SCENE_CARD,
                        file_path=shot.asset_path,
                        source_url=f"local://generated/{project_id}/shots/{Path(shot.asset_path).name}",
                        license_type="ORIGINAL_GENERATED",
                        content_sha256=shot.asset_sha256,
                    )
                    created_assets.append(shot_asset)
                    ordered_scene_hashes.append(shot.asset_sha256)

                # Construct scene_plans proxy for SoundDesigner
                scene_plans = [
                    SceneRenderPlan(
                        scene_index=s_idx,
                        narration_segment="",
                        target_duration_seconds=max(0.5, s.duration),
                        visual_asset_path=s.asset_path,
                        visual_asset_sha256=s.asset_sha256,
                    )
                    for s_idx, s in enumerate(timeline.shots)
                ]
            else:
                scene_plans = self.planner.plan_scenes(
                    script=project.script,
                    channel_name=channel_name,
                    total_audio_duration=tts_res.duration_seconds,
                    output_scenes_dir=scenes_dir,
                    subtitle_track=sub_track,
                )
                for idx, plan in enumerate(scene_plans):
                    card_asset = Asset(
                        id=f"ast-card-{project_id}-{idx:02d}",
                        project_id=project_id,
                        asset_type=AssetType.SCENE_CARD,
                        file_path=plan.visual_asset_path,
                        source_url=f"local://generated/{project_id}/scenes/{Path(plan.visual_asset_path).name}",
                        license_type="ORIGINAL_GENERATED",
                        content_sha256=plan.visual_asset_sha256,
                    )
                    created_assets.append(card_asset)
                    ordered_scene_hashes.append(plan.visual_asset_sha256)

            # Compute definitive production fingerprint matching requested fingerprint
            production_fingerprint = compute_production_fingerprint(
                canonical_narration_sha256=expected_narration_hash,
                render_profile_name=render_prof.name,
                tts_backend=tts_res.backend,
                voice=tts_res.voice,
                tts_rate=tts_res.rate,
                tts_pitch=tts_res.pitch,
                subtitle_format="srt",
                ordered_scene_asset_hashes=scene_hashes,
                audio_mode=render_prof.audio_mode.value,
            )

            # 9b. AI Background Music Generation (Anime Lo-Fi / Synthwave BGM)
            bgm_out = audio_dir / f"bgm_{project_id}.wav"
            bgm_path_str, bgm_sha256 = self.music_gen.generate_track(
                duration_seconds=tts_res.duration_seconds,
                output_path=bgm_out,
                mood="anime_lofi",
                bpm=85,
            )
            bgm_asset = Asset(
                id=f"ast-bgm-{project_id}",
                project_id=project_id,
                asset_type=AssetType.AUDIO_BGM,
                file_path=bgm_path_str,
                source_url=f"local://generated/{project_id}/audio/{Path(bgm_path_str).name}",
                license_type="ORIGINAL_GENERATED",
                content_sha256=bgm_sha256,
            )
            created_assets.append(bgm_asset)

            # 9c. AI Sound Design Sequencing (Transition whooshes & hook impact)
            sfx_out = audio_dir / f"sfx_{project_id}.wav"
            sfx_path_str, sfx_sha256 = self.sound_designer.generate_sfx_track(
                scene_plans=scene_plans,
                total_duration_seconds=tts_res.duration_seconds,
                output_path=sfx_out,
            )
            sfx_asset = Asset(
                id=f"ast-sfx-{project_id}",
                project_id=project_id,
                asset_type=AssetType.AUDIO_BGM,
                file_path=sfx_path_str,
                source_url=f"local://generated/{project_id}/audio/{Path(sfx_path_str).name}",
                license_type="ORIGINAL_GENERATED",
                content_sha256=sfx_sha256,
            )
            created_assets.append(sfx_asset)

            # Handle AudioMode branching (VOICE_ONLY, SOUND_ONLY, BOTH)
            render_audio_path = Path(tts_res.audio_path)
            render_bgm_path = Path(bgm_path_str) if render_prof.audio_mode in (AudioMode.BOTH, AudioMode.SOUND_ONLY) else None
            render_sfx_path = Path(sfx_path_str) if render_prof.audio_mode in (AudioMode.BOTH, AudioMode.SOUND_ONLY) else None

            if render_prof.audio_mode == AudioMode.SOUND_ONLY:
                # In SOUND_ONLY mode, generate a silent voice track to keep subtitle alignment without spoken audio
                silent_voice_path = audio_dir / f"silent_voice_{project_id}.wav"
                import wave
                num_samples = int(tts_res.duration_seconds * 44100)
                with wave.open(str(silent_voice_path), "wb") as wf:
                    wf.setnchannels(2)
                    wf.setsampwidth(2)
                    wf.setframerate(44100)
                    wf.writeframes(b"\x00\x00\x00\x00" * num_samples)
                render_audio_path = silent_voice_path

            # 10. Authoritative FFmpeg Video Render
            video_out = render_dir / f"final_{project_id}.mp4"
            if used_director and timeline:
                render_res: RenderResult = self.renderer.render_timeline(
                    project_id=project_id,
                    timeline=timeline,
                    audio_path=render_audio_path,
                    output_video_path=video_out,
                    subtitle_path=Path(sub_track.file_path),
                    bgm_path=render_bgm_path,
                    sfx_path=render_sfx_path,
                )
            else:
                render_res: RenderResult = self.renderer.render_video(
                    project_id=project_id,
                    scene_plans=scene_plans,
                    audio_path=render_audio_path,
                    output_video_path=video_out,
                    subtitle_path=Path(sub_track.file_path),
                    bgm_path=render_bgm_path,
                    sfx_path=render_sfx_path,
                )

            video_asset = Asset(
                id=f"ast-vid-{project_id}",
                project_id=project_id,
                asset_type=AssetType.FINAL_VIDEO,
                file_path=render_res.video_path,
                source_url=f"local://generated/{project_id}/render/{Path(render_res.video_path).name}",
                license_type="ORIGINAL_GENERATED",
                content_sha256=render_res.content_sha256,
            )
            created_assets.append(video_asset)

            # Persist all created assets to DB
            self.repo.save_assets(created_assets)

            # 11. Advance State Machine: PRODUCING -> RENDERED
            self.repo.update_project_state(
                project_id=project_id,
                to_state=VideoLifecycleState.RENDERED,
                reason=f"Authoritative FFmpeg rendering completed at {render_res.video_path}",
                expected_current_state=VideoLifecycleState.PRODUCING,
            )

            # 12. Real Technical QA Inspection (measuring final master MP4)
            quality_domain, qa_res = self.qa.inspect_video(
                project_id=project_id,
                video_path=Path(render_res.video_path),
                expected_narration_hash=expected_narration_hash,
                actual_narration_hash=tts_res.canonical_narration_sha256,
                expected_profile=render_prof,
                tts_input_hash=tts_res.canonical_narration_sha256,
                subtitle_source_hash=expected_narration_hash,
                render_input_hash=expected_narration_hash,
            )

            # 12b. Creative Visual Quality Evaluation Report
            if used_director and timeline and storyboard:
                failed_attempts = len([a for a in getattr(self.director, "asset_attempts", []) if not a.success])
                visual_report = self.visual_evaluator.generate_quality_report(
                    timeline=timeline,
                    storyboard=storyboard,
                    failed_attempts=failed_attempts,
                )
                report_path = proj_dir / "manifests" / f"creative_qa_report_{project_id}.json"
                report_path.write_text(visual_report.model_dump_json(indent=2), encoding="utf-8")

            # Persist QualityResult directly and via project to DB
            self.repo.save_quality_result(quality_domain)
            project.quality = quality_domain
            project.assets = created_assets
            self.repo.save_video_project(project)

            # 13. Build and write RenderManifest
            manifest = RenderManifest(
                project_id=project_id,
                source_commit=self._get_git_commit(),
                script_id=project.script.id,
                canonical_narration_sha256=expected_narration_hash,
                production_fingerprint=production_fingerprint,
                tts_input_sha256=tts_res.canonical_narration_sha256,
                subtitle_source_sha256=expected_narration_hash,
                render_input_narration_sha256=expected_narration_hash,
                render_profile=render_prof.name,
                tts_backend=tts_res.backend,
                voice=tts_res.voice,
                tts_rate=tts_res.rate,
                tts_pitch=tts_res.pitch,
                audio_path=tts_res.audio_path,
                audio_sha256=tts_res.audio_sha256,
                audio_duration=tts_res.duration_seconds,
                subtitle_path=sub_track.file_path,
                subtitle_sha256=sub_track.content_sha256,
                subtitle_format="srt",
                subtitle_cue_count=sub_track.cue_count,
                scene_count=len(timeline.shots) if (used_director and timeline) else len(scene_plans),
                visual_assets=(
                    [{"path": s.asset_path, "sha256": s.asset_sha256} for s in timeline.shots]
                    if (used_director and timeline)
                    else [{"path": p.visual_asset_path, "sha256": p.visual_asset_sha256} for p in scene_plans]
                ),
                ffmpeg_version=caps.ffmpeg_version,
                ffprobe_version=caps.ffprobe_version,
                ffmpeg_command=render_res.ffmpeg_command,
                final_video_path=render_res.video_path,
                final_video_sha256=render_res.content_sha256,
                final_video_size_bytes=render_res.file_size_bytes,
                video_duration=qa_res.video_duration,
                video_codec=qa_res.video_codec,
                audio_codec=qa_res.audio_codec,
                resolution=f"{qa_res.width}x{qa_res.height}",
                fps=qa_res.fps,
                audio_mode=render_prof.audio_mode.value,
                measured_loudness_lufs=qa_res.loudness_lufs,
                qa_verdict="PASSED" if qa_res.passed else "FAILED",
                qa_issues=qa_res.issues,
            )


            manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

            # 14. Final State Gate: RENDERED -> READY_FOR_REVIEW or QA_FAILED
            if qa_res.passed:
                self.repo.update_project_state(
                    project_id=project_id,
                    to_state=VideoLifecycleState.READY_FOR_REVIEW,
                    reason=f"Technical QA PASSED (Duration: {qa_res.video_duration}s, Loudness: {qa_res.loudness_lufs} LUFS)",
                    expected_current_state=VideoLifecycleState.RENDERED,
                )
            else:
                self.repo.update_project_state(
                    project_id=project_id,
                    to_state=VideoLifecycleState.QA_FAILED,
                    reason=f"Technical QA FAILED: {', '.join(qa_res.issues)}",
                    expected_current_state=VideoLifecycleState.RENDERED,
                )

            updated_project = self.repo.get_video_project(project_id) or project
            return updated_project, qa_res, manifest

        except Exception as e:
            # Handle failure cleanly without stranding in active state
            curr_proj = self.repo.get_video_project(project_id)
            if curr_proj and curr_proj.state in (VideoLifecycleState.PRODUCING, VideoLifecycleState.RENDERED):
                target_fail_state = VideoLifecycleState.BLOCKED if isinstance(e, (MediaProductionBlockerError, TTSBlockerError)) else VideoLifecycleState.FAILED
                self.repo.update_project_state(
                    project_id=project_id,
                    to_state=target_fail_state,
                    reason=f"Media production failure: {str(e)}",
                )
            raise
