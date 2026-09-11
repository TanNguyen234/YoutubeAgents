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

from app.media.cinematographer import CinematographerService
from app.media.models import RenderProfile, RenderResult, SceneRenderPlan


class RenderError(RuntimeError):
    """Raised when FFmpeg video rendering fails."""
    pass


def escape_ffmpeg_filter_path(file_path: Path) -> str:
    """Escape a file path for use inside FFmpeg filter parameters on Windows and Unix."""
    posix_path = file_path.resolve().as_posix()
    escaped = posix_path.replace("'", "\\'").replace(":", "\\:")
    return escaped


class FFmpegRenderer:
    """Renders video scenes, audio narration, and burned subtitles into standard MP4 via FFmpeg."""

    def __init__(
        self,
        profile: Optional[RenderProfile] = None,
        cinematographer: Optional[CinematographerService] = None,
    ):
        self.profile = profile or RenderProfile()
        self.cinematographer = cinematographer or CinematographerService()

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
        bgm_path: Optional[Path] = None,
        sfx_path: Optional[Path] = None,
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
        motions = ["zoom_in", "pan_right", "zoom_out", "pan_left"]

        for idx, plan in enumerate(scene_plans):
            seg_path = scenes_dir / f"scene_{idx:02d}.mp4"
            asset_path = Path(plan.visual_asset_path).resolve()
            is_video = asset_path.suffix.lower() in [".mp4", ".mov", ".webm", ".mkv", ".m4v"]

            if is_video:
                scene_vf = [
                    f"fps={self.profile.fps}",
                    f"scale={self.profile.width}:{self.profile.height}:force_original_aspect_ratio=decrease",
                    f"pad={self.profile.width}:{self.profile.height}:(ow-iw)/2:(oh-ih)/2",
                    f"format={self.profile.pixel_format}",
                ]
                seg_cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-stream_loop", "-1",
                    "-i", str(asset_path),
                    "-t", f"{plan.target_duration_seconds:.3f}",
                    "-vf", ",".join(scene_vf),
                    "-c:v", self.profile.video_codec,
                    "-preset", "ultrafast",
                    "-pix_fmt", self.profile.pixel_format,
                    "-r", str(self.profile.fps),
                    "-an",
                    "-threads", "2",
                    str(seg_path),
                ]
            else:
                # Kinetic Ken Burns camera motion on still images
                chosen_motion = motions[idx % len(motions)]
                kb_filter = self.cinematographer.build_ken_burns_filter(
                    duration_seconds=plan.target_duration_seconds,
                    fps=self.profile.fps,
                    width=self.profile.width,
                    height=self.profile.height,
                    motion_type=chosen_motion,
                )
                scene_vf = [
                    f"scale={self.profile.width}:{self.profile.height}:force_original_aspect_ratio=increase",
                    f"crop={self.profile.width}:{self.profile.height}",
                    kb_filter,
                    f"format={self.profile.pixel_format}",
                ]
                seg_cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-loop", "1",
                    "-i", str(asset_path),
                    "-t", f"{plan.target_duration_seconds:.3f}",
                    "-vf", ",".join(scene_vf),
                    "-c:v", self.profile.video_codec,
                    "-preset", "ultrafast",
                    "-pix_fmt", self.profile.pixel_format,
                    "-r", str(self.profile.fps),
                    "-threads", "2",
                    str(seg_path),
                ]
            executed_commands.append(seg_cmd)
            try:
                subprocess.run(
                    seg_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=True,
                    timeout=60,
                )
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
            escaped_p = posix_p.replace("'", "'\\''")
            concat_lines.append(f"file '{escaped_p}'")
        concat_script_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        total_duration = max(0.5, sum(p.target_duration_seconds for p in scene_plans))

        # 3. Build Composition Command
        final_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_script_path),
            "-i", str(audio_path),
        ]

        has_bgm = bgm_path and Path(bgm_path).exists()
        has_sfx = sfx_path and Path(sfx_path).exists()
        has_sub = subtitle_path and Path(subtitle_path).exists()

        curr_input_idx = 2
        bgm_input_idx = None
        sfx_input_idx = None

        if has_bgm:
            final_cmd.extend(["-i", str(Path(bgm_path).resolve())])
            bgm_input_idx = curr_input_idx
            curr_input_idx += 1

        if has_sfx:
            final_cmd.extend(["-i", str(Path(sfx_path).resolve())])
            sfx_input_idx = curr_input_idx
            curr_input_idx += 1

        filter_complex_parts: List[str] = []

        # Video filter branch (burn subtitles)
        if has_sub:
            esc_sub = escape_ffmpeg_filter_path(subtitle_path)
            if subtitle_path.suffix.lower() == ".ass":
                filter_complex_parts.append(f"[0:v]ass='{esc_sub}'[vout]")
            else:
                filter_complex_parts.append(f"[0:v]subtitles='{esc_sub}'[vout]")
            video_map = "[vout]"
        else:
            video_map = "0:v"

        # Audio filter branch (3-stem mixing: Voiceover [1:a], Ducked BGM, SFX)
        raw_mode = getattr(self.profile, "audio_mode", "both")
        mode_str = raw_mode.value.lower() if hasattr(raw_mode, "value") else str(raw_mode).lower()
        is_sound_only = "sound_only" in mode_str

        if is_sound_only:
            if has_bgm and has_sfx:
                audio_fc = (
                    f"[1:a]volume=0.001[silence];"
                    f"[{bgm_input_idx}:a]volume=1.5,aloop=loop=-1:size=2e+09[bgm_loop];"
                    f"[{sfx_input_idx}:a]volume=1.0[sfx_vol];"
                    f"[silence][bgm_loop][sfx_vol]amix=inputs=3:duration=first:dropout_transition=0:weights=0.001 1.5 1.0[a_mixed];"
                    f"[a_mixed]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5:linear=true[aout]"
                )
            elif has_bgm:
                audio_fc = (
                    f"[1:a]volume=0.001[silence];"
                    f"[{bgm_input_idx}:a]volume=1.5,aloop=loop=-1:size=2e+09[bgm_loop];"
                    f"[silence][bgm_loop]amix=inputs=2:duration=first:dropout_transition=0:weights=0.001 1.5[a_mixed];"
                    f"[a_mixed]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5:linear=true[aout]"
                )
            elif has_sfx:
                audio_fc = (
                    f"[1:a]volume=0.001[silence];"
                    f"[{sfx_input_idx}:a]volume=1.5[sfx_vol];"
                    f"[silence][sfx_vol]amix=inputs=2:duration=first:dropout_transition=0:weights=0.001 1.5[a_mixed];"
                    f"[a_mixed]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5:linear=true[aout]"
                )
            else:
                audio_fc = f"[1:a]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5:linear=true[aout]"
            filter_complex_parts.append(audio_fc)
            audio_map = "[aout]"
        elif has_bgm and has_sfx:
            audio_fc = (
                f"[{bgm_input_idx}:a]volume=0.20,aloop=loop=-1:size=2e+09[bgm_loop];"
                f"[bgm_loop][1:a]sidechaincompress=threshold=0.08:ratio=4:attack=50:release=300[ducked_bgm];"
                f"[{sfx_input_idx}:a]volume=0.35[sfx_vol];"
                f"[1:a][ducked_bgm][sfx_vol]amix=inputs=3:duration=first:dropout_transition=2[a_mixed];"
                f"[a_mixed]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5[aout]"
            )
            filter_complex_parts.append(audio_fc)
            audio_map = "[aout]"
        elif has_bgm and not has_sfx:
            audio_fc = (
                f"[{bgm_input_idx}:a]volume=0.22,aloop=loop=-1:size=2e+09[bgm_loop];"
                f"[bgm_loop][1:a]sidechaincompress=threshold=0.08:ratio=4:attack=50:release=300[ducked_bgm];"
                f"[1:a][ducked_bgm]amix=inputs=2:duration=first:dropout_transition=2[a_mixed];"
                f"[a_mixed]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5[aout]"
            )
            filter_complex_parts.append(audio_fc)
            audio_map = "[aout]"
        elif not has_bgm and has_sfx:
            audio_fc = (
                f"[{sfx_input_idx}:a]volume=0.35[sfx_vol];"
                f"[1:a][sfx_vol]amix=inputs=2:duration=first:dropout_transition=2[a_mixed];"
                f"[a_mixed]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5[aout]"
            )
            filter_complex_parts.append(audio_fc)
            audio_map = "[aout]"
        elif has_sub:
            filter_complex_parts.append(f"[1:a]loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5[aout]")
            audio_map = "[aout]"
        else:
            audio_map = "1:a"

        if filter_complex_parts:
            final_cmd.extend([
                "-filter_complex", ";".join(filter_complex_parts),
                "-map", video_map,
                "-map", audio_map,
            ])
            if has_sub:
                final_cmd.extend([
                    "-c:v", self.profile.video_codec,
                    "-preset", "ultrafast",
                    "-pix_fmt", self.profile.pixel_format,
                    "-r", str(self.profile.fps),
                ])
            else:
                final_cmd.extend([
                    "-c:v", "copy",
                ])
        else:
            final_cmd.extend([
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-af", f"loudnorm=I={self.profile.target_loudness_lufs}:LRA=11:TP=-1.5",
            ])

        final_cmd.extend([
            "-c:a", self.profile.audio_codec,
            "-b:a", self.profile.audio_bitrate,
            "-ar", str(self.profile.audio_sample_rate),
            "-t", f"{total_duration:.3f}",
            "-threads", "2",
            str(output_video_path),
        ])
        executed_commands.append(final_cmd)

        try:
            res = subprocess.run(
                final_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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
