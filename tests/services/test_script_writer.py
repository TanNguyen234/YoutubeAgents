"""Unit tests for ScriptWriter typed sections, scene durations, and narration composition."""

import pytest
from app.domain.enums import PlatformFormat
from app.domain.models import Scene, ScriptSections, Script
from app.services.script_writer import ScriptWriter


@pytest.fixture
def writer():
    return ScriptWriter()


def test_build_script_with_typed_sections(writer):
    scenes = [
        Scene(
            index=0,
            hook="Stop paying $20/mo for basic LLM wrappers.",
            narration="You can run local autonomous agents directly in Python using Antigravity.",
            target_duration_seconds=12.0,
            visual_prompt="Terminal running python script with rapid structured output stream",
            transition="fade",
        ),
        Scene(
            index=1,
            hook="Here is the secret.",
            narration="By pinning SQLite persistence and schema validation, you eliminate 99% of agent hallucinations.",
            target_duration_seconds=15.0,
            visual_prompt="Diagram showing SQLite foreign keys and state machine transitions",
            transition="cut",
        ),
        Scene(
            index=2,
            hook="Take control.",
            narration="Subscribe now and check the GitHub link in the description for the full source code.",
            target_duration_seconds=8.0,
            visual_prompt="YouTube subscribe animation with code repository link overlay",
            transition="fade",
        ),
    ]

    sections = ScriptSections(
        hook="Stop paying $20/mo for basic LLM wrappers.",
        intro="You can run local autonomous agents directly in Python using Antigravity.",
        segments=scenes,
        cta="Subscribe now and check the GitHub link in the description.",
        voiceover_text="Stop paying $20/mo for basic LLM wrappers. You can run local autonomous agents directly in Python using Antigravity. Here is the secret. By pinning SQLite persistence and schema validation, you eliminate 99% of agent hallucinations. Take control. Subscribe now and check the GitHub link in the description for the full source code.",
        estimated_duration=35.0,
    )

    script = writer.build_script(
        script_id="script-001",
        title="Run 100% Local AI Agents in Python",
        sections=sections,
    )

    assert isinstance(script, Script)
    assert script.id == "script-001"
    assert script.title == "Run 100% Local AI Agents in Python"
    assert script.hook == "Stop paying $20/mo for basic LLM wrappers."
    assert len(script.scenes) == 3
    assert script.total_word_count > 20
    assert script.estimated_duration_seconds == pytest.approx(35.0, rel=1e-2)
    assert script.sections is not None
    assert script.sections.cta == "Subscribe now and check the GitHub link in the description."


def test_calculate_speaking_duration(writer):
    # Standard rate ~140 wpm -> ~2.33 words per second -> ~0.43 seconds per word
    text = "One two three four five six seven eight nine ten"
    duration = writer.estimate_speaking_duration(text, words_per_minute=140)
    assert 4.0 <= duration <= 5.0
