"""Video and image candidate frame sampler for representative visual QA inspection."""

import hashlib
import logging
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

from app.media.acquisition.models import VisualAssetCandidate
from app.media.semantic_qa.models import (
    CandidateVisualSample,
    FFmpegUnavailableError,
    FFprobeFailedError,
    FrameExtractionFailedError,
    InvalidVideoError,
)

logger = logging.getLogger(__name__)

# Static image extensions supported directly
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv"}


class VideoFrameSampler:
    """Samples representative frames from video candidates or wraps static image candidates."""

    def __init__(self, temp_dir: Optional[Path] = None):
        self.temp_dir = temp_dir or Path(os.environ.get("TEMP", "/tmp")) / "youtube_agents_qa_frames"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _compute_file_sha256(path: Path | str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _get_video_duration(self, video_path: Path) -> float:
        """Measure precise video duration using ffprobe. Fails closed with typed error on failure."""
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            raise FFmpegUnavailableError("FFMPEG_UNAVAILABLE: ffprobe executable not found on PATH")
        try:
            cmd = [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            val = float(res.stdout.strip())
            if val <= 0.0:
                raise InvalidVideoError(f"INVALID_VIDEO: Video reported non-positive duration ({val}s): {video_path}")
            return val
        except subprocess.CalledProcessError as e:
            raise FFprobeFailedError(f"FFPROBE_FAILED: ffprobe failed for {video_path}: {e.stderr or e}") from e
        except ValueError as e:
            raise InvalidVideoError(f"INVALID_VIDEO: Could not parse video duration from ffprobe: {e}") from e
        except Exception as e:
            if isinstance(e, (FFmpegUnavailableError, FFprobeFailedError, InvalidVideoError)):
                raise
            raise FFprobeFailedError(f"FFPROBE_FAILED: {e}") from e

    def sample_candidate(
        self,
        candidate: VisualAssetCandidate,
        shot_id: str,
    ) -> CandidateVisualSample:
        """Extract representative frames for a candidate asset.

        Static images: returns the image directly.
        Videos: samples real decoded frames at 25%, 50%, and 75% of measured duration.
        Fails closed with typed error if real frames cannot be extracted.
        """
        path = Path(candidate.file_path)
        if not path.exists() or path.stat().st_size == 0:
            raise InvalidVideoError(f"INVALID_VIDEO: Candidate file does not exist or is empty: {path}")

        ext = path.suffix.lower()

        # 1. Static Image Candidate
        if ext in IMAGE_EXTENSIONS:
            sha = candidate.content_sha256 or self._compute_file_sha256(path)
            return CandidateVisualSample(
                candidate_id=candidate.candidate_id,
                sample_paths=[str(path.resolve())],
                sample_sha256s=[sha],
                sampling_method="direct_image",
            )

        # 2. Video Candidate
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise FFmpegUnavailableError("FFMPEG_UNAVAILABLE: ffmpeg executable not found on PATH")

        duration = candidate.duration_seconds or self._get_video_duration(path)
        if duration <= 0.0:
            raise InvalidVideoError(f"INVALID_VIDEO: Invalid video duration ({duration}s) for {path}")

        # Quarter, half, and three-quarter positions (never 0.0 to avoid black intro frames)
        ratios = [0.25, 0.50, 0.75]
        timestamps = [round(duration * r, 3) for r in ratios]

        sample_paths: List[str] = []
        sample_shas: List[str] = []

        for idx, ts in enumerate(timestamps):
            out_name = f"{shot_id}_{candidate.candidate_id}_f{idx}_{int(ts * 1000)}.png"
            out_path = self.temp_dir / out_name

            try:
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-ss", str(ts),
                    "-i", str(path),
                    "-vframes", "1",
                    "-q:v", "2",
                    str(out_path),
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
            except Exception as e:
                raise FrameExtractionFailedError(
                    f"FRAME_SAMPLING_FAILED: FFmpeg frame extraction failed at timestamp {ts}s for {path}: {e}"
                ) from e

            # Strictly require real extracted frame file
            if not out_path.exists() or out_path.stat().st_size == 0:
                raise FrameExtractionFailedError(
                    f"FRAME_SAMPLING_FAILED: Extracted frame does not exist or is empty: {out_path}"
                )

            sha = self._compute_file_sha256(out_path)
            sample_paths.append(str(out_path.resolve()))
            sample_shas.append(sha)

        return CandidateVisualSample(
            candidate_id=candidate.candidate_id,
            sample_paths=sample_paths,
            sample_sha256s=sample_shas,
            sampling_method="ffmpeg_25_50_75",
        )

    def cleanup_samples(self, sample: CandidateVisualSample) -> None:
        """Remove temporary sampled frames after evaluation."""
        if sample.sampling_method == "direct_image":
            return  # Never delete original static images!

        for p_str in sample.sample_paths:
            try:
                p = Path(p_str)
                if p.exists() and self.temp_dir in p.parents:
                    p.unlink(missing_ok=True)
            except Exception as e:
                logger.debug("Failed to clean temporary sample %s: %s", p_str, e)
