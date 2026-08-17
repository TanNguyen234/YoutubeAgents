"""Authoritative FFmpeg subprocess renderer for 1080x1920 9:16 video composition."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import List, Optional

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

from app.media.models import RenderProfile, RenderResult, SceneRenderPlan


class RenderError(RuntimeError):
    """Raised when FFmpeg video rendering fails."""
    pass


def escape_ffmpeg_filter_path(file_path: Path) -> str:
    """Escape a file path for use inside FFmpeg filter parameters on Windows and Unix."""
    # Convert to absolute path with forward slashes
    posix_path = file_path.resolve().as_posix()
    # In FFmpeg filter graph, colon ':' in drive letters (e.g. C:) must be escaped as '\:'
    escaped = posix_path.replace(":", "\\:")
    return escaped


class FFmpegRenderer:
    """Renders video scenes, audio narration, and burned subtitles into standard MP4 via FFmpeg."""

    def __init__(self, profile: Optional[RenderProfile] = None):
        self.profile = profile or RenderProfile()

    def _resolve_ffmpeg_binary(self) -> str:
        bin_path = shutil.which("ffmpeg")
        if not bin_path:
            raise RenderError("FFmpeg executable not found in PATH. Pre-flight capabilities must be verified before rendering.")
        return bin_path

    def render_video(
        self,
        project_id: str,
        scene_plans: List[SceneRenderPlan],
        audio_path: Path,
        output_video_path: Path,
        subtitle_path: Optional[Path] = None,
    ) -> RenderResult:
        """Execute authoritative FFmpeg composition to render 1080x1920 MP4."""
        ffmpeg_bin = self._resolve_ffmpeg_binary()

        if not scene_plans:
            raise RenderError(f"Cannot render video for project '{project_id}': no scene plans provided.")
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RenderError(f"Cannot render video for project '{project_id}': audio track missing or empty at {audio_path}.")

        output_video_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = output_video_path.parent
        scenes_dir = work_dir / "rendered_segments"
        scenes_dir.mkdir(parents=True, exist_ok=True)

        executed_commands: List[List[str]] = []

        # 1. Encode normalized video segment for each individual scene
        scene_video_paths: List[Path] = []
        for idx, plan in enumerate(scene_plans):
            seg_path = scenes_dir / f"scene_{idx:02d}.mp4"
            scene_vf = [
                f"fps={self.profile.fps}",
                f"scale={self.profile.width}:{self.profile.height}:force_original_aspect_ratio=decrease",
                f"pad={self.profile.width}:{self.profile.height}:(ow-iw)/2:(oh-ih)/2",
                f"format={self.profile.pixel_format}",
            ]
            seg_cmd = [
                ffmpeg_bin,
                "-y",
                "-loop", "1",
                "-i", str(Path(plan.visual_asset_path).resolve()),
                "-t", f"{plan.target_duration_seconds:.3f}",
                "-vf", ",".join(scene_vf),
                "-c:v", self.profile.video_codec,
                "-preset", "fast",
                "-pix_fmt", self.profile.pixel_format,
                "-r", str(self.profile.fps),
                str(seg_path),
            ]
            executed_commands.append(seg_cmd)
            try:
                subprocess.run(seg_cmd, capture_output=True, text=True, check=True, timeout=60)
            except subprocess.CalledProcessError as e:
                err_msg = e.stderr or e.stdout or str(e)
                raise RenderError(f"FFmpeg scene {idx} segment encoding failed with code {e.returncode}: {err_msg}") from e
            except Exception as e:
                raise RenderError(f"FFmpeg scene {idx} segment encoding failed: {e}") from e

            if not seg_path.exists() or seg_path.stat().st_size == 0:
                raise RenderError(f"Scene {idx} segment file is missing or zero bytes at {seg_path}.")
            scene_video_paths.append(seg_path)

        # 2. Build Concat Demuxer manifest for normalized scene segments
        concat_script_path = work_dir / f"concat_scenes_{project_id}.txt"
        concat_lines = []
        for seg_p in scene_video_paths:
            posix_p = seg_p.resolve().as_posix()
            concat_lines.append(f"file '{posix_p}'")
        concat_script_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        # 3. Build Final Filter Graph (subtitles overlay + audio loudnorm)
        vf_filters: List[str] = []
        if subtitle_path and subtitle_path.exists():
            esc_sub = escape_ffmpeg_filter_path(subtitle_path)
            vf_filters.append(f"subtitles='{esc_sub}'")

        audio_filter_str = f"loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5"
        total_duration = max(0.5, sum(p.target_duration_seconds for p in scene_plans))

        # 4. Construct Final Mux Command
        final_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_script_path),
            "-i", str(audio_path),
        ]
        if vf_filters:
            final_cmd.extend(["-vf", ",".join(vf_filters)])

        final_cmd.extend([
            "-af", audio_filter_str,
            "-c:v", self.profile.video_codec,
            "-preset", "fast",
            "-pix_fmt", self.profile.pixel_format,
            "-r", str(self.profile.fps),
            "-c:a", self.profile.audio_codec,
            "-b:a", self.profile.audio_bitrate,
            "-ar", str(self.profile.audio_sample_rate),
            "-t", f"{total_duration:.3f}",
            str(output_video_path),
        ])
        executed_commands.append(final_cmd)

        try:
            res = subprocess.run(
                final_cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            )
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr or e.stdout or str(e)
            raise RenderError(f"FFmpeg final composition failed with code {e.returncode}: {err_msg}") from e
        except Exception as e:
            raise RenderError(f"FFmpeg final composition failed: {e}") from e

        if not output_video_path.exists() or output_video_path.stat().st_size == 0:
            raise RenderError(f"Rendered video file is missing or zero bytes at {output_video_path}.")

        file_bytes = output_video_path.read_bytes()
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()
        file_size = output_video_path.stat().st_size

        return RenderResult(
            project_id=project_id,
            video_path=str(output_video_path),
            content_sha256=file_sha256,
            file_size_bytes=file_size,
            duration_seconds=total_duration,
            width=self.profile.width,
            height=self.profile.height,
            fps=self.profile.fps,
            ffmpeg_command=final_cmd,
        )
