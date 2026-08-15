"""Script writer generating structured video scripts with typed narrative sections."""

import re
from typing import List, Optional

from app.domain.models import Scene, Script, ScriptSections


class ScriptWriter:
    """Produces structured video scripts with discrete typed sections and duration calculations."""

    def __init__(self, default_wpm: int = 140):
        self.default_wpm = default_wpm

    @staticmethod
    def count_words(text: str) -> int:
        """Count clean words in text."""
        words = re.findall(r"\b\w+\b", text)
        return len(words)

    def estimate_speaking_duration(self, text: str, words_per_minute: Optional[int] = None) -> float:
        """Estimate spoken narration duration in seconds at standard speaking rate."""
        wpm = words_per_minute or self.default_wpm
        words = self.count_words(text)
        if words == 0:
            return 0.0
        # words / (wpm / 60)
        return round((words / wpm) * 60.0, 2)

    def build_script(
        self,
        script_id: str,
        title: str,
        sections: ScriptSections,
    ) -> Script:
        """Build and validate a typed Script domain model from sections."""
        all_narrations = [s.narration for s in sections.segments if s.narration]
        full_voiceover = sections.voiceover_text or " ".join(all_narrations)
        total_words = self.count_words(full_voiceover)
        if total_words == 0:
            total_words = max(1, sum(self.count_words(s.narration) for s in sections.segments))

        total_duration = sections.estimated_duration
        if total_duration <= 0.0:
            total_duration = sum(s.target_duration_seconds for s in sections.segments)

        return Script(
            id=script_id,
            title=title,
            hook=sections.hook,
            scenes=sections.segments,
            total_word_count=total_words,
            estimated_duration_seconds=total_duration,
            sections=sections,
        )
