"""Deterministic subtitle generator supporting SRT and ASS formats from canonical narration."""

import hashlib
from pathlib import Path
import re
from typing import List, Optional

from app.media.models import SubtitleCue, SubtitleTrack


class SubtitleGenerationError(RuntimeError):
    """Raised when subtitle generation fails."""
    pass


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SRT timestamp string HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milli = int(round((seconds - int(seconds)) * 1000))
    if milli >= 1000:
        milli = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{milli:03d}"


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp string H:MM:SS.cc (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centi = int(round((seconds - int(seconds)) * 100))
    if centi >= 100:
        centi = 99
    return f"{hrs:d}:{mins:02d}:{secs:02d}.{centi:02d}"


class SubtitleGenerator:
    """Generates strictly grounded, non-overlapping subtitle cues from canonical spoken narration."""

    def __init__(self, words_per_cue: int = 6):
        self.words_per_cue = words_per_cue

    def _split_into_phrases(self, text: str) -> List[str]:
        """Split text into bite-sized subtitle cues for Shorts readability."""
        clean_text = text.strip()
        if not clean_text:
            return []

        # Split on sentence terminators while keeping words intact
        sentence_chunks = re.split(r"(?<=[.!?])\s+", clean_text)
        phrases = []

        for sent in sentence_chunks:
            words = sent.strip().split()
            if not words:
                continue

            # If sentence is short, keep as single cue
            if len(words) <= self.words_per_cue:
                phrases.append(" ".join(words))
            else:
                # Group into chunks of words_per_cue
                for i in range(0, len(words), self.words_per_cue):
                    chunk = words[i: i + self.words_per_cue]
                    phrases.append(" ".join(chunk))

        return phrases

    def generate_subtitles(
        self,
        canonical_narration: str,
        audio_duration_seconds: float,
        output_srt_path: Path,
        output_ass_path: Optional[Path] = None,
    ) -> SubtitleTrack:
        """Generate SRT and optional ASS subtitle files aligned to real audio duration."""
        if not canonical_narration or not canonical_narration.strip():
            raise SubtitleGenerationError("Cannot generate subtitles for empty canonical narration.")
        if audio_duration_seconds <= 0.0:
            raise SubtitleGenerationError(f"Audio duration must be > 0 (got {audio_duration_seconds}s).")

        phrases = self._split_into_phrases(canonical_narration)
        if not phrases:
            raise SubtitleGenerationError("Failed to segment canonical narration into subtitle cues.")

        # Calculate word counts for proportional time allocation
        word_counts = [max(1, len(p.split())) for p in phrases]
        total_words = sum(word_counts)

        cues: List[SubtitleCue] = []
        current_time = 0.0

        for idx, (phrase, wc) in enumerate(zip(phrases, word_counts), start=1):
            cue_duration = audio_duration_seconds * (wc / total_words)
            start_time = current_time
            end_time = min(audio_duration_seconds, current_time + cue_duration)

            # Ensure monotonic positive duration
            if end_time <= start_time:
                end_time = start_time + 0.1

            cues.append(
                SubtitleCue(
                    index=idx,
                    start_time=round(start_time, 3),
                    end_time=round(end_time, 3),
                    text=phrase,
                )
            )
            current_time = end_time

        # Ensure last cue reaches full duration with tiny tolerance
        if cues:
            cues[-1].end_time = round(audio_duration_seconds, 3)

        # Write SRT file
        output_srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_lines = []
        for cue in cues:
            srt_lines.append(str(cue.index))
            srt_lines.append(f"{format_srt_timestamp(cue.start_time)} --> {format_srt_timestamp(cue.end_time)}")
            srt_lines.append(cue.text)
            srt_lines.append("")

        srt_content = "\n".join(srt_lines)
        output_srt_path.write_text(srt_content, encoding="utf-8")
        srt_sha256 = hashlib.sha256(output_srt_path.read_bytes()).hexdigest()

        # Write optional ASS file for high-quality burned subtitles
        if output_ass_path:
            output_ass_path.parent.mkdir(parents=True, exist_ok=True)
            ass_content = self._build_ass_script(cues)
            output_ass_path.write_text(ass_content, encoding="utf-8")

        return SubtitleTrack(
            file_path=str(output_srt_path),
            content_sha256=srt_sha256,
            cue_count=len(cues),
            cues=cues,
        )

    def _build_ass_script(self, cues: List[SubtitleCue]) -> str:
        """Build Advanced SubStation Alpha (.ass) script with styling for 1080x1920 Shorts."""
        header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,3,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        event_lines = []
        for cue in cues:
            start_str = format_ass_timestamp(cue.start_time)
            end_str = format_ass_timestamp(cue.end_time)
            # Escape ASS special chars
            safe_text = cue.text.replace("{", "(").replace("}", ")").replace("\\", "/")
            event_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{safe_text}")

        return header + "\n".join(event_lines) + "\n"
