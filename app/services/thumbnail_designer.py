"""High-CTR Thumbnail Designer and Compositing Service."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
import uuid
from PIL import Image, ImageDraw, ImageFont

from app.db.repository import SQLiteRepository
from app.domain.models import ThumbnailPackage


class CandidateRenderResult(NamedTuple):
    file_path_16_9: Path
    file_path_9_16: Path
    content_sha256: str
    actual_visual_strategy: str
    visual_fallback_reason: Optional[str]
    layout_metadata: Dict[str, Any]


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
            raise ValueError(f"Project '{project_id}' not found.")

        clean_headline = headline_text.strip().upper()

        path_16_9 = self.output_dir / f"thumb_{project_id}_16_9.jpg"
        self._render_single_thumbnail(
            target_size=(1280, 720),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_16_9,
            is_vertical=False,
            visual_strategy="FOCUS",
        )

        path_9_16 = self.output_dir / f"thumb_{project_id}_9_16.jpg"
        self._render_single_thumbnail(
            target_size=(1080, 1920),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_9_16,
            is_vertical=True,
            visual_strategy="FOCUS",
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
        visual_strategy: Optional[str] = None,
        supporting_image_paths: Optional[List[str]] = None,
    ) -> CandidateRenderResult:
        """Render high-contrast 16:9 and 9:16 thumbnails for a tournament candidate and return paths + metadata."""
        clean_headline = (headline_text or "").strip().upper()

        # Strategy normalization
        norm_strategy = "FOCUS"
        strat_str = (visual_strategy or "").strip().lower()
        if any(k in strat_str for k in ("split", "contrast", "comparison", "benchmark", "versus", "tension")):
            norm_strategy = "SPLIT_CONTRAST"
        elif any(k in strat_str for k in ("crop", "detail", "zoom", "flow", "breakdown", "close_up")):
            norm_strategy = "DETAIL_CROP"
        else:
            norm_strategy = "FOCUS"

        supporting_path = None
        fallback_reason = None
        if norm_strategy == "SPLIT_CONTRAST":
            if supporting_image_paths and len(supporting_image_paths) > 0:
                first_supp = supporting_image_paths[0]
                if first_supp and Path(first_supp).exists():
                    supporting_path = first_supp
            if not supporting_path:
                fallback_reason = "Secondary visual asset unavailable; using dual-panel contrast treatment."

        path_16_9 = self.output_dir / f"thumb_{project_id}_{candidate_id}_16_9.jpg"
        meta_16_9 = self._render_single_thumbnail(
            target_size=(1280, 720),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_16_9,
            is_vertical=False,
            visual_strategy=norm_strategy,
            supporting_bg_path=supporting_path,
        )

        path_9_16 = self.output_dir / f"thumb_{project_id}_{candidate_id}_9_16.jpg"
        meta_9_16 = self._render_single_thumbnail(
            target_size=(1080, 1920),
            headline=clean_headline,
            series_badge=series_badge,
            bg_path=background_image_path,
            output_path=path_9_16,
            is_vertical=True,
            visual_strategy=norm_strategy,
            supporting_bg_path=supporting_path,
        )

        with open(path_16_9, "rb") as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()

        layout_metadata = {
            "requested_strategy": visual_strategy or "FOCUS",
            "actual_strategy": norm_strategy,
            "fallback_reason": fallback_reason,
            "composition_branch": meta_16_9["layout_branch"],
            "meta_16_9": meta_16_9,
            "meta_9_16": meta_9_16,
        }

        return CandidateRenderResult(
            file_path_16_9=path_16_9,
            file_path_9_16=path_9_16,
            content_sha256=sha256,
            actual_visual_strategy=norm_strategy,
            visual_fallback_reason=fallback_reason,
            layout_metadata=layout_metadata,
        )

    def _render_single_thumbnail(
        self,
        target_size: Tuple[int, int],
        headline: str,
        series_badge: Optional[str],
        bg_path: Optional[str],
        output_path: Path,
        is_vertical: bool,
        visual_strategy: str = "FOCUS",
        supporting_bg_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compose individual thumbnail with strategy-driven layout, contrast overlay, and pill typography."""
        width, height = target_size
        layout_branch = "FOCUS_LEFT_PILL"

        # 1. Base Image Generation according to visual strategy
        if visual_strategy == "SPLIT_CONTRAST":
            if supporting_bg_path and Path(supporting_bg_path).exists():
                base = Image.new("RGB", (width, height), color=(15, 23, 42))
                primary_img = self._load_image_safely(bg_path)
                supp_img = self._load_image_safely(supporting_bg_path)

                if not is_vertical:
                    layout_branch = "SPLIT_SIDE_BY_SIDE"
                    half_w = width // 2
                    p_crop = self._crop_to_fill(primary_img or Image.new("RGB", (half_w, height), (15, 23, 42)), half_w, height)
                    s_crop = self._crop_to_fill(supp_img or Image.new("RGB", (width - half_w, height), (30, 41, 59)), width - half_w, height)
                    base.paste(p_crop, (0, 0))
                    base.paste(s_crop, (half_w, 0))
                    draw_div = ImageDraw.Draw(base)
                    draw_div.line([(half_w, 0), (half_w, height)], fill=(255, 215, 0), width=4)
                else:
                    layout_branch = "SPLIT_TOP_BOTTOM"
                    half_h = height // 2
                    p_crop = self._crop_to_fill(primary_img or Image.new("RGB", (width, half_h), (15, 23, 42)), width, half_h)
                    s_crop = self._crop_to_fill(supp_img or Image.new("RGB", (width, height - half_h), (30, 41, 59)), width, height - half_h)
                    base.paste(p_crop, (0, 0))
                    base.paste(s_crop, (0, half_h))
                    draw_div = ImageDraw.Draw(base)
                    draw_div.line([(0, half_h), (width, half_h)], fill=(255, 215, 0), width=4)
            else:
                layout_branch = "SPLIT_PANEL_CONTRAST"
                primary_img = self._load_image_safely(bg_path)
                base = self._crop_to_fill(primary_img or Image.new("RGB", (width, height), (15, 23, 42)), width, height)
                dark_tint = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                draw_tint = ImageDraw.Draw(dark_tint)
                if not is_vertical:
                    half_w = width // 2
                    draw_tint.rectangle([half_w, 0, width, height], fill=(0, 0, 0, 150))
                    base = Image.alpha_composite(base.convert("RGBA"), dark_tint).convert("RGB")
                    draw_div = ImageDraw.Draw(base)
                    draw_div.line([(half_w, 0), (half_w, height)], fill=(255, 255, 255), width=4)
                else:
                    half_h = height // 2
                    draw_tint.rectangle([0, half_h, width, height], fill=(0, 0, 0, 150))
                    base = Image.alpha_composite(base.convert("RGBA"), dark_tint).convert("RGB")
                    draw_div = ImageDraw.Draw(base)
                    draw_div.line([(0, half_h), (width, half_h)], fill=(255, 255, 255), width=4)

        elif visual_strategy == "DETAIL_CROP":
            layout_branch = "DETAIL_ZOOM_BOTTOM_PILL"
            primary_img = self._load_image_safely(bg_path)
            if primary_img:
                src_w, src_h = primary_img.size
                crop_w = max(10, int(src_w / 1.5))
                crop_h = max(10, int(src_h / 1.5))
                left = (src_w - crop_w) // 2
                top = (src_h - crop_h) // 2
                zoomed = primary_img.crop((left, top, left + crop_w, top + crop_h))
                base = self._crop_to_fill(zoomed, width, height)
            else:
                base = Image.new("RGB", (width, height), color=(30, 27, 75))
        else:
            layout_branch = "FOCUS_LEFT_PILL"
            primary_img = self._load_image_safely(bg_path)
            base = self._crop_to_fill(primary_img or Image.new("RGB", (width, height), (15, 23, 42)), width, height)

        # 2. Typography & Pill Banner Overlay
        text_x, text_y = 0, 0
        if headline:
            font_size = 72 if is_vertical else 64
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()

            draw_calc = ImageDraw.Draw(base)
            bbox = draw_calc.textbbox((0, 0), headline, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            # Strategy-specific positioning
            if visual_strategy == "DETAIL_CROP":
                if is_vertical:
                    text_x = (width - text_w) // 2
                    text_y = int(height * 0.65)
                else:
                    text_x = int(width * 0.08)
                    text_y = int(height * 0.68)
            elif visual_strategy == "SPLIT_CONTRAST":
                if is_vertical:
                    text_x = (width - text_w) // 2
                    text_y = int(height * 0.18)
                else:
                    text_x = (width - text_w) // 2
                    text_y = int(height * 0.18)
            else:
                if is_vertical:
                    text_x = (width - text_w) // 2
                    text_y = int(height * 0.35)
                else:
                    text_x = int(width * 0.08)
                    text_y = (height - text_h) // 2

            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw_ov = ImageDraw.Draw(overlay)
            pill_pad_x = 30
            pill_pad_y = 20
            pill_rect = [
                text_x - pill_pad_x,
                text_y - pill_pad_y,
                text_x + text_w + pill_pad_x,
                text_y + text_h + pill_pad_y,
            ]
            draw_ov.rectangle(pill_rect, fill=(0, 0, 0, 200))
            base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")

            draw = ImageDraw.Draw(base)
            draw.text(
                (text_x, text_y),
                headline,
                fill=(255, 255, 255),
                font=font,
                stroke_width=4,
                stroke_fill=(0, 0, 0),
            )

        # 3. Series badge (if present)
        if series_badge:
            draw = ImageDraw.Draw(base)
            try:
                badge_font = ImageFont.truetype("arial.ttf", 32)
            except Exception:
                badge_font = ImageFont.load_default()
            b_text = series_badge.upper()
            b_bbox = draw.textbbox((0, 0), b_text, font=badge_font)
            bw = b_bbox[2] - b_bbox[0]
            bh = b_bbox[3] - b_bbox[1]
            bx, by = 40, 40
            draw.rectangle([bx - 15, by - 10, bx + bw + 15, by + bh + 10], fill=(239, 68, 68))
            draw.text((bx, by), b_text, fill=(255, 255, 255), font=badge_font)

        base.save(output_path, "JPEG", quality=92)
        return {
            "layout_branch": layout_branch,
            "target_size": target_size,
            "text_position": [text_x, text_y] if headline else None,
        }

    def _load_image_safely(self, path_str: Optional[str]) -> Optional[Image.Image]:
        """Safely load and convert image to RGB if file exists."""
        if not path_str:
            return None
        p = Path(path_str)
        if not p.exists() or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            return None
        try:
            with Image.open(p) as img:
                return img.convert("RGB")
        except Exception:
            return None

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
