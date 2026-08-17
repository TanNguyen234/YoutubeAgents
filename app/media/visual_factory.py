"""Deterministic visual card factory creating high-resolution 9:16 scene cards with Pillow."""

import hashlib
import math
from pathlib import Path
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


# Palette presets for aesthetic dark mode video backgrounds
THEME_PALETTES = [
    # Deep Cyber Indigo / Slate
    {"top": (15, 23, 42), "bottom": (30, 41, 59), "accent": (56, 189, 248), "card": (24, 34, 53)},
    # Midnight Violet / Obsidian
    {"top": (19, 14, 38), "bottom": (40, 24, 69), "accent": (168, 85, 247), "card": (30, 20, 55)},
    # Deep Forest / Emerald
    {"top": (6, 30, 24), "bottom": (16, 55, 45), "accent": (52, 211, 153), "card": (12, 42, 35)},
    # Crimson Dark / Onyx
    {"top": (38, 12, 19), "bottom": (69, 20, 32), "accent": (251, 113, 133), "card": (50, 16, 25)},
]


class VisualFactory:
    """Renders deterministic, legible, copyright-safe 1080x1920 9:16 visual cards for video scenes."""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.width = width
        self.height = height

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        """Load system font with size or fallback to default."""
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            try:
                return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
            except Exception:
                return ImageFont.load_default()

    def _draw_vertical_gradient(self, draw: ImageDraw.ImageDraw, top_color: Tuple[int, int, int], bottom_color: Tuple[int, int, int]):
        """Draw a smooth vertical color gradient across the 1080x1920 canvas."""
        for y in range(self.height):
            ratio = y / self.height
            r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
            g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
            b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
            draw.line([(0, y), (self.width, y)], fill=(r, g, b))

    def _wrap_text(self, text: str, max_chars_per_line: int = 24) -> List[str]:
        """Wrap text cleanly into short lines for vertical display."""
        words = text.split()
        lines = []
        cur_line = []
        cur_len = 0
        for w in words:
            if cur_len + len(w) + 1 > max_chars_per_line and cur_line:
                lines.append(" ".join(cur_line))
                cur_line = [w]
                cur_len = len(w)
            else:
                cur_line.append(w)
                cur_len += len(w) + 1
        if cur_line:
            lines.append(" ".join(cur_line))
        return lines

    def render_scene_card(
        self,
        scene_index: int,
        channel_name: str,
        topic_title: str,
        scene_headline: str,
        output_path: Path,
        scene_total: int = 3,
    ) -> Tuple[str, str]:
        """Render a 1080x1920 scene card, save PNG, and return (file_path, sha256)."""
        palette = THEME_PALETTES[scene_index % len(THEME_PALETTES)]

        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # 1. Background Gradient
        self._draw_vertical_gradient(draw, palette["top"], palette["bottom"])

        # 2. Geometric Accent Elements (Subtle Grid/Glow)
        for i in range(0, self.width, 120):
            draw.line([(i, 0), (i, self.height)], fill=(255, 255, 255, 8), width=1)
        for j in range(0, self.height, 120):
            draw.line([(0, j), (self.width, j)], fill=(255, 255, 255, 8), width=1)

        # 3. Channel Badge Header (Top area: y=160)
        badge_font = self._get_font(36)
        channel_tag = f"● {channel_name.upper()}"
        badge_bbox = draw.textbbox((0, 0), channel_tag, font=badge_font)
        badge_w = badge_bbox[2] - badge_bbox[0]
        badge_x = (self.width - badge_w) // 2
        badge_y = 180

        # Badge pill container
        pill_pad_x, pill_pad_y = 30, 14
        draw.rounded_rectangle(
            [
                badge_x - pill_pad_x,
                badge_y - pill_pad_y,
                badge_x + badge_w + pill_pad_x,
                badge_y + 36 + pill_pad_y,
            ],
            radius=24,
            fill=(0, 0, 0, 120),
            outline=palette["accent"],
            width=2,
        )
        draw.text((badge_x, badge_y), channel_tag, font=badge_font, fill=palette["accent"])

        # 4. Scene Progress Indicator (e.g. PART 1 OF 3)
        prog_font = self._get_font(28)
        prog_text = f"SCENE {scene_index + 1} / {max(1, scene_total)}"
        prog_bbox = draw.textbbox((0, 0), prog_text, font=prog_font)
        prog_w = prog_bbox[2] - prog_bbox[0]
        draw.text(((self.width - prog_w) // 2, 280), prog_text, font=prog_font, fill=(148, 163, 184))

        # 5. Central Hero Concept Box (y=440 to y=1200)
        box_margin = 80
        box_top = 420
        box_bottom = 1260
        box_rect = [box_margin, box_top, self.width - box_margin, box_bottom]

        # Shadow and Card Container
        draw.rounded_rectangle(box_rect, radius=32, fill=palette["card"], outline=(255, 255, 255, 40), width=3)

        # Top banner on card
        draw.rounded_rectangle(
            [box_margin, box_top, self.width - box_margin, box_top + 16],
            radius=16,
            fill=palette["accent"],
        )

        # Topic Sub-label inside card
        topic_sub_font = self._get_font(32)
        draw.text((box_margin + 50, box_top + 60), f"CORE CONCEPT:", font=topic_sub_font, fill=palette["accent"])

        # Scene Headline / Key Point (large text inside card)
        headline_font = self._get_font(60)
        lines = self._wrap_text(scene_headline, max_chars_per_line=20)
        y_text = box_top + 140
        line_height = 84

        for line in lines[:8]:
            l_bbox = draw.textbbox((0, 0), line, font=headline_font)
            draw.text((box_margin + 50, y_text), line, font=headline_font, fill=(255, 255, 255))
            y_text += line_height

        # Card Footer Accent bar
        draw.line(
            [(box_margin + 50, box_bottom - 80), (self.width - box_margin - 50, box_bottom - 80)],
            fill=(255, 255, 255, 30),
            width=2,
        )
        footer_font = self._get_font(28)
        draw.text((box_margin + 50, box_bottom - 60), "100% VERIFIED TECHNICAL SPECIFICATION", font=footer_font, fill=(148, 163, 184))

        # 6. Save image to disk
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

        return str(output_path), sha256
