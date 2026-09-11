"""Diagram renderer generating clean, high-contrast architecture and process flow visualizations."""

import hashlib
from pathlib import Path
import re
from typing import List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


class DiagramRenderer:
    """Renders 1080x1920 9:16 technical diagrams, commit graphs, and process flows using Pillow."""

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

    def render_architecture_diagram(
        self,
        title: str,
        steps_or_nodes: List[str],
        output_path: Path,
        active_step_index: int = 0,
        flow_label: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Render a vertical architecture/flow diagram with connecting arrows and active step highlight."""
        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))  # Slate dark
        draw = ImageDraw.Draw(img)

        # 1. Subtle background grid
        for x in range(0, self.width, 120):
            draw.line([(x, 0), (x, self.height)], fill=(30, 41, 59), width=1)
        for y in range(0, self.height, 120):
            draw.line([(0, y), (self.width, y)], fill=(30, 41, 59), width=1)

        # 2. Header Category Badge
        header_font = self._get_font(28)
        badge_text = f"⚙️ SYSTEM ARCHITECTURE // {title.upper()[:30]}"
        draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=(56, 189, 248), width=2)
        draw.text((self.width // 2, 195), badge_text, font=header_font, fill=(56, 189, 248), anchor="mm")

        # 3. Compute node placements
        nodes = steps_or_nodes if steps_or_nodes else ["Input / Request", "Processing Engine", "State Storage", "Output Response"]
        num_nodes = min(5, len(nodes))
        nodes = nodes[:num_nodes]

        start_y = 360
        box_height = 160
        gap = 100
        box_width = self.width - 240
        box_left = 120

        node_font = self._get_font(34)
        detail_font = self._get_font(24)

        for i, node_text in enumerate(nodes):
            top_y = start_y + i * (box_height + gap)
            bottom_y = top_y + box_height
            is_active = (i == active_step_index) or (active_step_index < 0)

            fill_color = (24, 34, 53) if not is_active else (30, 58, 95)
            border_color = (56, 189, 248) if is_active else (71, 85, 105)
            text_color = (255, 255, 255) if is_active else (203, 213, 225)
            border_width = 3 if is_active else 1

            # Draw glowing shadow if active
            if is_active:
                draw.rounded_rectangle([box_left - 4, top_y - 4, box_left + box_width + 4, bottom_y + 4], radius=24, fill=(56, 189, 248, 60))

            # Main Node Box
            draw.rounded_rectangle([box_left, top_y, box_left + box_width, bottom_y], radius=20, fill=fill_color, outline=border_color, width=border_width)

            # Node Index Pill
            index_font = self._get_font(22)
            draw.rounded_rectangle([box_left + 24, top_y + 24, box_left + 74, top_y + 64], radius=10, fill=border_color)
            draw.text((box_left + 49, top_y + 44), f"0{i+1}", font=index_font, fill=(15, 23, 42), anchor="mm")

            # Node Title & Subtitle
            parts = node_text.split(":") if ":" in node_text else (node_text, "")
            draw.text((box_left + 94, top_y + 44), parts[0].strip().upper(), font=node_font, fill=text_color, anchor="lm")
            if parts[1].strip():
                draw.text((box_left + 94, top_y + 105), parts[1].strip()[:38], font=detail_font, fill=(148, 163, 184), anchor="lm")

            # Connector Arrow to Next Node
            if i < num_nodes - 1:
                arrow_start_y = bottom_y + 10
                arrow_end_y = arrow_start_y + gap - 20
                center_x = self.width // 2
                draw.line([(center_x, arrow_start_y), (center_x, arrow_end_y)], fill=(56, 189, 248), width=3)
                # Arrowhead
                draw.polygon([
                    (center_x, arrow_end_y + 12),
                    (center_x - 12, arrow_end_y),
                    (center_x + 12, arrow_end_y),
                ], fill=(56, 189, 248))

                # Flow label badge
                if flow_label and i == 0:
                    fl_font = self._get_font(20)
                    draw.rounded_rectangle([center_x + 20, arrow_start_y + 20, center_x + 200, arrow_start_y + 55], radius=8, fill=(15, 23, 42), outline=(56, 189, 248))
                    draw.text((center_x + 110, arrow_start_y + 37), flow_label[:18], font=fl_font, fill=(56, 189, 248), anchor="mm")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256

    def render_from_instruction(self, instruction: str, output_path: Path, title: str = "Mechanism") -> Tuple[str, str]:
        """Parse diagram instruction string and render matching diagram."""
        # Extract potential node names from instruction (e.g. Client -> WAL -> DB)
        steps = []
        if "->" in instruction or "→" in instruction:
            raw_steps = re.split(r"->|→", instruction)
            steps = [s.strip() for s in raw_steps if s.strip()]
        elif ":" in instruction:
            steps = [s.strip() for s in instruction.split(",") if s.strip()]

        if not steps or len(steps) < 2:
            # Generate sensible technical architecture steps from text
            if "wal" in instruction.lower() or "sqlite" in instruction.lower():
                steps = ["App Process: Reader & Writer", "Write-Ahead Log (WAL): Sequential Log Append", "Shared Memory (-shm): Lock-Free Index", "Database File: Background Checkpoint"]
            elif "llm" in instruction.lower() or "token" in instruction.lower():
                steps = ["Input Prompt: 'The capital is...'", "Transformer Layers: Multi-Head Attention", "Logits Projection: Vocabulary Probabilities", "Next Token Selected: 'Paris' (82%)"]
            elif "docker" in instruction.lower() or "container" in instruction.lower():
                steps = ["Host OS Kernel: Shared Namespaces & cgroups", "Container Engine: Isolated Process Tree", "Filesystem Overlay: Copy-on-Write Layers"]
            else:
                steps = ["Input Source: Request Payload", "Processing Engine: Pipeline Transform", "Optimized Storage: State Commit", "Verified Output: Response Delivered"]

        return self.render_architecture_diagram(
            title=title,
            steps_or_nodes=steps,
            output_path=output_path,
            active_step_index=1,
            flow_label="Active Pipeline",
        )
