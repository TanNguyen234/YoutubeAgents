"""Production media capability checker for FFmpeg, FFprobe, TTS, and storage."""

import os
from pathlib import Path
import shutil
import subprocess
from typing import List, Optional
from pydantic import BaseModel, Field

# Ensure static-ffmpeg binaries are available in PATH if installed
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass


class MediaCapabilities(BaseModel):
    """Runtime capability diagnostics for video production."""

    ffmpeg_available: bool = Field(description="FFmpeg executable is available and executable")
    ffmpeg_version: Optional[str] = Field(default=None, description="Reported FFmpeg version string")
    ffmpeg_path: Optional[str] = Field(default=None, description="Resolved path to ffmpeg binary")
    ffprobe_available: bool = Field(description="FFprobe executable is available and executable")
    ffprobe_version: Optional[str] = Field(default=None, description="Reported FFprobe version string")
    ffprobe_path: Optional[str] = Field(default=None, description="Resolved path to ffprobe binary")
    tts_backend: Optional[str] = Field(default=None, description="Configured active TTS backend")
    tts_available: bool = Field(default=False, description="TTS backend is importable and usable")
    pillow_available: bool = Field(default=False, description="Pillow image library is available")
    output_writable: bool = Field(default=False, description="Target output directory is writable")
    blockers: List[str] = Field(default_factory=list, description="List of unmet production requirements")

    @property
    def is_production_ready(self) -> bool:
        """Return True if all required production capabilities are satisfied."""
        return (
            self.ffmpeg_available
            and self.ffprobe_available
            and self.tts_available
            and self.pillow_available
            and self.output_writable
            and len(self.blockers) == 0
        )


def check_media_capabilities(
    output_dir: Optional[Path] = None,
    tts_backend_name: str = "edge-tts",
) -> MediaCapabilities:
    """Perform real runtime capability inspection by actually executing binaries."""
    # 1. FFmpeg detection & version execution
    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_available = False
    ffmpeg_version = None

    if ffmpeg_path:
        try:
            res = subprocess.run(
                [ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            first_line = res.stdout.splitlines()[0] if res.stdout else ""
            if "ffmpeg version" in first_line:
                ffmpeg_available = True
                ffmpeg_version = first_line.strip()
        except Exception:
            ffmpeg_available = False

    # 2. FFprobe detection & version execution
    ffprobe_path = shutil.which("ffprobe")
    ffprobe_available = False
    ffprobe_version = None

    if ffprobe_path:
        try:
            res = subprocess.run(
                [ffprobe_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            first_line = res.stdout.splitlines()[0] if res.stdout else ""
            if "ffprobe version" in first_line:
                ffprobe_available = True
                ffprobe_version = first_line.strip()
        except Exception:
            ffprobe_available = False

    # 3. TTS backend detection
    tts_available = False
    actual_tts_backend = None
    if tts_backend_name == "edge-tts":
        try:
            import edge_tts  # noqa: F401
            tts_available = True
            actual_tts_backend = "edge-tts"
        except ImportError:
            tts_available = False
            actual_tts_backend = None

    # 4. Pillow detection
    pillow_available = False
    try:
        from PIL import Image  # noqa: F401
        pillow_available = True
    except ImportError:
        pillow_available = False

    # 5. Output directory writability check
    target_out = output_dir or Path("output/projects")
    output_writable = False
    try:
        target_out.mkdir(parents=True, exist_ok=True)
        test_file = target_out / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        output_writable = True
    except Exception:
        output_writable = False

    # 6. Compile blockers
    blockers = []
    if not ffmpeg_available:
        blockers.append("FFmpeg binary not found or failed execution (`ffmpeg -version`).")
    if not ffprobe_available:
        blockers.append("FFprobe binary not found or failed execution (`ffprobe -version`).")
    if not tts_available:
        blockers.append(f"Configured TTS backend '{tts_backend_name}' is not installed/usable.")
    if not pillow_available:
        blockers.append("Pillow library is missing (required for scene visual composition).")
    if not output_writable:
        blockers.append(f"Target output directory '{target_out}' is not writable.")

    return MediaCapabilities(
        ffmpeg_available=ffmpeg_available,
        ffmpeg_version=ffmpeg_version,
        ffmpeg_path=ffmpeg_path,
        ffprobe_available=ffprobe_available,
        ffprobe_version=ffprobe_version,
        ffprobe_path=ffprobe_path,
        tts_backend=actual_tts_backend,
        tts_available=tts_available,
        pillow_available=pillow_available,
        output_writable=output_writable,
        blockers=blockers,
    )
