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


def test_subtitle_word_boundaries_karaoke(tmp_path: Path):
    """Ensure word boundaries produce millisecond-aligned cues and ASS karaoke tags."""
    generator = SubtitleGenerator(words_per_cue=3)
    canonical = "Zero slop video architecture enables maximum audience retention."
    duration = 5.0
    srt_file = tmp_path / "karaoke.srt"
    ass_file = tmp_path / "karaoke.ass"

    word_boundaries = [
        {"text": "Zero", "start_time": 0.1, "end_time": 0.4},
        {"text": "slop", "start_time": 0.45, "end_time": 0.8},
        {"text": "video", "start_time": 0.85, "end_time": 1.2},
        {"text": "architecture", "start_time": 1.3, "end_time": 2.1},
        {"text": "enables", "start_time": 2.15, "end_time": 2.7},
        {"text": "maximum", "start_time": 2.75, "end_time": 3.3},
        {"text": "audience", "start_time": 3.35, "end_time": 3.9},
        {"text": "retention.", "start_time": 3.95, "end_time": 4.8},
    ]

    track = generator.generate_subtitles(
        canonical_narration=canonical,
        audio_duration_seconds=duration,
        output_srt_path=srt_file,
        output_ass_path=ass_file,
        word_boundaries=word_boundaries,
    )

    assert srt_file.exists()
    assert ass_file.exists()
    assert track.cue_count == 3  # 8 words with 3 words per cue -> 3 cues

    # Check ASS content contains kinetic bounce and karaoke \\k tags
    ass_text = ass_file.read_text(encoding="utf-8")
    assert "[Script Info]" in ass_text
    assert "PlayResX: 1080" in ass_text
    assert "PlayResY: 1920" in ass_text
    assert "\\k" in ass_text
    assert "\\fscx115" in ass_text  # Kinetic pop bounce
    assert "&H0000FFFF" in ass_text  # Neon yellow highlight

