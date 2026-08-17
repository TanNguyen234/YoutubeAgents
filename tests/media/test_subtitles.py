"""Tests for subtitle generation, timestamp monotonicity, and full text reconstruction."""

from pathlib import Path
import re
import pytest

from app.media.subtitles import SubtitleGenerator, SubtitleGenerationError


def normalize_words(text: str) -> str:
    """Strip all punctuation and extra whitespace for strict lexical matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text).lower()).strip()


def test_subtitle_reconstruction_and_monotonicity(tmp_path: Path):
    """Ensure subtitle generator produces monotonic cues that fully reconstruct canonical narration."""
    generator = SubtitleGenerator(words_per_cue=5)
    canonical = (
        "SQLite WAL mode separates read and write operations into dedicated structures. "
        "Readers query the write ahead log shared memory index without taking write locks. "
        "Writers append sequential frames directly to the wal file."
    )
    duration = 18.5
    srt_file = tmp_path / "test.srt"
    ass_file = tmp_path / "test.ass"

    track = generator.generate_subtitles(
        canonical_narration=canonical,
        audio_duration_seconds=duration,
        output_srt_path=srt_file,
        output_ass_path=ass_file,
    )

    assert srt_file.exists()
    assert ass_file.exists()
    assert track.cue_count > 0

    # 1. Monotonic and non-overlapping timing checks
    prev_end = 0.0
    reconstructed_phrases = []

    for cue in track.cues:
        assert cue.start_time >= prev_end - 0.001
        assert cue.end_time > cue.start_time
        assert cue.end_time <= duration + 0.05
        prev_end = cue.end_time
        reconstructed_phrases.append(cue.text)

    # 2. Strict text reconstruction check
    reconstructed_text = " ".join(reconstructed_phrases)
    assert normalize_words(reconstructed_text) == normalize_words(canonical)


def test_subtitle_generation_empty_error(tmp_path: Path):
    """Empty narration must raise SubtitleGenerationError."""
    generator = SubtitleGenerator()
    with pytest.raises(SubtitleGenerationError):
        generator.generate_subtitles(
            canonical_narration="",
            audio_duration_seconds=10.0,
            output_srt_path=tmp_path / "empty.srt",
        )
