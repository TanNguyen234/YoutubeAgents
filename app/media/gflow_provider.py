"""Google Flow (GFlow) MCP/CLI media provider for real AI image and video generation."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image
from pydantic import BaseModel, Field

from app.domain.enums import AssetType
from app.domain.models import Asset


class AssetGenerationAttempt(BaseModel):
    """Structured record of an individual asset generation attempt."""

    shot_id: str = Field(description="Unique shot identifier")
    provider: str = Field(description="Visual provider name (e.g. gflow, local_diagram, local_chart)")
    modality: str = Field(description="Target visual modality")
    attempt: int = Field(default=1, ge=1, description="1-based attempt sequence number")
    prompt: Optional[str] = Field(default=None, description="Prompt or instruction used")
    success: bool = Field(description="Whether generation succeeded")
    output_path: Optional[str] = Field(default=None, description="Output file path if successful")
    error_type: Optional[str] = Field(default=None, description="Error classification category")
    error_message: Optional[str] = Field(default=None, description="Detailed error diagnostic")
    latency_ms: Optional[int] = Field(default=None, ge=0, description="Execution duration in milliseconds")
    requested_modality: Optional[str] = Field(default=None, description="Modality initially requested")
    actual_modality: Optional[str] = Field(default=None, description="Modality actually rendered")
    fallback_reason: Optional[str] = Field(default=None, description="Explanation for fallback if rerouted")


class GFlowError(RuntimeError):
    """Raised when GFlow generation or communication fails."""
    pass


class GFlowBlockerError(RuntimeError):
    """Raised when GFlow is unauthenticated, unavailable, or out of credits."""
    pass


class GFlowMediaProvider:
    """Manages AI visual asset generation through Google Flow CLI/MCP."""

    DEFAULT_GFLOW_PATH = Path("C:/Users/VI TINH THANH AN/.local/bin/gflow.exe")

    def __init__(self, executable_path: Optional[Path] = None, profile: str = "tanntd-2005"):
        self.profile = profile
        if executable_path is not None:
            self.executable = Path(executable_path)
        elif self.DEFAULT_GFLOW_PATH.exists():
            self.executable = self.DEFAULT_GFLOW_PATH
        else:
            found = shutil.which("gflow")
            self.executable = Path(found) if found else self.DEFAULT_GFLOW_PATH

    def check_capabilities(self) -> Dict[str, Any]:
        """Verify gflow executable, session authentication, and available credits."""
        if not self.executable.exists():
            return {
                "available": False,
                "error": f"GFlow executable not found at '{self.executable}'.",
                "credits": 0,
            }

        cmd = [str(self.executable), "credits", "user", "--profile", self.profile, "--json"]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if res.returncode != 0:
                return {
                    "available": False,
                    "error": f"GFlow credits check failed (exit {res.returncode}): {res.stderr.strip()}",
                    "credits": 0,
                }

            # Parse JSON output (skip any leading log lines)
            stdout = res.stdout.strip()
            json_start = stdout.find("{")
            if json_start == -1:
                return {"available": False, "error": "No JSON payload in gflow output", "credits": 0}

            data = json.loads(stdout[json_start:])
            credits = data.get("credits", 0)
            authenticated = data.get("authenticated", False)

            return {
                "available": authenticated and credits > 0,
                "authenticated": authenticated,
                "credits": credits,
                "email": data.get("email"),
                "profile": data.get("profile"),
                "executable": str(self.executable),
            }
        except subprocess.TimeoutExpired:
            return {"available": False, "error": "GFlow probe timed out after 30s", "credits": 0}
        except Exception as e:
            return {"available": False, "error": str(e), "credits": 0}

    def generate_image(
        self,
        prompt: str,
        output_path: Path,
        aspect: str = "9:16",
        model: str = "nano2",
        timeout_seconds: int = 120,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Generate an AI visual using Google Flow Imagen model.

        Returns (file_path, content_sha256, metadata).
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.executable.exists():
            raise GFlowBlockerError(f"GFlow CLI not found at '{self.executable}'.")

        cmd = [
            str(self.executable),
            "image",
            "t2i",
            prompt,
            "-o",
            str(output_path),
            "--model",
            model,
            "--aspect",
            aspect,
            "--profile",
            self.profile,
            "--json",
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
                raise GFlowError(
                    f"GFlow image generation failed (exit {res.returncode}): {res.stderr.strip() or res.stdout.strip()}"
                )

            # Ensure exact target format and dimensions (1080x1920 for 9:16)
            with Image.open(output_path) as img:
                w, h = img.size
                if aspect == "9:16" and (w != 1080 or h != 1920):
                    # Resize / crop to exact target specifications
                    img_ratio = w / h
                    target_ratio = 1080 / 1920
                    if abs(img_ratio - target_ratio) > 0.05:
                        # Crop to center then resize
                        if img_ratio > target_ratio:
                            new_w = int(h * target_ratio)
                            left = (w - new_w) // 2
                            img = img.crop((left, 0, left + new_w, h))
                        else:
                            new_h = int(w / target_ratio)
                            top = (h - new_h) // 2
                            img = img.crop((0, top, w, top + new_h))
                    img = img.resize((1080, 1920), Image.Resampling.LANCZOS)
                    img.save(str(output_path), format="PNG")

            content_bytes = output_path.read_bytes()
            sha256 = hashlib.sha256(content_bytes).hexdigest()

            metadata = {
                "prompt": prompt,
                "model": model,
                "aspect": aspect,
                "file_size": len(content_bytes),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": "gflow",
            }
            return str(output_path), sha256, metadata

        except subprocess.TimeoutExpired:
            raise GFlowError(f"GFlow image generation timed out after {timeout_seconds}s for prompt: '{prompt[:60]}'")
        except Exception as e:
            if isinstance(e, (GFlowError, GFlowBlockerError)):
                raise
            raise GFlowError(f"Unexpected error during GFlow generation: {e}") from e

    def generate_video(
        self,
        prompt: str,
        output_path: Path,
        aspect: str = "9:16",
        model: str = "omni-flash",
        duration: int = 6,
        initial_frame: Optional[Path] = None,
        timeout_seconds: int = 300,
        duration_seconds: Optional[int] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Generate an AI video clip using Google Flow Veo / omni-flash model.

        Supports text-to-video (t2v) and image-to-video (i2v via initial_frame).
        Returns (file_path, content_sha256, metadata).
        """
        if duration_seconds is not None:
            duration = duration_seconds

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.executable.exists():
            raise GFlowBlockerError(f"GFlow CLI not found at '{self.executable}'.")

        if initial_frame:
            initial_frame = Path(initial_frame)
            if not initial_frame.exists():
                raise GFlowError(f"Initial frame for i2v does not exist: {initial_frame}")
            cmd = [
                str(self.executable),
                "video",
                "i2v",
                prompt,
                "--initial-frame",
                str(initial_frame),
                "-o",
                str(output_path),
                "--model",
                model,
                "--aspect",
                aspect,
                "--duration",
                str(duration),
                "--profile",
                self.profile,
                "--json",
            ]
        else:
            cmd = [
                str(self.executable),
                "video",
                "t2v",
                prompt,
                "-o",
                str(output_path),
                "--model",
                model,
                "--aspect",
                aspect,
                "--duration",
                str(duration),
                "--profile",
                self.profile,
                "--json",
            ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
                raise GFlowError(
                    f"GFlow video generation failed (exit {res.returncode}): {res.stderr.strip() or res.stdout.strip()}"
                )

            content_bytes = output_path.read_bytes()
            sha256 = hashlib.sha256(content_bytes).hexdigest()

            metadata = {
                "prompt": prompt,
                "model": model,
                "aspect": aspect,
                "duration": duration,
                "initial_frame": str(initial_frame) if initial_frame else None,
                "file_size": len(content_bytes),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": "gflow",
                "mode": "i2v" if initial_frame else "t2v",
            }
            return str(output_path), sha256, metadata

        except subprocess.TimeoutExpired:
            raise GFlowError(f"GFlow video generation timed out after {timeout_seconds}s for prompt: '{prompt[:60]}'")
        except Exception as e:
            if isinstance(e, (GFlowError, GFlowBlockerError)):
                raise
            raise GFlowError(f"Unexpected error during GFlow video generation: {e}") from e

    def create_asset_record(
        self,
        project_id: str,
        file_path: str,
        content_sha256: str,
        prompt: str,
    ) -> Asset:
        """Create a validated domain Asset record for the generated GFlow visual."""
        return Asset(
            id=f"asset-gflow-{hashlib.sha256(file_path.encode()).hexdigest()[:8]}",
            project_id=project_id,
            asset_type=AssetType.IMAGE,
            file_path=file_path,
            source_url=f"gflow://imagen/nano2?prompt={prompt[:100]}",
            license_type="AI_GENERATED_GFLOW",
            content_sha256=content_sha256,
            created_at=datetime.now(timezone.utc),
        )

    def create_video_asset_record(
        self,
        project_id: str,
        file_path: str,
        content_sha256: str,
        prompt: str,
        model: str = "omni-flash",
    ) -> Asset:
        """Create a validated domain Asset record for the generated GFlow Veo video."""
        return Asset(
            id=f"asset-gflow-vid-{hashlib.sha256(file_path.encode()).hexdigest()[:8]}",
            project_id=project_id,
            asset_type=AssetType.VIDEO_CLIP,
            file_path=file_path,
            source_url=f"gflow://veo/{model}?prompt={prompt[:100]}",
            license_type="AI_GENERATED_GFLOW_VEO",
            content_sha256=content_sha256,
            created_at=datetime.now(timezone.utc),
        )
