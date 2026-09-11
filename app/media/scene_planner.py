"""Scene planner aligning script scenes, visual assets, duration distribution, and subtitle cues."""

import hashlib
from pathlib import Path
from typing import Any, List, Optional

from app.domain.models import Scene, Script
from app.media.models import SceneRenderPlan, SubtitleTrack
from app.media.visual_factory import VisualFactory


class ScenePlanningError(RuntimeError):
    """Raised when scene planning fails."""
    pass


class ScenePlanner:
    """Plans video scene visual assets and duration distribution matching real audio duration."""

    def __init__(
        self,
        visual_factory: Optional[VisualFactory] = None,
        gflow_provider: Optional[Any] = None,
    ):
        self.visual_factory = visual_factory or VisualFactory()
        self.gflow_provider = gflow_provider

    def build_cinematic_veo_prompt(
        self,
        scene: Scene,
        scene_index: int,
        total_scenes: int,
        topic_title: str,
    ) -> str:
        """Construct a structured 5-to-7 component cinematic prompt for Google Veo video generation.

        Formula: [Camera Motion] + [Subject] + [Action/Physics] + [Environment/Lighting] + [Style/Texture]
        """
        # 1. Camera Motion
        if scene_index == 0:
            camera_motion = "High-speed dynamic push-in tracking shot, dramatic low-angle Dutch tilt"
        elif scene_index == total_scenes - 1:
            camera_motion = "Elevated sweeping aerial pullback crane shot"
        elif scene_index % 2 == 1:
            camera_motion = "Fluid lateral tracking camera orbit around the focal subject"
        else:
            camera_motion = "Slow cinematic dolly forward with continuous optical parallax"

        # 2. Subject Core
        subject_core = (scene.visual_prompt or scene.hook or scene.narration or topic_title).strip()
        if len(subject_core) > 160:
            subject_core = subject_core[:157] + "..."

        # 3. Action / Physics
        if scene_index == 0:
            action_physics = "kinetic energy burst, floating atmospheric dust particles, high-velocity motion blur"
        else:
            action_physics = "subtle fluid atmospheric movement, drifting embers, realistic environmental motion"

        # 4. Environment / Lighting
        lighting_env = "volumetric god rays, dramatic high-contrast chiaroscuro lighting, moody neon highlights, deep atmospheric depth"

        # 5. Style / Texture
        style_texture = "cinematic photorealistic 8k, hyper-detailed textures, 35mm anamorphic lens bokeh, vertical 9:16 framing, masterpiece, no text, no captions, no watermarks"

        return f"{camera_motion}, featuring {subject_core}. {action_physics}. {lighting_env}. {style_texture}."

    def plan_scenes(
        self,
        script: Script,
        channel_name: str,
        total_audio_duration: float,
        output_scenes_dir: Path,
        subtitle_track: Optional[SubtitleTrack] = None,
    ) -> List[SceneRenderPlan]:
        """Create structured SceneRenderPlan for each scene in the script."""
        if not script or not script.scenes:
            raise ScenePlanningError("Cannot plan scenes for empty script.")
        if total_audio_duration <= 0.0:
            raise ScenePlanningError(f"Total audio duration must be positive ({total_audio_duration}s).")

        scenes = script.scenes
        total_scenes = len(scenes)

        # Calculate word count weights for duration allocation
        scene_words = [max(1, len(s.narration.split())) for s in scenes]
        total_words = sum(scene_words)

        plans: List[SceneRenderPlan] = []
        cur_time = 0.0

        output_scenes_dir.mkdir(parents=True, exist_ok=True)

        for idx, scene in enumerate(scenes):
            # Compute proportional duration
            scene_dur = round(total_audio_duration * (scene_words[idx] / total_words), 3)
            scene_start = cur_time
            scene_end = min(total_audio_duration, cur_time + scene_dur)
            cur_time = scene_end

            # Extract headline from hook or visual_prompt or narration
            headline = scene.hook or scene.visual_prompt or scene.narration
            if len(headline) > 60:
                headline = headline[:57] + "..."

            # Construct professional Google 5-7 component Veo prompt
            cinematic_prompt = self.build_cinematic_veo_prompt(
                scene=scene,
                scene_index=idx,
                total_scenes=total_scenes,
                topic_title=script.title,
            )

            # AI visual generation via GFlow (Veo Video / Imagen Illustration)
            bg_path: Optional[Path] = None
            motion_video_path: Optional[Path] = None

            if self.gflow_provider:
                try:
                    # For Scene 0 (Hook) or video-enabled providers, attempt motion video first
                    if hasattr(self.gflow_provider, "generate_video"):
                        try:
                            raw_video_path = output_scenes_dir / f"veo_clip_{idx:02d}.mp4"
                            vid_file, _, _ = self.gflow_provider.generate_video(
                                prompt=cinematic_prompt,
                                output_path=raw_video_path,
                                duration_seconds=min(10, max(4, int(round(scene_dur)))),
                            )
                            if Path(vid_file).exists() and Path(vid_file).stat().st_size > 0:
                                motion_video_path = Path(vid_file)
                        except Exception:
                            motion_video_path = None

                    # If motion video not generated, fallback to high-fidelity AI image
                    if not motion_video_path and hasattr(self.gflow_provider, "generate_image"):
                        raw_gflow_path = output_scenes_dir / f"veo_art_{idx:02d}.png"
                        gflow_file, _, _ = self.gflow_provider.generate_image(
                            prompt=cinematic_prompt,
                            output_path=raw_gflow_path,
                            aspect="9:16",
                        )
                        bg_path = Path(gflow_file)
                except Exception:
                    bg_path = None
                    motion_video_path = None

            if motion_video_path and motion_video_path.exists():
                file_path = str(motion_video_path)
                card_sha256 = hashlib.sha256(motion_video_path.read_bytes()).hexdigest()
            else:
                card_path = output_scenes_dir / f"scene_{idx:02d}.png"
                file_path, card_sha256 = self.visual_factory.render_anime_scene_frame(
                    scene_index=idx,
                    channel_name=channel_name,
                    topic_title=script.title,
                    scene_headline=headline,
                    output_path=card_path,
                    scene_total=total_scenes,
                    background_image_path=bg_path,
                )

            # Filter matching subtitle cues in this time window
            matched_cues = []
            if subtitle_track and subtitle_track.cues:
                for cue in subtitle_track.cues:
                    if (cue.start_time >= scene_start and cue.start_time < scene_end) or (
                        cue.end_time > scene_start and cue.end_time <= scene_end
                    ):
                        matched_cues.append(cue)

            plans.append(
                SceneRenderPlan(
                    scene_index=idx,
                    narration_segment=scene.narration,
                    target_duration_seconds=scene_dur,
                    visual_asset_path=file_path,
                    visual_asset_sha256=card_sha256,
                    subtitle_cues=matched_cues,
                    transition=scene.transition or ("impact" if idx == 0 else "fade"),
                )
            )

        # Adjust last scene duration to match exact total duration
        if plans:
            allocated = sum(p.target_duration_seconds for p in plans[:-1])
            plans[-1].target_duration_seconds = max(0.5, round(total_audio_duration - allocated, 3))

        return plans
