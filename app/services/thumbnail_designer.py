"""High-CTR Thumbnail Designer and Compositing Service."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import uuid
from PIL import Image, ImageDraw, ImageFont

from app.db.repository import SQLiteRepository
from app.domain.models import ThumbnailPackage


class ThumbnailDesignerService:
    """Composes high-CTR thumbnail packages in 16:9 landscape and 9:16 vertical short formats."""

    def __init__(self, repository: SQLiteRepository, output_dir: Path):
        self.repository = repository
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_thumbnail_package(
        self,
        project_id: str,
        headline_text: str,
        background_image_path: Optional[str] = None,
        series_badge: Optional[str] = None,
    ) -> ThumbnailPackage:
        """Render high-contrast 16:9 and 9:16 thumbnails with safe-zone compliance and persist package."""
        project = self.repository.get_video_project(project_id)
        if not project:
            raise ValueError(f"VideoProject '{project_id}' not found.")

        # Clean headline (2-4 words ideally)
        clean_headline = headline_text.strip().upper()

        # Render 16:9 (1280x720)
        path_16_9 = self.output_dir / f"thumb_{project_id}_16_9.jpg"
        self._render_single_thumbnail(
            target_size=(1280, 720),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_16_9,
            is_vertical=False,
        )

        # Render 9:16 (1080x1920)
        path_9_16 = self.output_dir / f"thumb_{project_id}_9_16.jpg"
        self._render_single_thumbnail(
            target_size=(1080, 1920),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_9_16,
            is_vertical=True,
        )

        # Calculate SHA-256 of the primary thumbnail
        with open(path_16_9, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()

        pkg = ThumbnailPackage(
            id=f"thm-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            file_path_16_9=str(path_16_9),
            file_path_9_16=str(path_9_16),
            headline_text=clean_headline,
            content_sha256=sha256,
            provenance={
                "generator": "ThumbnailDesignerService",
                "background_source": background_image_path or "default_gradient",
                "series_badge": series_badge,
            },
            created_at=datetime.now(timezone.utc),
        )

        self.repository.save_thumbnail_package(pkg)
        return pkg

    def render_candidate_thumbnail(
        self,
        project_id: str,
        candidate_id: str,
        headline_text: Optional[str] = None,
        background_image_path: Optional[str] = None,
        series_badge: Optional[str] = None,
    ) -> Tuple[Path, Path, str]:
        """Render high-contrast 16:9 and 9:16 thumbnails for a tournament candidate and return paths + sha256."""
        clean_headline = (headline_text or "").strip().upper()

        path_16_9 = self.output_dir / f"thumb_{project_id}_{candidate_id}_16_9.jpg"
        self._render_single_thumbnail(
            target_size=(1280, 720),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_16_9,
            is_vertical=False,
        )

        path_9_16 = self.output_dir / f"thumb_{project_id}_{candidate_id}_9_16.jpg"
        self._render_single_thumbnail(
            target_size=(1080, 1920),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_9_16,
            is_vertical=True,
        )

        with open(path_16_9, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()

        return path_16_9, path_9_16, sha256

    def _render_single_thumbnail(
        self,
        target_size: Tuple[int, int],
        headline: str,
        series_badge: Optional[str],
        bg_path: Optional[str],
        output_path: Path,
        is_vertical: bool,
    ) -> None:
        """Compose individual thumbnail with background framing, dark gradient, and pill typography."""
        width, height = target_size

        # 1. Base Image
        base = None
        if bg_path and Path(bg_path).exists() and Path(bg_path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            try:
                with Image.open(bg_path) as src_img:
                    src_rgb = src_img.convert("RGB")
                    # Resize and crop to fill
                    base = self._crop_to_fill(src_rgb, width, height)
            except Exception:
                base = None

        if base is None:
            # Fallback high-contrast dark gradient canvas
            base = Image.new("RGB", (width, height), color=(15, 23, 42))

        # 2. Add dramatic dark overlay on lower or center third to boost contrast
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)

        # 3. Typography setup (if headline is present)
        if headline:
            draw = ImageDraw.Draw(base)
            font_size = 72 if is_vertical else 64
            try:
                # Try system font, fallback to default
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

            # Calculate text bounding box
            bbox = draw.textbbox((0, 0), headline, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            # Strategic positioning:
            # Avoid bottom-right 25% width/height (YouTube duration badge safe zone)
            # Position in upper-middle (vertical) or middle-left (landscape)
            if is_vertical:
                text_x = (width - text_w) // 2
                text_y = int(height * 0.35)
            else:
                text_x = int(width * 0.1)
                text_y = (height - text_h) // 2

            # Draw dark high-contrast pill banner behind headline
            pill_pad_x = 30
            pill_pad_y = 20
            pill_rect = [
                text_x - pill_pad_x,
                text_y - pill_pad_y,
                text_x + text_w + pill_pad_x,
                text_y + text_h + pill_pad_y,
            ]
            draw_ov.rectangle(pill_rect, fill=(0, 0, 0, 200))

            # Composite overlay
            base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(base)

            # Draw headline with bold white text and black stroke
            draw.text(
                (text_x, text_y),
                headline,
                fill=(255, 255, 255),
                font=font,
                stroke_width=4,
                stroke_fill=(0, 0, 0),
            )
        else:
            # Composite minimal overlay if no headline
            base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(base)

        # 4. Draw optional Series Badge (top-left safe zone)
        if series_badge:
            draw = ImageDraw.Draw(base)
            try:
                badge_font = ImageFont.truetype("arial.ttf", 32)
            except Exception:
                badge_font = ImageFont.load_default()
            badge_text = series_badge.upper()
            b_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            bw = b_bbox[2] - b_bbox[0]
            bh = b_bbox[3] - b_bbox[1]
            bx = 40
            by = 40
            draw.rectangle(
                [bx - 15, by - 10, bx + bw + 15, by + bh + 10],
                fill=(239, 68, 68),  # YouTube Red accent
            )
            draw.text((bx, by), badge_text, fill=(255, 255, 255), font=badge_font)

        # Save to disk
        base.save(output_path, "JPEG", quality=92)


    def _crop_to_fill(self, img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        """Crop and scale image to fill target dimensions without aspect distortion."""
        src_w, src_h = img.size
        scale = max(target_w / src_w, target_h / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))
