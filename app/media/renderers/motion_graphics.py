"""Motion graphics renderer for before/after comparisons, stat callouts, terminal code, and timelines."""

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
from typing import List, Optional, Tuple, Union
from PIL import Image, ImageDraw, ImageFont

from app.media.director.models import MissingGroundedVisualData


class MotionGraphicsRenderer:
    """Renders 1080x1920 9:16 high-impact kinetic typography, stat callouts, and comparisons."""

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
        badge_text: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Render high-impact stat callout card (e.g. +18% Benchmark Improvement, $26B Revenue)."""
        if not big_stat or not str(big_stat).strip():
            raise MissingGroundedVisualData("render_stat_callout requires an explicit grounded big_stat metric.")

        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Header Badge
        header_font = self._get_font(28)
        draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=(168, 85, 247), width=2)
        badge_title = badge_text or "KEY METRIC"
        draw.text((self.width // 2, 195), f"🎯 {badge_title.upper()}", font=header_font, fill=(168, 85, 247), anchor="mm")

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

    def _encode_frames_to_mp4(
        self,
        frame_images: List[Image.Image],
        output_path: Path,
        fps: int = 24,
    ) -> Tuple[str, str]:
        """Encode a sequence of PIL images into an H.264 MP4 video file using ffmpeg rawvideo pipe."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            try:
                import static_ffmpeg
                static_ffmpeg.add_paths()
                ffmpeg_bin = shutil.which("ffmpeg")
            except Exception:
                pass

        if not ffmpeg_bin:
            # Fallback to PNG if ffmpeg is completely absent
            fallback_png = output_path.with_suffix(".png")
            if frame_images:
                frame_images[-1].save(str(fallback_png), format="PNG")
                h = hashlib.sha256(fallback_png.read_bytes()).hexdigest()
                return str(fallback_png), h
            raise RuntimeError("Cannot encode video without ffmpeg and frame images.")

        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.width}x{self.height}",
            "-pix_fmt", "rgb24",
            "-r", str(fps),
            "-i", "-",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            str(output_path),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for frame in frame_images:
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        proc.wait(timeout=30)

        if not output_path.exists() or output_path.stat().st_size == 0:
            fallback_png = output_path.with_suffix(".png")
            frame_images[-1].save(str(fallback_png), format="PNG")
            h = hashlib.sha256(fallback_png.read_bytes()).hexdigest()
            return str(fallback_png), h

        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256

    def render_animated_token_prediction(
        self,
        prompt_text: str,
        candidates: List[Tuple[str, float]],
        selected_token: str,
        output_path: Path,
        duration: float = 3.5,
        fps: int = 24,
    ) -> Tuple[str, str]:
        """Render a true temporal animation demonstrating LLM next-token selection:
        prompt typing -> candidate tokens appear -> bars grow -> selected token highlighted -> appended.
        """
        num_frames = max(12, int(duration * fps))
        frames: List[Image.Image] = []

        f_title = self._get_font(26)
        f_prompt = self._get_font(34)
        f_token = self._get_font(28)
        f_badge = self._get_font(22)

        for i in range(num_frames):
            t = i / fps
            img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
            draw = ImageDraw.Draw(img)

            # Header Badge
            draw.rounded_rectangle([80, 140, self.width - 80, 210], radius=16, fill=(30, 41, 59), outline=(56, 189, 248), width=2)
            draw.text((self.width // 2, 175), "⚡ AUTOREGRESSIVE TOKEN PREDICTION", font=f_title, fill=(56, 189, 248), anchor="mm")

            # Box 1: Prompt Box
            box1_y1, box1_y2 = 250, 470
            draw.rounded_rectangle([80, box1_y1, self.width - 80, box1_y2], radius=20, fill=(13, 17, 23), outline=(51, 65, 85), width=2)
            draw.text((110, box1_y1 + 40), "INPUT CONTEXT / PROMPT", font=f_badge, fill=(100, 116, 139), anchor="ls")

            # Phase 1: Typing prompt (0.0s to 0.8s)
            t_type = min(1.0, t / 0.8)
            num_chars = int(len(prompt_text) * t_type)
            typed_prompt = prompt_text[:num_chars]
            cursor = "_" if (int(t * 4) % 2 == 0) and t < 0.9 else ""

            # Phase 5: Append token at end (>= 2.8s)
            if t >= 2.8:
                draw.text((110, box1_y1 + 110), f'"{prompt_text} ', font=f_prompt, fill=(240, 246, 252), anchor="ls")
                w_offset = int(draw.textlength(f'"{prompt_text} ', font=f_prompt))
                draw.text((110 + w_offset, box1_y1 + 110), f'{selected_token}."', font=f_prompt, fill=(52, 211, 153), anchor="ls")
                draw.rounded_rectangle([110, box1_y1 + 145, 330, box1_y1 + 185], radius=8, fill=(16, 185, 129))
                draw.text((220, box1_y1 + 165), "✓ TOKEN APPENDED", font=f_badge, fill=(15, 23, 42), anchor="mm")
            else:
                draw.text((110, box1_y1 + 110), f'"{typed_prompt}{cursor}"', font=f_prompt, fill=(240, 246, 252), anchor="ls")

            # Phase 2 & 3 & 4: Candidate tokens & Bar growth
            if t >= 0.8:
                box2_y1, box2_y2 = 520, 1250
                draw.rounded_rectangle([80, box2_y1, self.width - 80, box2_y2], radius=24, fill=(24, 34, 53), outline=(71, 85, 105), width=2)
                draw.text((110, box2_y1 + 50), "NEXT-TOKEN CANDIDATES & PROBABILITIES", font=f_badge, fill=(148, 163, 184), anchor="ls")

                # Progress of bar growth (0.8s to 2.2s)
                growth_t = max(0.0, min(1.0, (t - 1.0) / 1.1))
                ease_growth = growth_t * (2.0 - growth_t)

                row_y = box2_y1 + 100
                max_bar_w = self.width - 450

                for tok, prob in candidates[:4]:
                    is_selected = (tok.lower() == selected_token.lower())
                    row_h = 110

                    # Pulse highlight for selected token in Phase 4 (2.2s to 2.8s)
                    if is_selected and t >= 2.2:
                        pulse_fill = (22, 55, 45) if t < 2.8 else (16, 45, 35)
                        pulse_outline = (52, 211, 153) if (int(t * 6) % 2 == 0) else (16, 185, 129)
                        draw.rounded_rectangle([100, row_y, self.width - 100, row_y + row_h], radius=14, fill=pulse_fill, outline=pulse_outline, width=3)
                        draw.rounded_rectangle([self.width - 240, row_y + 15, self.width - 120, row_y + 45], radius=6, fill=(16, 185, 129))
                        draw.text((self.width - 180, row_y + 30), "TOP CHOICE", font=f_badge, fill=(15, 23, 42), anchor="mm")
                    else:
                        draw.rounded_rectangle([100, row_y, self.width - 100, row_y + row_h], radius=14, fill=(15, 23, 42))

                    # Token label
                    label_color = (52, 211, 153) if (is_selected and t >= 2.2) else (226, 232, 240)
                    draw.text((130, row_y + 45), tok, font=f_token, fill=label_color, anchor="ls")

                    # Growing Bar
                    current_bar_w = int(max_bar_w * prob * ease_growth)
                    bar_color = (16, 185, 129) if is_selected else (56, 189, 248)
                    draw.rounded_rectangle([320, row_y + 22, 320 + max(6, current_bar_w), row_y + 55], radius=8, fill=bar_color)

                    # Percentage counter
                    current_p = prob * ease_growth * 100
                    draw.text((340 + current_bar_w + 15, row_y + 43), f"{current_p:.1f}%", font=f_badge, fill=(203, 213, 225), anchor="ls")

                    row_y += 140

            frames.append(img)

        return self._encode_frames_to_mp4(frames, output_path=output_path, fps=fps)

    def render_animated_terminal_video(
        self,
        command: str,
        output_lines: List[str],
        output_path: Path,
        duration: float = 3.0,
        fps: int = 24,
        window_title: str = "bash — terminal",
    ) -> Tuple[str, str]:
        """Render a terminal execution video with character typing and progressive line reveal."""
        num_frames = max(12, int(duration * fps))
        frames: List[Image.Image] = []

        code_font = self._get_font(28)
        out_font = self._get_font(24)
        t_font = self._get_font(20)

        w_left, w_right = 80, self.width - 80
        w_top, w_bottom = 260, 1300

        for i in range(num_frames):
            t = i / fps
            img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
            draw = ImageDraw.Draw(img)

            # Terminal Window Box
            draw.rounded_rectangle([w_left, w_top, w_right, w_bottom], radius=20, fill=(13, 17, 23), outline=(48, 54, 61), width=2)
            draw.rounded_rectangle([w_left, w_top, w_right, w_top + 60], radius=20, fill=(22, 27, 34))
            draw.ellipse([w_left + 24, w_top + 22, w_left + 42, w_top + 40], fill=(255, 95, 87))
            draw.ellipse([w_left + 54, w_top + 22, w_left + 72, w_top + 40], fill=(254, 188, 46))
            draw.ellipse([w_left + 84, w_top + 22, w_left + 102, w_top + 40], fill=(40, 202, 65))
            draw.text((self.width // 2, w_top + 31), window_title, font=t_font, fill=(139, 148, 158), anchor="mm")

            # Phase 1: Typing command (0.0s to 0.9s)
            t_type = min(1.0, t / 0.9)
            n_chars = int(len(command) * t_type)
            cmd_typed = command[:n_chars]
            cursor = "_" if (int(t * 4) % 2 == 0) and t < 1.0 else ""

            cur_y = w_top + 100
            draw.text((w_left + 40, cur_y), "$", font=code_font, fill=(56, 189, 248), anchor="ls")
            draw.text((w_left + 75, cur_y), f"{cmd_typed}{cursor}", font=code_font, fill=(240, 246, 252), anchor="ls")
            cur_y += 55

            # Phase 2: Progressive line reveal (>= 1.0s)
            if t >= 1.0 and output_lines:
                reveal_t = min(1.0, (t - 1.0) / max(0.5, duration - 1.0))
                lines_to_show = int(reveal_t * len(output_lines)) + 1
                for line in output_lines[:lines_to_show]:
                    color = (52, 211, 153) if "success" in line.lower() or "ok" in line.lower() else (139, 148, 158)
                    draw.text((w_left + 40, cur_y), line[:52], font=out_font, fill=color, anchor="ls")
                    cur_y += 48

            frames.append(img)

        return self._encode_frames_to_mp4(frames, output_path=output_path, fps=fps)

    def render_animated_bar_growth(
        self,
        title: str,
        categories: List[str],
        values: List[float],
        output_path: Path,
        duration: float = 3.0,
        fps: int = 24,
        unit: str = "%",
    ) -> Tuple[str, str]:
        """Render an animated bar chart with growing bars and dynamic numerical easing."""
        num_frames = max(12, int(duration * fps))
        frames: List[Image.Image] = []

        h_font = self._get_font(28)
        cat_f = self._get_font(26)
        val_f = self._get_font(24)

        max_val = max(values) if values else 100.0
        max_bar_w = self.width - 450

        for i in range(num_frames):
            t = i / fps
            img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
            draw = ImageDraw.Draw(img)

            # Header
            draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=(56, 189, 248), width=2)
            draw.text((self.width // 2, 195), f"📊 {title.upper()[:28]}", font=h_font, fill=(56, 189, 248), anchor="mm")

            # Growth curve
            progress = min(1.0, t / max(0.5, duration * 0.75))
            eased = progress * (2.0 - progress)

            cur_y = 360
            for cat, val in zip(categories[:6], values[:6]):
                draw.text((100, cur_y + 35), cat[:18], font=cat_f, fill=(226, 232, 240), anchor="ls")
                bar_w = int(max_bar_w * (val / max_val) * eased)
                draw.rounded_rectangle([320, cur_y + 10, 320 + max(6, bar_w), cur_y + 46], radius=8, fill=(16, 185, 129))
                cur_v = val * eased
                draw.text((340 + bar_w + 10, cur_y + 33), f"{cur_v:.1f}{unit}", font=val_f, fill=(148, 163, 184), anchor="ls")
                cur_y += 90

            frames.append(img)

        return self._encode_frames_to_mp4(frames, output_path=output_path, fps=fps)
