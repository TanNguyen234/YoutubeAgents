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

ANIME_PALETTES = [
    # Blazing Hinokami Kagura (Demon Slayer Flame)
    {"top": (54, 15, 8), "bottom": (20, 5, 5), "accent": (255, 130, 20), "card": (40, 12, 8), "tag": "🔥 SAKUGA MASTERPIECE // EPISODE 19"},
    # Thunder Breathing / Golden Lightning
    {"top": (44, 30, 6), "bottom": (18, 12, 3), "accent": (255, 215, 0), "card": (34, 22, 5), "tag": "⚔️ 3D HYBRID CAMERA BREAKTHROUGH"},
    # Water & Flame Breathing Fusion
    {"top": (50, 16, 22), "bottom": (20, 6, 12), "accent": (255, 95, 60), "card": (40, 12, 16), "tag": "✨ HAND-DRAWN CRAFT ON 2S"},
    # Sun Breathing Supreme / Crimson Gold
    {"top": (62, 12, 12), "bottom": (25, 4, 4), "accent": (255, 75, 30), "card": (48, 10, 10), "tag": "🔥 SUN BREATHING MASTERY"},
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
        background_image_path: Optional[Path] = None,
    ) -> Tuple[str, str]:
        """Render a 1080x1920 scene card, save PNG, and return (file_path, sha256)."""
        is_anime = any(kw in channel_name.lower() or kw in topic_title.lower() for kw in ("anime", "slayer", "sakuga", "hinokami", "manga", "ghibli"))
        if is_anime:
            palette = ANIME_PALETTES[scene_index % len(ANIME_PALETTES)]
        else:
            palette = THEME_PALETTES[scene_index % len(THEME_PALETTES)]

        if background_image_path and Path(background_image_path).exists():
            try:
                bg = Image.open(background_image_path).convert("RGB")
                w, h = bg.size
                target_ratio = self.width / self.height
                img_ratio = w / h
                if abs(img_ratio - target_ratio) > 0.02:
                    if img_ratio > target_ratio:
                        new_w = int(h * target_ratio)
                        left = (w - new_w) // 2
                        bg = bg.crop((left, 0, left + new_w, h))
                    else:
                        new_h = int(w / target_ratio)
                        top = (h - new_h) // 2
                        bg = bg.crop((0, top, w, top + new_h))
                img = bg.resize((self.width, self.height), Image.Resampling.LANCZOS)
                # Dim the background image for readability
                overlay = Image.new("RGBA", (self.width, self.height), (15, 23, 42, 160))
                img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            except Exception:
                img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
                self._draw_vertical_gradient(ImageDraw.Draw(img), palette["top"], palette["bottom"])
        else:
            img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
            self._draw_vertical_gradient(ImageDraw.Draw(img), palette["top"], palette["bottom"])

        draw = ImageDraw.Draw(img)

        # 2. Geometric / Anime Accent Elements
        grid_alpha = 10 if is_anime else 6
        grid_col = (255, 120, 40, grid_alpha) if is_anime else (255, 255, 255, grid_alpha)
        for i in range(0, self.width, 160):
            draw.line([(i, 0), (i, self.height)], fill=grid_col[:3], width=1)
        for j in range(0, self.height, 160):
            draw.line([(0, j), (self.width, j)], fill=grid_col[:3], width=1)

        # Draw glowing anime embers if anime mode
        if is_anime:
            import random
            rng = random.Random(scene_index * 777 + 42)
            for _ in range(35):
                ex = rng.randint(40, self.width - 40)
                ey = rng.randint(220, self.height - 180)
                erad = rng.randint(2, 5)
                ecol = (255, rng.randint(140, 220), rng.randint(20, 60))
                draw.ellipse([(ex - erad, ey - erad), (ex + erad, ey + erad)], fill=ecol)

        # 3. Channel Badge Header (Top Area: y=160)
        badge_font = self._get_font(34)
        channel_icon = "🔥" if is_anime else "●"
        channel_tag = f"{channel_icon} {channel_name.upper()} // EPISODE 01"
        badge_bbox = draw.textbbox((0, 0), channel_tag, font=badge_font)
        badge_w = badge_bbox[2] - badge_bbox[0]
        badge_x = (self.width - badge_w) // 2
        badge_y = 160

        pill_pad_x, pill_pad_y = 32, 14
        draw.rounded_rectangle(
            [
                badge_x - pill_pad_x,
                badge_y - pill_pad_y,
                badge_x + badge_w + pill_pad_x,
                badge_y + 34 + pill_pad_y,
            ],
            radius=24,
            fill=(10, 15, 30, 200),
            outline=palette["accent"],
            width=2,
        )
        draw.text((badge_x, badge_y), channel_tag, font=badge_font, fill=palette["accent"])

        # 4. Cinematic Anime Visual Composition
        scene_tag_font = self._get_font(26)
        scene_tag = palette.get("tag", f"CHAPTER {scene_index + 1} OF {scene_total} // KEY CONCEPT")
        tag_bbox = draw.textbbox((0, 0), scene_tag, font=scene_tag_font)
        tag_w = tag_bbox[2] - tag_bbox[0]
        tag_x = (self.width - tag_w) // 2
        tag_y = 260

        draw.rounded_rectangle(
            [tag_x - 24, tag_y - 10, tag_x + tag_w + 24, tag_y + 26 + 10],
            radius=18,
            fill=(15, 23, 42, 180),
            outline=palette["accent"],
            width=1,
        )
        draw.text((tag_x, tag_y), scene_tag, font=scene_tag_font, fill=palette["accent"])

        # Main Dynamic Headline (Cinematic anime headline typography)
        headline_font = self._get_font(48)
        clean_headline = scene_headline.strip().upper()
        lines = self._wrap_text(clean_headline, max_chars_per_line=24)
        y_text = 340
        line_height = 68

        # Draw dark glowing background plate for text legibility
        text_plate_h = len(lines[:4]) * line_height + 40
        draw.rounded_rectangle(
            [60, y_text - 20, self.width - 60, y_text + text_plate_h],
            radius=20,
            fill=(10, 15, 30, 210),
            outline=(255, 255, 255, 30),
            width=1,
        )

        for line in lines[:4]:
            draw.text((self.width // 2, y_text), line, font=headline_font, fill=(255, 255, 255), anchor="mt")
            y_text += line_height

        # 5. Visual Safe Zone: Keep center and bottom clear for dynamic kinetic subtitles
        draw.line([(80, self.height - 180), (self.width - 80, self.height - 180)], fill=(255, 255, 255, 20), width=1)

        # 6. Save image to disk
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()

        return str(output_path), sha256

    def render_anime_scene_frame(
        self,
        scene_index: int,
        channel_name: str,
        topic_title: str,
        scene_headline: str,
        output_path: Path,
        scene_total: int = 3,
        background_image_path: Optional[Path] = None,
    ) -> Tuple[str, str]:
        """Render a cinematic anime scene frame with dramatic lighting and clean typography."""
        return self.render_scene_card(
            scene_index=scene_index,
            channel_name=channel_name,
            topic_title=topic_title,
            scene_headline=scene_headline,
            output_path=output_path,
            scene_total=scene_total,
            background_image_path=background_image_path,
        )

