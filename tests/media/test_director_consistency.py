"""Tests for Director consistency, dossier wiring, reasoning backend propagation, and fallback modality accounting."""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.backend import ReasoningBackend
from app.db.repository import SQLiteRepository
from app.domain.enums import ContentFormat, VideoLifecycleState
from app.domain.models import Channel, Claim, FactCheckReport, ResearchDossier, ResearchSource, Scene, Script, VideoProject
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    BeatPurpose,
    NarrativeBeat,
    ShotSpec,
    ShotTimeline,
    Storyboard,
    TimelineShot,
    VisualIntent,
    VisualModality,
)
from app.media.models import MediaQAResult, RenderResult, TTSResult
from app.media.pipeline import MediaProductionPipeline


class FakeReasoningBackend(ReasoningBackend):
    def generate_text(self, prompt: str, **kwargs) -> str:
        return "fake reasoning response"


def test_media_pipeline_loads_research_dossier_from_repository(tmp_path: Path):
    """Ensure MediaProductionPipeline retrieves real ResearchDossier and FactCheckReport and passes them to Director."""
    repo = MagicMock(spec=SQLiteRepository)
    project_id = "proj_test_dossier"

    script = Script(
        id="scr_01",
        title="WAL Architecture",
        hook="How databases protect data.",
        scenes=[Scene(index=0, narration="Write ahead logging buffers sequential disk writes.", target_duration_seconds=3.0)],
        total_word_count=8,
        estimated_duration_seconds=3.0,
    )
    project = VideoProject(
        id=project_id,
        channel_id="chan_01",
        title="WAL Architecture",
        state=VideoLifecycleState.VERIFIED,
        script=script,
    )
    dossier = ResearchDossier(
        id="dos_01",
        topic_id="top_01",
        sources=[ResearchSource(id="src_01", title="PG Docs", url="https://postgresql.org", content_sha256="hash123")],
        claims=[],
        summary="Dossier summary",
    )
    fact_report = FactCheckReport(
        id="fc_01",
        project_id=project_id,
        claims=[],
        audit_summary="Fact report summary",
    )

    repo.get_video_project.return_value = project
    repo.get_channel.return_value = Channel(
        id="chan_01",
        title="Tech Channel",
        handle="@tech",
        niche="programming",
        target_audience="developers",
        default_voice="en-US-GuyNeural",
    )
    repo.get_research_dossier.return_value = dossier
    repo.get_fact_check_report.return_value = fact_report
    repo.get_assets_by_project.return_value = []

    mock_director = MagicMock(spec=AutoDirectorService)
    mock_timeline = ShotTimeline(
        shots=[
            TimelineShot(
                shot_id="shot_01",
                scene_index=0,
                beat_id="b_01",
                start=0.0,
                end=3.0,
                duration=3.0,
                asset_path=str(tmp_path / "shot_01.png"),
                asset_sha256="fake_sha",
                modality=VisualModality.DIAGRAM,
            )
        ],
        total_duration=3.0,
    )
    mock_storyboard = Storyboard(
        project_id=project_id,
        total_duration=3.0,
        content_format=ContentFormat.EXPLAINER,
        shots=[
            ShotSpec(
                shot_id="shot_01",
                beat_id="b_01",
                scene_index=0,
                narration_segment="Write ahead logging buffers sequential disk writes.",
                purpose="explain",
                duration_seconds=3.0,
                subject="WAL",
                action="explain",
                visual_modality=VisualModality.DIAGRAM,
            )
        ],
    )
    mock_director.plan_and_render_timeline.return_value = (mock_timeline, mock_storyboard)

    # Mock TTS and Subtitles to run quickly without live external services
    mock_tts = MagicMock()
    expected_hash = hashlib.sha256(script.get_canonical_narration().encode("utf-8")).hexdigest()
    mock_tts.synthesize.return_value = TTSResult(
        audio_path=str(tmp_path / "audio.mp3"),
        duration_seconds=3.0,
        word_timings=[],
        sample_rate=24000,
        audio_sha256="audio_sha",
        backend="edge_tts",
        voice="en-US-GuyNeural",
        canonical_narration_sha256=expected_hash,
    )
    (tmp_path / "audio.mp3").write_bytes(b"mock audio bytes")
    (tmp_path / "shot_01.png").write_bytes(b"mock shot bytes")

    mock_renderer = MagicMock()
    mock_render_result = RenderResult(
        project_id=project_id,
        video_path=str(tmp_path / "final.mp4"),
        content_sha256="final_sha",
        file_size_bytes=100,
        duration_seconds=3.0,
    )
    mock_renderer.render_timeline.return_value = mock_render_result
    mock_renderer.render_video.return_value = mock_render_result
    (tmp_path / "final.mp4").write_bytes(b"mock video bytes")

    mock_qa = MagicMock()
    mock_qa_res = MediaQAResult(
        passed=True,
        file_path=str(tmp_path / "final.mp4"),
        file_size_bytes=100,
        video_duration=3.0,
        audio_duration=3.0,
        duration_drift=0.0,
        width=1080,
        height=1920,
        fps=30.0,
        video_codec="h264",
        audio_codec="aac",
        loudness_lufs=-14.0,
    )
    mock_qa.inspect_video.return_value = (None, mock_qa_res)

    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=mock_tts,
        renderer=mock_renderer,
        director_service=mock_director,
        qa_inspector=mock_qa,
        base_output_dir=tmp_path,
    )

    pipeline.run_production(project_id=project_id)

    # Assert that repo was queried for real dossier and fact report
    repo.get_research_dossier.assert_called_once_with(project_id)
    repo.get_fact_check_report.assert_called_once_with(project_id)

    # Assert that Director received them explicitly
    mock_director.plan_and_render_timeline.assert_called_once()
    _, kwargs = mock_director.plan_and_render_timeline.call_args
    assert kwargs["dossier"] == dossier
    assert kwargs["fact_report"] == fact_report


def test_reasoning_backend_reaches_beat_decomposer():
    """Verify that a ReasoningBackend provided to MediaProductionPipeline is forwarded to AutoDirectorService and BeatDecomposer."""
    fake_backend = FakeReasoningBackend()
    repo = MagicMock(spec=SQLiteRepository)

    pipeline = MediaProductionPipeline(
        repository=repo,
        reasoning_backend=fake_backend,
    )

    assert pipeline.director is not None
    assert pipeline.director.backend == fake_backend
    assert pipeline.director.decomposer.backend == fake_backend


def test_fallback_updates_actual_modality(tmp_path: Path):
    """Verify that when a requested modality falls back (e.g. STOCK_VIDEO without stock provider or ungrounded DOCUMENT_EVIDENCE), the actual modality is recorded on ShotSpec and TimelineShot."""
    director = AutoDirectorService()

    # Create a shot requesting STOCK_VIDEO (unsupported, should fall back to MOTION_GRAPHICS)
    stock_shot = ShotSpec(
        shot_id="shot_stock",
        beat_id="b_stock",
        scene_index=0,
        narration_segment="Datacenter servers humming at scale.",
        purpose="explain",
        duration_seconds=2.5,
        subject="Datacenter",
        action="show servers",
        visual_modality=VisualModality.STOCK_VIDEO,
    )

    result = director._generate_shot_asset(
        shot=stock_shot,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Server Architecture",
        channel_name="Tech Channel",
    )

    assert result.requested_modality == VisualModality.STOCK_VIDEO
    assert result.actual_modality in (VisualModality.MOTION_GRAPHICS, VisualModality.DIAGRAM)
    assert Path(result.path).exists()


def test_director_run_state_is_reset_between_projects(tmp_path: Path):
    """Ensure asset_attempts, shot_evaluations, and shot_attempt_counts are cleared before each plan_and_render_timeline run."""
    director = AutoDirectorService()

    # Simulate dirty state from Project A
    director.asset_attempts.append(MagicMock())
    director.shot_evaluations["proj_a_shot_01"] = MagicMock()
    director.shot_attempt_counts["proj_a_shot_01"] = 3

    script = Script(
        id="scr_proj_b",
        title="Project B Topic",
        hook="Hook",
        scenes=[Scene(index=0, narration="Explanation for Project B.", target_duration_seconds=3.0)],
        total_word_count=5,
        estimated_duration_seconds=3.0,
    )

    director.plan_and_render_timeline(
        project_id="proj_b",
        script=script,
        channel_name="Tech Channel",
        total_audio_duration=3.0,
        output_dir=tmp_path,
    )

    # State from Project A must NOT be present
    assert "proj_a_shot_01" not in director.shot_evaluations
    assert "proj_a_shot_01" not in director.shot_attempt_counts
    # Only attempts from Project B should be recorded
    for attempt in director.asset_attempts:
        assert "proj_a" not in attempt.shot_id


def test_llm_generated_beats_receive_verified_source_refs():
    """LLM-generated beats must be post-processed by canonical enrichment to receive real verified source_refs."""
    from app.domain.enums import ClaimVerificationVerdict
    from app.media.director.beat_decomposer import BeatDecomposer, DecomposedBeatsPayload

    backend = MagicMock(spec=ReasoningBackend)
    # Simulate LLM returning raw narrative beats without provenance
    backend.generate_structured.return_value = DecomposedBeatsPayload(
        beats=[
            NarrativeBeat(
                beat_id="b_llm_01",
                scene_index=0,
                narration="PostgreSQL write-ahead logging buffers sequential disk writes before memory page flush.",
                duration_hint=3.0,
                purpose=BeatPurpose.EXPLAIN,
                visual_intent=VisualIntent.SHOW_PROCESS,
                key_claim=None,
                source_refs=[],  # LLM did not provide source_refs
            )
        ]
    )

    claim = Claim(
        id="clm_pg_01",
        source_id="src_pg_docs",
        statement="PostgreSQL write-ahead logging buffers sequential disk writes before memory page flush.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://postgresql.org/docs/wal",
    )
    source = ResearchSource(
        id="src_pg_docs",
        title="PostgreSQL Documentation",
        url="https://postgresql.org/docs/wal",
        content_sha256="pg_hash",
    )

    script = Script(
        id="scr_01",
        title="WAL Topic",
        hook="Hook",
        scenes=[Scene(index=0, narration="PostgreSQL write-ahead logging buffers sequential disk writes before memory page flush.", target_duration_seconds=3.0)],
        total_word_count=10,
        estimated_duration_seconds=3.0,
    )

    decomposer = BeatDecomposer(backend=backend)
    beats = decomposer.decompose_script(script, total_audio_duration=3.0, claims=[claim], sources=[source])

    assert len(beats) == 1
    # Canonical enrichment must have attached the real verified source ref
    assert "src_pg_docs" in beats[0].source_refs
    assert beats[0].key_claim == claim.statement


def test_llm_generated_unmatched_beat_has_no_fake_source():
    """Unmatched LLM narrative beats must never be assigned invented or placeholder sources."""
    from app.media.director.beat_decomposer import BeatDecomposer, DecomposedBeatsPayload

    backend = MagicMock(spec=ReasoningBackend)
    backend.generate_structured.return_value = DecomposedBeatsPayload(
        beats=[
            NarrativeBeat(
                beat_id="b_llm_intro",
                scene_index=0,
                narration="Welcome back to our programming channel, let's dive into code!",
                duration_hint=2.5,
                purpose=BeatPurpose.HOOK,
                visual_intent=VisualIntent.ESTABLISH_CONTEXT,
                key_claim=None,
                source_refs=["invented_fake_source_by_llm"],  # LLM hallucinates a source
            )
        ]
    )

    source = ResearchSource(
        id="src_real",
        title="Real Source",
        url="https://real.org",
        content_sha256="sha",
    )

    script = Script(
        id="scr_02",
        title="Intro",
        hook="Welcome",
        scenes=[Scene(index=0, narration="Welcome back to our programming channel, let's dive into code!", target_duration_seconds=2.5)],
        total_word_count=9,
        estimated_duration_seconds=2.5,
    )

    decomposer = BeatDecomposer(backend=backend)
    beats = decomposer.decompose_script(script, total_audio_duration=2.5, claims=[], sources=[source])

    assert len(beats) == 1
    # Enrichment must purge invented sources when no verified claim matches
    assert beats[0].source_refs == []
    assert not any("fake" in s or "invented" in s for s in beats[0].source_refs)


def test_deterministic_and_llm_paths_share_same_provenance_rules():
    """Both deterministic and LLM paths must use enrich_beats_with_provenance and produce identical provenance."""
    from app.domain.enums import ClaimVerificationVerdict
    from app.media.director.beat_decomposer import BeatDecomposer, enrich_beats_with_provenance

    claim = Claim(
        id="clm_shared",
        source_id="src_shared",
        statement="Database transactions guarantee ACID properties.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://cmu.db.edu/acid",
    )
    source = ResearchSource(
        id="src_shared",
        title="CMU DB Course",
        url="https://cmu.db.edu/acid",
        content_sha256="cmu_hash",
    )

    raw_beat = NarrativeBeat(
        beat_id="b_test",
        scene_index=0,
        narration="Database transactions guarantee ACID properties across concurrent workloads.",
        duration_hint=3.0,
        purpose=BeatPurpose.EXPLAIN,
        visual_intent=VisualIntent.SHOW_PROCESS,
    )

    enriched = enrich_beats_with_provenance([raw_beat], claims=[claim], sources=[source])
    assert len(enriched) == 1
    assert enriched[0].source_refs == ["src_shared"]
    assert enriched[0].requires_evidence is True
    assert enriched[0].key_claim == claim.statement

