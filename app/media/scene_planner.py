"""Scene planner aligning script scenes, visual assets, duration distribution, and subtitle cues."""

from pathlib import Path
from typing import List, Optional

from app.domain.models import Script
from app.media.models import SceneRenderPlan, SubtitleTrack
from app.media.visual_factory import VisualFactory


class ScenePlanningError(RuntimeError):
    """Raised when scene planning fails."""
    pass


class ScenePlanner:
    """Plans video scene visual assets and duration distribution matching real audio duration."""

    def __init__(self, visual_factory: Optional[VisualFactory] = None):
        self.visual_factory = visual_factory or VisualFactory()

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

            card_path = output_scenes_dir / f"scene_{idx:02d}.png"
            file_path, card_sha256 = self.visual_factory.render_scene_card(
                scene_index=idx,
                channel_name=channel_name,
                topic_title=script.title,
                scene_headline=headline,
                output_path=card_path,
                scene_total=total_scenes,
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
                    transition=scene.transition or "fade",
                )
            )

        # Adjust last scene duration to match exact total duration
        if plans:
            allocated = sum(p.target_duration_seconds for p in plans[:-1])
            plans[-1].target_duration_seconds = max(0.5, round(total_audio_duration - allocated, 3))

        return plans
