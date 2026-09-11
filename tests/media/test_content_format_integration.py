"""Tests for ContentFormat propagation, format-aware scripting, and channel creative profile wiring."""

from pathlib import Path
import pytest

from app.domain.enums import ContentFormat
from app.domain.models import Channel, ResearchDossier, ResearchSource, Scene, Script, ScriptSections
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import VisualModality
from app.media.director.profiles import (
    BENCHMARK_ANALYSIS_PROFILE,
    CODE_TUTORIAL_PROFILE,
    EDITORIAL_TECH_PROFILE,
    TECH_DOCUMENTARY_PROFILE,
    get_channel_profile_for_niche,
)
from app.services.script_generator import ScriptGenerator
from app.services.script_writer import ScriptWriter


def test_channel_creative_profile_niche_resolution():
    """Profiles are resolved accurately based on channel niche keywords."""
    prof_code = get_channel_profile_for_niche("python programming")
    assert prof_code.name == CODE_TUTORIAL_PROFILE.name
    assert VisualModality.CODE_ANIMATION in prof_code.preferred_modalities

    prof_bench = get_channel_profile_for_niche("ai_research and hardware benchmarks")
    assert prof_bench.name == BENCHMARK_ANALYSIS_PROFILE.name
    assert VisualModality.DATA_VISUALIZATION in prof_bench.preferred_modalities

    prof_doc = get_channel_profile_for_niche("tech history and founder stories")
    assert prof_doc.name == TECH_DOCUMENTARY_PROFILE.name

    prof_default = get_channel_profile_for_niche("general technology")
    assert prof_default.name == EDITORIAL_TECH_PROFILE.name


def test_script_generator_format_aware_prompt(monkeypatch):
    """ScriptGenerator incorporates specific format guidelines for DEMO, COMPARISON, and CASE_STUDY."""
    captured_prompts = []

    class MockBackend:
        def generate_structured(self, prompt, schema):
            captured_prompts.append(prompt)
            return ScriptSections(
                hook="Test hook",
                intro="Test intro",
                segments=[
                    Scene(scene_index=0, narration="Segment 1", hook="Intro", visual_prompt="Visual 1", target_duration_seconds=5.0)
                ],
                cta="Test cta",
                voiceover_text="Test voiceover",
                estimated_duration=5.0,
            )

    generator = ScriptGenerator(backend=MockBackend())
    channel = Channel(id="c1", handle="@techlive", title="Tech Live", niche="systems", target_audience="Engineers")
    dossier = ResearchDossier(
        id="dossier-1",
        topic_id="t1",
        summary="Summary of research documentation",
        sources=[ResearchSource(id="s1", url="https://example.com", title="Doc", content_sha256="abc123456789")],
    )

    # 1. DEMO format
    generator.generate_script_sections(channel=channel, keyword="Docker setup", dossier=dossier, content_format=ContentFormat.DEMO)
    assert any("FORMAT: DEMO" in p for p in captured_prompts)
    assert any("show setup -> run command" in p for p in captured_prompts)

    # 2. COMPARISON format
    captured_prompts.clear()
    generator.generate_script_sections(channel=channel, keyword="Postgres vs MySQL", dossier=dossier, content_format=ContentFormat.COMPARISON)
    assert any("FORMAT: HEAD-TO-HEAD COMPARISON" in p for p in captured_prompts)
    assert any("A behavior -> B behavior" in p for p in captured_prompts)


def test_script_writer_persists_content_format():
    """ScriptWriter attaches content_format to constructed Script."""
    writer = ScriptWriter()
    sections = ScriptSections(
        hook="Hook text",
        intro="Intro text",
        segments=[Scene(scene_index=0, narration="Action text", hook="Hook", visual_prompt="Prompt", target_duration_seconds=6.0)],
        cta="CTA text",
        voiceover_text="Full voiceover",
        estimated_duration=6.0,
    )
    script = writer.build_script(
        script_id="scr-demo-01",
        title="Demo Title",
        sections=sections,
        content_format=ContentFormat.DEMO,
    )
    assert script.content_format == ContentFormat.DEMO


def test_storyboard_records_selected_profile_and_format(tmp_path: Path):
    """AutoDirector plans storyboard with channel creative profile name and content format."""
    code_profile = CODE_TUTORIAL_PROFILE
    director = AutoDirectorService(profile=code_profile)

    script = Script(
        id="scr-profile-test",
        title="Git Rebase Workflow",
        hook="How rebase works",
        scenes=[
            Scene(scene_index=0, narration="Run git rebase main to replay your commits onto main.", hook="Step 1", visual_prompt="Terminal with git rebase")
        ],
        total_word_count=12,
        estimated_duration_seconds=6.0,
        content_format=ContentFormat.DEMO,
    )

    out_dir = tmp_path / "out_profile_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    timeline, storyboard = director.plan_and_render_timeline(
        project_id="proj_prof_01",
        script=script,
        channel_name="Developer Channel",
        total_audio_duration=6.0,
        output_dir=out_dir,
        content_format=ContentFormat.DEMO,
    )

    assert storyboard.profile_name == CODE_TUTORIAL_PROFILE.name
    assert storyboard.content_format == ContentFormat.DEMO
    # Storyboard hash must include the profile name
    h1 = storyboard.compute_hash()
    storyboard.profile_name = "Different Profile"
    h2 = storyboard.compute_hash()
    assert h1 != h2
