"""Unit tests for RetentionPlanner and format-specific narrative grammars."""

import pytest

from app.domain.enums import ContentFormat, HookAngle, RetentionCueType
from app.domain.models import HookCandidate, VideoCreativeBrief
from app.services.retention_planner import FORMAT_GRAMMARS, RetentionPlanner


@pytest.fixture
def sample_hook():
    return HookCandidate(
        text="There's a subtle lock in SQLite that silently freezes concurrent readers.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Expose the hidden locking mechanism and demonstrate how WAL eliminates reader blocking.",
    )


def test_all_content_formats_have_explicit_narrative_grammar():
    """Every single ContentFormat enum member must possess an explicit narrative grammar."""
    planner = RetentionPlanner()

    for fmt in ContentFormat:
        grammar = planner.get_grammar(fmt)
        assert grammar is not None
        assert grammar.content_format == fmt
        assert len(grammar.stages) >= 4
        assert grammar.description.strip() != ""
        assert "NARRATIVE GRAMMAR" in grammar.script_prompt_instructions
        # Must be explicitly registered in FORMAT_GRAMMARS
        assert fmt in FORMAT_GRAMMARS


def test_short_video_does_not_force_excessive_retention_cues(sample_hook):
    """A short 15-20s video must not be overloaded with excessive retention cues or nested loops."""
    planner = RetentionPlanner()
    brief_ultra_short = VideoCreativeBrief(target_duration_seconds=18.0)

    blueprint = planner.build_blueprint(
        hook=sample_hook,
        brief=brief_ultra_short,
        content_format=ContentFormat.EXPLAINER,
        topic="SQLite Concurrency",
    )

    # <= 20s must have at most 3-4 cues
    assert len(blueprint.cues) <= 4
    cue_types = [c.cue_type for c in blueprint.cues]
    assert RetentionCueType.OPEN_LOOP in cue_types
    assert RetentionCueType.LOOP_CLOSE in cue_types
    # Must not have multiple rehooks or multiple pattern interrupts
    assert cue_types.count(RetentionCueType.REHOOK) == 0
    assert cue_types.count(RetentionCueType.PATTERN_INTERRUPT) <= 1


def test_30_second_short_retention_structure(sample_hook):
    """A standard 30s Short receives 1 open loop, 1 pattern interrupt, reveal, loop close, and cta."""
    planner = RetentionPlanner()
    brief_30s = VideoCreativeBrief(target_duration_seconds=32.0)

    blueprint = planner.build_blueprint(
        hook=sample_hook,
        brief=brief_30s,
        content_format=ContentFormat.DEMO,
        topic="SQLite WAL",
    )

    assert len(blueprint.cues) == 5
    cue_types = [c.cue_type for c in blueprint.cues]
    assert cue_types == [
        RetentionCueType.OPEN_LOOP,
        RetentionCueType.PATTERN_INTERRUPT,
        RetentionCueType.REVEAL,
        RetentionCueType.LOOP_CLOSE,
        RetentionCueType.CTA,
    ]


def test_longer_video_scales_retention_cues(sample_hook):
    """Longer videos scale to include escalations, periodic interrupts, and climaxes."""
    planner = RetentionPlanner()

    # 120s video
    brief_120s = VideoCreativeBrief(target_duration_seconds=120.0)
    blueprint_120s = planner.build_blueprint(
        hook=sample_hook,
        brief=brief_120s,
        content_format=ContentFormat.CASE_STUDY,
        topic="Outage Post-Mortem",
    )
    assert len(blueprint_120s.cues) >= 7
    types_120s = [c.cue_type for c in blueprint_120s.cues]
    assert RetentionCueType.ESCALATION in types_120s
    assert RetentionCueType.CLIMAX in types_120s
    assert types_120s.count(RetentionCueType.PATTERN_INTERRUPT) >= 2

    # 300s long-form video
    brief_300s = VideoCreativeBrief(target_duration_seconds=300.0)
    blueprint_300s = planner.build_blueprint(
        hook=sample_hook,
        brief=brief_300s,
        content_format=ContentFormat.BREAKDOWN,
        topic="Distributed Consensus",
    )
    assert len(blueprint_300s.cues) >= 8


def test_hook_promise_has_matching_payoff(sample_hook):
    """Retention blueprint explicitly ties the opening hook promise to the promised payoff and closing cue."""
    planner = RetentionPlanner()
    brief = VideoCreativeBrief(target_duration_seconds=45.0)

    blueprint = planner.build_blueprint(
        hook=sample_hook,
        brief=brief,
        content_format=ContentFormat.EXPLAINER,
        topic="SQLite Concurrency",
    )

    assert blueprint.promised_payoff == sample_hook.promise
    assert "SQLite" in blueprint.core_question

    close_cues = [c for c in blueprint.cues if c.cue_type == RetentionCueType.LOOP_CLOSE]
    assert len(close_cues) >= 1
    assert close_cues[0].linked_hook_promise == sample_hook.promise
