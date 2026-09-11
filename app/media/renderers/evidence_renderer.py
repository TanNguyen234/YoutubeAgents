"""Evidence and document citation renderer for grounded technical claims."""

import hashlib
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


class EvidenceRenderer:
    """Renders 1080x1920 9:16 empirical evidence, document quotation, and benchmark proof cards."""

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

    def render_evidence_card(
        self,
        source_title: str,
        source_url: str,
        highlighted_claim: str,
        output_path: Path,
        benchmark_name: Optional[str] = None,
        is_verbatim: bool = False,
        claim_verified: bool = False,
    ) -> Tuple[str, str]:
        """Render document evidence card with accurate verification provenance and quotation styling."""
        img = Image.new("RGB", (self.width, self.height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # 1. Header Badge
        header_font = self._get_font(28)
        if claim_verified:
            header_text = "📑 VERIFIED EMPIRICAL EVIDENCE"
            header_color = (59, 130, 246)
        else:
            header_text = "📑 SOURCE DOCUMENT REFERENCE"
            header_color = (148, 163, 184)

        draw.rounded_rectangle([80, 160, self.width - 80, 230], radius=16, fill=(30, 41, 59), outline=header_color, width=2)
        draw.text((self.width // 2, 195), header_text, font=header_font, fill=header_color, anchor="mm")

        # 2. Document Snapshot Card Frame
        d_left, d_right = 80, self.width - 80
        d_top, d_bottom = 280, 1200
        draw.rounded_rectangle([d_left, d_top, d_right, d_bottom], radius=24, fill=(24, 34, 53), outline=header_color, width=2)

        # 3. Source URL Badge Header
        draw.rounded_rectangle([d_left, d_top, d_right, d_top + 70], radius=24, fill=(30, 41, 59))
        url_font = self._get_font(20)
        draw.text((d_left + 30, d_top + 35), f"🔒 {source_url[:48]}", font=url_font, fill=(148, 163, 184), anchor="lm")

        # 4. Verification Checkmark Badge
        ver_f = self._get_font(18)
        if claim_verified:
            draw.rounded_rectangle([d_right - 140, d_top + 16, d_right - 20, d_top + 54], radius=10, fill=(16, 185, 129))
            draw.text((d_right - 80, d_top + 35), "VERIFIED", font=ver_f, fill=(15, 23, 42), anchor="mm")
        else:
            draw.rounded_rectangle([d_right - 140, d_top + 16, d_right - 20, d_top + 54], radius=10, fill=(71, 85, 105))
            draw.text((d_right - 80, d_top + 35), "SOURCE", font=ver_f, fill=(241, 245, 249), anchor="mm")

        # 5. Document Title
        title_font = self._get_font(34)
        draw.text((d_left + 40, d_top + 130), source_title[:38], font=title_font, fill=(255, 255, 255), anchor="ls")

        # Benchmark Pill if provided
        if benchmark_name:
            bm_font = self._get_font(22)
            draw.rounded_rectangle([d_left + 40, d_top + 150, d_left + 280, d_top + 195], radius=8, fill=(37, 99, 235))
            draw.text((d_left + 160, d_top + 172), benchmark_name.upper()[:24], font=bm_font, fill=(255, 255, 255), anchor="mm")

        # 6. Highlighted Content Box (Source Excerpt vs Verified Claim)
        q_top = d_top + 230
        q_bottom = q_top + 380
        box_outline = (234, 179, 8) if is_verbatim else ((59, 130, 246) if claim_verified else (100, 116, 139))
        draw.rounded_rectangle([d_left + 30, q_top, d_right - 30, q_bottom], radius=16, fill=(45, 55, 72), outline=box_outline, width=2)

        # Highlight bar on left
        draw.rounded_rectangle([d_left + 30, q_top, d_left + 46, q_bottom], radius=16, fill=box_outline)

        # Tag header above or in box
        tag_font = self._get_font(18)
        if is_verbatim:
            content_type_tag = "SOURCE EXCERPT (VERBATIM)"
            tag_color = (234, 179, 8)
        elif claim_verified:
            content_type_tag = "VERIFIED CLAIM"
            tag_color = (96, 165, 250)
        else:
            content_type_tag = "SOURCE REFERENCE"
            tag_color = (148, 163, 184)
        draw.text((d_left + 70, q_top + 30), content_type_tag, font=tag_font, fill=tag_color, anchor="ls")

        # Text wrapping
        quote_font = self._get_font(28)
        words = highlighted_claim.split()
        lines = []
        cur = []
        for w in words:
            if len(" ".join(cur + [w])) > 28 and cur:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))

        q_y = q_top + 75
        text_fill = (254, 240, 138) if is_verbatim else (241, 245, 249)
        for idx, line in enumerate(lines[:5]):
            # Only use quotation marks if the excerpt is verbatim from the source document
            if is_verbatim:
                line_str = f'"{line}"' if (idx == 0 and len(lines) == 1) else (f'"{line}' if idx == 0 else (f'{line}"' if idx == len(lines[:5]) - 1 else f' {line}'))
            else:
                line_str = line
            draw.text((d_left + 70, q_y), line_str, font=quote_font, fill=text_fill, anchor="ls")
            q_y += 50

        # Provenance attribution footer
        att_font = self._get_font(22)
        draw.text((self.width // 2, d_bottom - 50), "Source-grounded via YouTube Autopilot Research Dossier", font=att_font, fill=(100, 116, 139), anchor="mm")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), format="PNG")
        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256
