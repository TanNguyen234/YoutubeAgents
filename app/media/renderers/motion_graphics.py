"""Motion graphics renderer for before/after comparisons, stat callouts, terminal code, and timelines."""

import hashlib
from pathlib import Path
import re
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


class MotionGraphicsRenderer:
    """Renders 1080x1920 9:16 visual components for comparisons, code/terminal simulations, and stat callouts."""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.width = width
        self.height = height

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            try:
                return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
            except Exception:
                return ImageFont.load_default()

    def render_before_after_comparison(
        self,
        title: str,
        before_label: str,
        before_points: List[str],
        after_label: str,
        after_points: List[str],
        output_path: Path,
    ) -> Tuple[str, str]:
        """Render side-by-side or stacked Before vs After comparative architecture card."""
        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Header Badge
        header_font = self._get_font(28)
        draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=(244, 63, 94), width=2)
        draw.text((self.width // 2, 195), f"⚖️ ARCHITECTURE COMPARISON // {title.upper()[:26]}", font=header_font, fill=(244, 63, 94), anchor="mm")

        # Top Card: BEFORE (Red / Slate)
        top_y1, top_y2 = 320, 720
        draw.rounded_rectangle([100, top_y1, self.width - 100, top_y2], radius=24, fill=(30, 20, 30), outline=(239, 68, 68), width=2)

        tag_f = self._get_font(24)
        draw.rounded_rectangle([130, top_y1 + 24, 300, top_y1 + 68], radius=10, fill=(239, 68, 68))
        draw.text((215, top_y1 + 46), f"❌ {before_label.upper()}", font=tag_f, fill=(255, 255, 255), anchor="mm")

        item_f = self._get_font(30)
        cur_y = top_y1 + 110
        for pt in before_points[:3]:
            draw.text((150, cur_y), f"•  {pt[:40]}", font=item_f, fill=(226, 232, 240), anchor="ls")
            cur_y += 70

        # VS Badge in middle
        vs_f = self._get_font(28)
        draw.ellipse([self.width // 2 - 40, 750, self.width // 2 + 40, 830], fill=(15, 23, 42), outline=(255, 255, 255), width=2)
        draw.text((self.width // 2, 790), "VS", font=vs_f, fill=(255, 255, 255), anchor="mm")

        # Bottom Card: AFTER (Green / Emerald)
        bot_y1, bot_y2 = 860, 1260
        draw.rounded_rectangle([100, bot_y1, self.width - 100, bot_y2], radius=24, fill=(16, 36, 30), outline=(16, 185, 129), width=2)

        draw.rounded_rectangle([130, bot_y1 + 24, 300, bot_y1 + 68], radius=10, fill=(16, 185, 129))
        draw.text((215, bot_y1 + 46), f"✅ {after_label.upper()}", font=tag_f, fill=(15, 23, 42), anchor="mm")

        cur_y = bot_y1 + 110
        for pt in after_points[:3]:
            draw.text((150, cur_y), f"•  {pt[:40]}", font=item_f, fill=(240, 253, 244), anchor="ls")
            cur_y += 70

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256

    def render_code_terminal(
        self,
        command: str,
        output_lines: List[str],
        output_path: Path,
        window_title: str = "bash — terminal",
    ) -> Tuple[str, str]:
        """Render a clean macOS/Linux styled developer terminal executing command."""
        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Window Box
        w_left, w_right = 80, self.width - 80
        w_top, w_bottom = 260, 1300
        draw.rounded_rectangle([w_left, w_top, w_right, w_bottom], radius=20, fill=(13, 17, 23), outline=(48, 54, 61), width=2)

        # Title bar & Window Controls (Close, Minimize, Zoom dots)
        draw.rounded_rectangle([w_left, w_top, w_right, w_top + 60], radius=20, fill=(22, 27, 34))
        draw.ellipse([w_left + 24, w_top + 22, w_left + 42, w_top + 40], fill=(255, 95, 87))    # Red
        draw.ellipse([w_left + 54, w_top + 22, w_left + 72, w_top + 40], fill=(254, 188, 46))   # Yellow
        draw.ellipse([w_left + 84, w_top + 22, w_left + 102, w_top + 40], fill=(40, 202, 65))   # Green

        t_font = self._get_font(20)
        draw.text((self.width // 2, w_top + 31), window_title, font=t_font, fill=(139, 148, 158), anchor="mm")

        # Terminal Content
        code_font = self._get_font(28)
        cur_y = w_top + 100

        # Prompt & Command
        draw.text((w_left + 40, cur_y), "$", font=code_font, fill=(56, 189, 248), anchor="ls")
        draw.text((w_left + 75, cur_y), command[:45], font=code_font, fill=(240, 246, 252), anchor="ls")
        cur_y += 55

        # Output lines
        out_font = self._get_font(24)
        for line in output_lines[:8]:
            color = (52, 211, 153) if "success" in line.lower() or "ok" in line.lower() else (139, 148, 158)
            draw.text((w_left + 40, cur_y), line[:52], font=out_font, fill=color, anchor="ls")
            cur_y += 48

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256

    def render_stat_callout(
        self,
        big_stat: str,
        label: str,
        context_detail: str,
        output_path: Path,
    ) -> Tuple[str, str]:
        """Render high-impact stat callout card (e.g. +18% Benchmark Improvement, $26B Revenue)."""
        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Header Badge
        header_font = self._get_font(28)
        draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=(168, 85, 247), width=2)
        draw.text((self.width // 2, 195), "🎯 CRITICAL BREAKTHROUGH", font=header_font, fill=(168, 85, 247), anchor="mm")

        # Huge Stat Box
        box_y1, box_y2 = 360, 960
        draw.rounded_rectangle([100, box_y1, self.width - 100, box_y2], radius=32, fill=(24, 34, 53), outline=(168, 85, 247), width=3)

        # Big Number
        stat_font = self._get_font(96)
        draw.text((self.width // 2, box_y1 + 180), big_stat, font=stat_font, fill=(255, 255, 255), anchor="mm")

        # Label Banner
        lbl_font = self._get_font(38)
        draw.text((self.width // 2, box_y1 + 320), label.upper()[:30], font=lbl_font, fill=(168, 85, 247), anchor="mm")

        # Context Details
        det_font = self._get_font(26)
        draw.text((self.width // 2, box_y1 + 420), context_detail[:46], font=det_font, fill=(148, 163, 184), anchor="mm")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256
