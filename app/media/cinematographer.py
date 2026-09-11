"""Cinematographer Service providing dynamic camera motion (Ken Burns) and BGM audio ducking."""

import math
from pathlib import Path
from typing import List, Optional

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass



class CinematographerService:
    """Calculates cinematic FFmpeg camera motion and audio ducking filter graphs."""

    def build_ken_burns_filter(
        self,
        duration_seconds: float,
        fps: int = 30,
        width: int = 1080,
        height: int = 1920,
        motion_type: str = "zoom_in",
    ) -> str:
        """Construct FFmpeg zoompan filter string for subtle, fluid camera drift.

        Supported motion types:
        - 'zoom_in': Slow smooth zoom towards center.
        - 'zoom_out': Slow pull back from center.
        - 'pan_left': Gentle horizontal drift to the left.
        - 'pan_right': Gentle horizontal drift to the right.
        """
        frames = max(1, int(math.ceil(duration_seconds * fps)))

        if motion_type == "zoom_in":
            # Start at 1.0, zoom slowly to 1.12
            zoom_expr = "min(pzoom+0.0008,1.12)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion_type == "zoom_out":
            # Start zoomed at 1.12, pull back to 1.0
            zoom_expr = "max(1.12-on*0.0008,1.0)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion_type == "pan_left":
            zoom_expr = "1.08"
            x_expr = f"max(0, (iw-iw/zoom)*(1-on/{frames}))"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion_type == "pan_right":
            zoom_expr = "1.08"
            x_expr = f"min(iw-iw/zoom, (iw-iw/zoom)*(on/{frames}))"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            # Static fallback
            zoom_expr = "1.0"
            x_expr = "0"
            y_expr = "0"

        return (
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={frames}:s={width}x{height}:fps={fps}"
        )

    def build_ducked_audio_complex_filter(
        self,
        voiceover_input_index: int = 0,
        bgm_input_index: int = 1,
        ducking_volume: float = 0.15,
        total_duration_seconds: Optional[float] = None,
    ) -> str:
        """Construct FFmpeg filter_complex graph to duck BGM under primary voiceover."""
        # Duck BGM volume and mix with voiceover
        dur_filter = f",atrim=0:{total_duration_seconds:.2f}" if total_duration_seconds else ""
        return (
            f"[{bgm_input_index}:a]volume={ducking_volume:.2f}{dur_filter}[bgm_ducked];"
            f"[{voiceover_input_index}:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )

    def generate_synthetic_whoosh_sfx(self, output_wav: Path, duration: float = 0.25) -> Path:
        """Synthesize a clean whoosh sound effect for scene transitions using native FFmpeg filters."""
        import subprocess
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        lavfi_expr = (
            f"anoisesrc=d={duration:.2f}:c=white,"
            f"bandpass=f=850:width_type=h:w=650,"
            f"afade=t=in:ss=0:d=0.08,afade=t=out:st=0.12:d=0.13,volume=0.35"
        )
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", lavfi_expr,
            "-ac", "2", "-ar", "44100", str(output_wav)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_wav

    def generate_synthetic_impact_sfx(self, output_wav: Path, duration: float = 0.20) -> Path:
        """Synthesize a punchy impact sound effect for dramatic reveal cues using native FFmpeg filters."""
        import subprocess
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        lavfi_expr = f"sine=f=110:d={duration:.2f},afade=t=out:st=0.03:d=0.17,volume=0.40"
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", lavfi_expr,
            "-ac", "2", "-ar", "44100", str(output_wav)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_wav


