"""Chart and data visualization renderer for empirical proof and probability distributions."""

import hashlib
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


class ChartRenderer:
    """Renders 1080x1920 9:16 high-contrast data visualizations and charts using Pillow."""

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

    def render_horizontal_bar_chart(
        self,
        title: str,
        categories: List[str],
        values: List[float],
        output_path: Path,
        unit: str = "%",
        highlight_index: int = 0,
        subtitle: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Render horizontal bar chart ideal for probability distributions, benchmarks, and rankings."""
        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))  # Slate dark
        draw = ImageDraw.Draw(img)

        # 1. Background Grid Lines
        for x in range(0, self.width, 120):
            draw.line([(x, 0), (x, self.height)], fill=(30, 41, 59), width=1)
        for y in range(0, self.height, 120):
            draw.line([(0, y), (self.width, y)], fill=(30, 41, 59), width=1)

        # 2. Header Category Badge
        header_font = self._get_font(28)
        draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=(52, 211, 153), width=2)
        draw.text((self.width // 2, 195), f"📊 DATA INSIGHT // {title.upper()[:28]}", font=header_font, fill=(52, 211, 153), anchor="mm")

        # Subtitle / Metric Context
        if subtitle:
            sub_font = self._get_font(26)
            draw.text((self.width // 2, 280), subtitle[:45], font=sub_font, fill=(148, 163, 184), anchor="mm")

        # 3. Bar Chart Calculations
        num_items = min(5, len(categories))
        cats = categories[:num_items]
        vals = values[:num_items] if values else [82.0, 12.0, 4.0, 2.0]
        max_val = max(vals) if max(vals) > 0 else 100.0

        bar_start_y = 380
        bar_height = 90
        gap = 70
        max_bar_width = self.width - 360
        bar_left = 120

        label_font = self._get_font(32)
        val_font = self._get_font(34)

        for i in range(num_items):
            cat = cats[i]
            val = vals[i]
            is_highlight = (i == highlight_index)

            y = bar_start_y + i * (bar_height + gap)
            bar_w = int((val / max_val) * max_bar_width)
            bar_w = max(40, bar_w)

            # Category Label
            cat_color = (255, 255, 255) if is_highlight else (203, 213, 225)
            draw.text((bar_left, y - 28), cat.upper(), font=label_font, fill=cat_color, anchor="ls")

            # Bar background track
            draw.rounded_rectangle([bar_left, y, bar_left + max_bar_width, y + bar_height], radius=14, fill=(30, 41, 59))

            # Foreground Bar
            bar_color = (52, 211, 153) if is_highlight else (56, 189, 248)
            draw.rounded_rectangle([bar_left, y, bar_left + bar_w, y + bar_height], radius=14, fill=bar_color)

            # Value Label
            val_text = f"{val:.1f}{unit}" if isinstance(val, float) and val % 1 != 0 else f"{int(val)}{unit}"
            # Text inside bar or right of bar
            text_x = bar_left + bar_w + 24
            draw.text((text_x, y + bar_height // 2), val_text, font=val_font, fill=(255, 255, 255), anchor="lm")

            # Selection / Star badge for highlighted winner
            if is_highlight:
                draw.rounded_rectangle([self.width - 180, y + 16, self.width - 90, y + bar_height - 16], radius=10, fill=(16, 185, 129))
                badge_f = self._get_font(20)
                draw.text((self.width - 135, y + bar_height // 2), "TOP", font=badge_f, fill=(15, 23, 42), anchor="mm")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256

    def render_from_instruction(self, instruction: str, output_path: Path, title: str = "Probability Distribution") -> Tuple[str, str]:
        """Parse chart instruction string and render matching bar or probability chart."""
        cats = []
        vals = []

        # Check for token probability case (e.g. Paris 82%, Lyon 6%, London 2%)
        if "token" in instruction.lower() or "paris" in instruction.lower() or "llm" in instruction.lower():
            cats = ['"Paris"', '"Lyon"', '"London"', '"Berlin"']
            vals = [82.0, 8.0, 6.0, 4.0]
            title = "Next-Token Probability"
            subtitle = 'Prompt: "The capital of France is..."'
        elif "nvidia" in instruction.lower() or "revenue" in instruction.lower():
            cats = ["2024 (AI Boom)", "2023", "2022", "2021"]
            vals = [26.0, 13.5, 6.7, 5.0]
            title = "NVIDIA Quarterly Data Center Rev"
            subtitle = "Data Center Compute Explosion ($B USD)"
        elif "benchmark" in instruction.lower() or "improved" in instruction.lower() or "%" in instruction.lower():
            numbers = [float(n.replace("%", "")) for n in re.findall(r"\b\d+(?:\.\d+)?%?\b", instruction)]
            if numbers:
                vals = numbers[:4]
                cats = [f"Model v{i+1}" for i in range(len(vals))]
            else:
                cats = ["Target Optimization", "Baseline Model", "Legacy System"]
                vals = [94.0, 76.0, 52.0]
            title = "Benchmark Accuracy Delta"
            subtitle = "Evaluation against Standardized Test Suite"
        else:
            cats = ["Primary Variant", "Alternative A", "Alternative B"]
            vals = [78.0, 15.0, 7.0]
            subtitle = "Comparative Performance Metric"

        return self.render_horizontal_bar_chart(
            title=title,
            categories=cats,
            values=vals,
            output_path=output_path,
            unit="%" if max(vals) <= 100 else "$B",
            highlight_index=0,
            subtitle=subtitle,
        )
