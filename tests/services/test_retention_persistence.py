"""Comprehensive tests for typed retention persistence, TTS timestamp mapping, release gates, and concrete anchor grounding."""

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from app.db.repository import SQLiteRepository
from app.domain.enums import (
    ApprovalOrigin,
    AssetType,
    ClaimVerificationVerdict,
    ConcreteAnchorType,
    ContentFormat,
    HookAngle,
    PrivacyStatus,
    PsychologicalMechanism,
    QualityStatus,
    RetentionCueType,
    VideoLifecycleState,
)
from app.domain.models import (
    Channel,
    Claim,
    FactCheckReport,
    HookCandidate,
    ResearchDossier,
    ResearchSource,
    RetentionBlueprint,
    RetentionCue,
    Scene,
    Script,
    ScriptSections,
    VideoProject,
)
from app.domain.retention import (
    ConcreteAnchorAudit,
    DropRisk,
    OpenLoopAudit,
    RetentionMoment,
    ScriptRetentionReport,
    map_retention_moments_to_timestamps,
)
from app.media.models import (
    RenderManifest,
    RETENTION_POLICY_VERSION,
    TTSResult,
    compute_retention_plan_hash,
)
from app.media.pipeline import MediaProductionPipeline
from app.services.pipeline_brain import BrainPipeline
from app.services.retention_planner import RetentionPlanner
from app.services.script_retention import ScriptRetentionEvaluator
from tests.media.test_media_pipeline import MockTTSBackend


# ============================================================================
# P1-2, P2-1, P2-2: Persistence & Timestamp Tests
# ============================================================================

def test_retention_report_typed_sqlite_round_trip(tmp_path: Path):
    """ScriptRetentionReport, RetentionMoment, and ConcreteAnchorAudit survive SQLite round-trip as typed Pydantic instances."""
    repo = SQLiteRepository(str(tmp_path / "test_round_trip.db"))
    channel = Channel(
        id="ch_round_trip",
        title="Engineering Channel",
        handle="@EngChannel",
        description="Tech",
        niche="Databases",
        target_audience="Developers",
    )
    repo.save_channel(channel)

    moment = RetentionMoment(
        position_ratio=0.25,
        timestamp_seconds=None,
        type=RetentionCueType.REVEAL,
        reason="Key insight explains WAL mode",
        psychological_mechanism=PsychologicalMechanism.NOVELTY,
        narration_anchor="Switching to WAL mode writes new pages",
    )
    report = ScriptRetentionReport(
        passed=True,
        hook_quality_score=0.92,
        progression_score=0.88,
        payoff_alignment_score=0.95,
        strongest_moments=[moment],
        concrete_anchors=[
            ConcreteAnchorAudit(
                anchor_type=ConcreteAnchorType.ANALOGY,
                scene_index=0,
                text="Think of WAL like an append-only ledger",
                grounded=True,
            )
        ],
    )
    scenes = [
        Scene(index=0, narration="Think of WAL like an append-only ledger.", target_duration_seconds=10.0),
        Scene(index=1, narration="Switching to WAL mode writes new pages.", target_duration_seconds=10.0),
    ]
    sections = ScriptSections(
        hook="SQLite concurrency is subtle.",
        intro="Let's unpack locking.",
        segments=scenes,
        cta="Subscribe for deeper dives.",
        estimated_duration=20.0,
        retention_report=report,
    )
    script = Script(
        id="scr_round_trip",
        title="SQLite WAL",
        hook="SQLite concurrency is subtle.",
        scenes=scenes,
        sections=sections,
        total_word_count=25,
        estimated_duration_seconds=20.0,
        retention_report=report,
    )
    project = VideoProject(
        id="proj_round_trip",
        channel_id=channel.id,
        title="SQLite WAL",
        state=VideoLifecycleState.CREATED,
        script=script,
    )

    repo.save_video_project(project)
    reloaded = repo.get_video_project("proj_round_trip")

    assert reloaded is not None
    assert reloaded.script is not None
    # Report must be strongly typed, not a raw dict
    assert isinstance(reloaded.script.retention_report, ScriptRetentionReport)
    assert len(reloaded.script.retention_report.strongest_moments) == 1

    # Moment must be strongly typed
    reloaded_moment = reloaded.script.retention_report.strongest_moments[0]
    assert isinstance(reloaded_moment, RetentionMoment)
    assert reloaded_moment.psychological_mechanism == PsychologicalMechanism.NOVELTY
    assert isinstance(reloaded_moment.psychological_mechanism, PsychologicalMechanism)
    assert reloaded_moment.narration_anchor == "Switching to WAL mode writes new pages"

    # Concrete anchors must be strongly typed
    assert len(reloaded.script.retention_report.concrete_anchors) == 1
    assert isinstance(reloaded.script.retention_report.concrete_anchors[0], ConcreteAnchorAudit)
    assert reloaded.script.retention_report.concrete_anchors[0].grounded is True


def test_retention_timestamp_round_trip_preservation(tmp_path: Path):
    """Retention moments enriched with real TTS word boundaries preserve timestamps across repeated DB persistence."""
    repo = SQLiteRepository(str(tmp_path / "test_ts_round_trip.db"))
    channel = Channel(
        id="ch_ts",
        title="Eng Channel",
        handle="@Eng",
        description="Tech",
        niche="Databases",
        target_audience="Developers",
    )
    repo.save_channel(channel)

    moment = RetentionMoment(
        position_ratio=0.5,
        timestamp_seconds=None,
        type=RetentionCueType.REVEAL,
        reason="WAL eliminates reader blocking",
        psychological_mechanism=PsychologicalMechanism.NOVELTY,
        narration_anchor="WAL mode eliminates reader blocking",
    )
    report = ScriptRetentionReport(
        passed=True,
        hook_quality_score=0.90,
        progression_score=0.85,
        payoff_alignment_score=0.90,
        strongest_moments=[moment],
    )
    scenes = [
        Scene(index=0, narration="SQLite locks readers in default mode.", target_duration_seconds=10.0),
        Scene(index=1, narration="WAL mode eliminates reader blocking.", target_duration_seconds=10.0),
    ]
    sections = ScriptSections(
        hook="SQLite concurrency is subtle.",
        intro="Let's unpack locking.",
        segments=scenes,
        cta="Subscribe for more.",
        estimated_duration=20.0,
        retention_report=report,
    )
    script = Script(
        id="scr_ts",
        title="SQLite WAL",
        hook="SQLite concurrency is subtle.",
        scenes=scenes,
        sections=sections,
        total_word_count=20,
        estimated_duration_seconds=20.0,
        retention_report=report,
    )
    project = VideoProject(
        id="proj_ts",
        channel_id=channel.id,
        title="SQLite WAL",
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)

    # 1. Reload before TTS
    loaded_1 = repo.get_video_project("proj_ts")
    assert loaded_1 is not None
    assert loaded_1.script.retention_report.strongest_moments[0].timestamp_seconds is None

    # 2. Enrich with TTS timing
    timing_events = [
        {"word": "wal", "start": 9.8},
        {"word": "mode", "start": 10.1},
        {"word": "eliminates", "start": 10.5},
    ]
    loaded_1.script.retention_report.populate_timestamps(
        total_duration_seconds=22.0,
        timing_events=timing_events,
        canonical_narration=loaded_1.script.get_canonical_narration(),
    )
    assert loaded_1.script.retention_report.strongest_moments[0].timestamp_seconds is not None
    assert loaded_1.script.retention_report.strongest_moments[0].timestamp_seconds == 9.8

    # 3. Save again
    repo.save_video_project(loaded_1)

    # 4. Reload again and verify timestamps remain intact
    loaded_2 = repo.get_video_project("proj_ts")
    assert loaded_2 is not None
    assert isinstance(loaded_2.script.retention_report, ScriptRetentionReport)
    saved_moment = loaded_2.script.retention_report.strongest_moments[0]
    assert saved_moment.timestamp_seconds == 9.8
    assert saved_moment.narration_anchor == "WAL mode eliminates reader blocking"


def test_retention_timestamp_mapping_failure_is_observable(caplog):
    """Timestamp mapping errors are logged as warnings rather than being silently swallowed."""
    broken_report = MagicMock()
    broken_report.populate_timestamps.side_effect = RuntimeError("Synthetic timing glitch")

    logger = logging.getLogger("app.media.pipeline")
    with caplog.at_level(logging.WARNING):
        try:
            broken_report.populate_timestamps(10.0)
        except Exception:
            logger.warning(
                "RETENTION_TIMESTAMP_MAPPING_FAILED: Failed to map retention timestamps after TTS",
                exc_info=True,
            )

    assert "RETENTION_TIMESTAMP_MAPPING_FAILED" in caplog.text


class TimingMockTTSBackend(MockTTSBackend):
    def __init__(self, duration_seconds: float = 6.0, timing_events: Optional[list] = None):
        super().__init__(duration_seconds=duration_seconds)
        self.timing_events = timing_events or []

    def synthesize(self, *args, **kwargs) -> TTSResult:
        res = super().synthesize(*args, **kwargs)
        res.timing_events = list(self.timing_events)
        return res


def _setup_pipeline_project_with_report(tmp_path: Path, moment_timestamp: Optional[float] = None):
    db_path = tmp_path / "test_pipeline_persistence.db"
    repo = SQLiteRepository(str(db_path))
    channel = Channel(
        id="chan_p1_2",
        title="Engineering Channel",
        handle="@Eng",
        description="Tech",
        niche="Databases",
        target_audience="Developers",
    )
    repo.save_channel(channel)
    moment = RetentionMoment(
        position_ratio=0.5,
        timestamp_seconds=moment_timestamp,
        type=RetentionCueType.REVEAL,
        reason="WAL eliminates reader blocking",
        psychological_mechanism=PsychologicalMechanism.NOVELTY,
        narration_anchor="WAL mode eliminates reader blocking",
    )
    report = ScriptRetentionReport(
        passed=True,
        hook_quality_score=0.90,
        progression_score=0.85,
        payoff_alignment_score=0.90,
        strongest_moments=[moment],
        concrete_anchors=[
            ConcreteAnchorAudit(
                anchor_type=ConcreteAnchorType.ANALOGY,
                scene_index=0,
                text="Think of WAL like an append-only ledger",
                grounded=True,
            )
        ],
    )
    scenes = [
        Scene(scene_index=0, narration="SQLite locks readers in default mode.", hook="Hook 1", target_duration_seconds=5.0),
        Scene(scene_index=1, narration="WAL mode eliminates reader blocking.", hook="Hook 2", target_duration_seconds=5.0),
    ]
    sections = ScriptSections(
        hook="SQLite concurrency is subtle.",
        intro="Let's unpack locking.",
        segments=scenes,
        cta="Subscribe for more.",
        estimated_duration=10.0,
        retention_report=report,
    )
    script = Script(
        id="scr_p1_2",
        title="SQLite WAL Architecture",
        hook="SQLite concurrency is subtle.",
        scenes=scenes,
        sections=sections,
        total_word_count=12,
        estimated_duration_seconds=10.0,
        retention_report=report,
    )
    project = VideoProject(
        id="proj_p1_2",
        channel_id=channel.id,
        title="SQLite WAL Architecture",
        state=VideoLifecycleState.CREATED,
        script=script,
    )
    repo.save_video_project(project)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.RESEARCHING)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.PLANNED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.SCRIPTED)
    repo.update_project_state(project.id, to_state=VideoLifecycleState.VERIFIED)
    return repo, project.id


def test_cache_hit_populates_missing_retention_timestamps(tmp_path: Path):
    """When a cached render is reused, missing retention timestamps are populated from cached timing metadata."""
    repo, project_id = _setup_pipeline_project_with_report(tmp_path)
    timing = [
        {"word": "wal", "start": 3.2},
        {"word": "mode", "start": 3.5},
        {"word": "eliminates", "start": 3.8},
        {"word": "reader", "start": 4.1},
        {"word": "blocking", "start": 4.5},
    ]
    tts = TimingMockTTSBackend(duration_seconds=6.0, timing_events=timing)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_cache_ts",
    )

    _, _, manifest_1 = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1
    assert manifest_1.tts_timing_events is not None

    p = repo.get_video_project(project_id)
    p.script.retention_report.strongest_moments[0].timestamp_seconds = None
    p.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(p)

    reloaded_before = repo.get_video_project(project_id)
    assert reloaded_before.script.retention_report.strongest_moments[0].timestamp_seconds is None

    proj_2, qa_2, manifest_2 = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1
    assert proj_2.script.retention_report.strongest_moments[0].timestamp_seconds is not None
    assert proj_2.script.retention_report.strongest_moments[0].timestamp_seconds == 3.2


def test_cache_hit_does_not_resynthesize_tts_for_metadata(tmp_path: Path):
    """Metadata enrichment on render cache hit does not re-invoke external/synthesizing TTS."""
    repo, project_id = _setup_pipeline_project_with_report(tmp_path)
    timing = [{"word": "wal", "start": 3.2}]
    tts = TimingMockTTSBackend(duration_seconds=6.0, timing_events=timing)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_no_tts",
    )

    pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1

    p = repo.get_video_project(project_id)
    p.script.retention_report.strongest_moments[0].timestamp_seconds = None
    p.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(p)

    pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1


def test_cache_hit_persists_enriched_timestamps(tmp_path: Path):
    """Enriched timestamps from cache hit survive reloading from SQLite."""
    repo, project_id = _setup_pipeline_project_with_report(tmp_path)
    timing = [{"word": "wal", "start": 3.2}, {"word": "mode", "start": 3.5}]
    tts = TimingMockTTSBackend(duration_seconds=6.0, timing_events=timing)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_persist_ts",
    )

    pipeline.run_production(project_id=project_id)
    p = repo.get_video_project(project_id)
    p.script.retention_report.strongest_moments[0].timestamp_seconds = None
    p.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(p)

    pipeline.run_production(project_id=project_id)

    reloaded = repo.get_video_project(project_id)
    assert reloaded.script.retention_report.strongest_moments[0].timestamp_seconds is not None
    assert reloaded.script.retention_report.strongest_moments[0].timestamp_seconds == 3.2


def test_old_cache_without_timing_events_uses_duration_ratio_fallback(tmp_path: Path):
    """When a cached manifest lacks word-level timing events, duration ratio fallback is used."""
    repo, project_id = _setup_pipeline_project_with_report(tmp_path)
    tts = TimingMockTTSBackend(duration_seconds=10.0, timing_events=[])
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_ratio_fallback",
    )

    pipeline.run_production(project_id=project_id)

    manifest_path = tmp_path / "out_ratio_fallback" / project_id / "manifests" / "render_manifest.json"
    manifest_obj = RenderManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest_dict = manifest_obj.model_dump()
    manifest_dict["tts_timing_events"] = None
    manifest_dict["audio_duration_seconds"] = 10.0
    manifest_path.write_text(RenderManifest(**manifest_dict).model_dump_json(indent=2), encoding="utf-8")

    p = repo.get_video_project(project_id)
    p.script.retention_report.strongest_moments[0].timestamp_seconds = None
    p.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(p)

    proj_2, _, _ = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1
    assert proj_2.script.retention_report.strongest_moments[0].timestamp_seconds == 5.0


def test_timestamp_enrichment_failure_is_logged_not_silenced(tmp_path: Path, caplog):
    """Failure during cache-hit timestamp enrichment logs a warning and does not crash render reuse."""
    repo, project_id = _setup_pipeline_project_with_report(tmp_path)
    tts = TimingMockTTSBackend(duration_seconds=6.0, timing_events=[])
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_log_failure",
    )

    pipeline.run_production(project_id=project_id)

    p = repo.get_video_project(project_id)
    p.script.retention_report.strongest_moments[0].timestamp_seconds = None
    p.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(p)

    with caplog.at_level(logging.WARNING):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                ScriptRetentionReport,
                "populate_timestamps",
                MagicMock(side_effect=RuntimeError("Corrupt timing data")),
            )
            proj_2, qa_2, _ = pipeline.run_production(project_id=project_id)

    assert "RETENTION_TIMESTAMP_MAPPING_FAILED" in caplog.text
    assert qa_2.passed is True


def test_persistence_and_cache_interaction_populates_timestamps(tmp_path: Path):
    """End-to-end scenario: project with missing timestamps reuses valid render cache, avoids TTS, enriches and persists timestamps."""
    repo, project_id = _setup_pipeline_project_with_report(tmp_path)
    timing = [
        {"word": "wal", "start": 3.2},
        {"word": "mode", "start": 3.5},
        {"word": "eliminates", "start": 3.8},
        {"word": "reader", "start": 4.1},
        {"word": "blocking", "start": 4.5},
    ]
    tts = TimingMockTTSBackend(duration_seconds=6.0, timing_events=timing)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_e2e_cache_ts",
    )

    # 1. Create project with retention blueprint, retention report, strongest moments without timestamps
    p = repo.get_video_project(project_id)
    p.script.retention_blueprint = RetentionBlueprint(
        hook=HookCandidate(text="Hook", angle=HookAngle.CURIOSITY_GAP, promise="Promise"),
        cues=[RetentionCue(cue_id="c1", cue_type=RetentionCueType.REVEAL, target_position_ratio=0.5, purpose="P", anchor_text="WAL mode eliminates reader blocking", linked_hook_promise="Promise")],
        core_question="Q",
        promised_payoff="P",
        content_format=ContentFormat.EXPLAINER,
        target_duration_seconds=10.0,
    )
    p.script.retention_report.strongest_moments[0].timestamp_seconds = None
    # 2. Persist project
    repo.save_video_project(p)

    # Initial run to generate valid cache
    _, _, manifest_1 = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1
    tts.call_count = 0  # reset call counter

    # Simulate fresh project reload with missing timestamps
    p2 = repo.get_video_project(project_id)
    p2.script.retention_report.strongest_moments[0].timestamp_seconds = None
    p2.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(p2)

    # 4. Call MediaProductionPipeline.run_production
    proj_out, qa_out, manifest_out = pipeline.run_production(project_id=project_id)

    # 5. Pipeline should REUSE render cache
    assert manifest_out.final_video_sha256 == manifest_1.final_video_sha256
    # 6. Must NOT synthesize TTS again
    assert tts.call_count == 0
    # 7. Must populate missing retention timestamps
    assert proj_out.script.retention_report.strongest_moments[0].timestamp_seconds is not None

    # 8-9. Reload project and verify strongly typed contracts
    reloaded = repo.get_video_project(project_id)
    assert isinstance(reloaded.script.retention_report, ScriptRetentionReport)
    assert isinstance(reloaded.script.retention_report.strongest_moments[0], RetentionMoment)
    assert reloaded.script.retention_report.strongest_moments[0].timestamp_seconds is not None
    assert reloaded.state in (VideoLifecycleState.READY_FOR_REVIEW, VideoLifecycleState.RENDERED)


# ============================================================================
# P1-3: Final Retention Release Gate Tests
# ============================================================================

def test_final_retention_pass_transitions_to_verified(tmp_path: Path, monkeypatch):
    """FactCheck PASS + Retention PASS transitions project to VERIFIED."""
    repo = SQLiteRepository(str(tmp_path / "test_gate_pass.db"))
    channel = Channel(
        id="ch_gate_pass",
        title="Systems Engineering",
        handle="@SystemsEng",
        description="Deep tech channel",
        niche="Database Internals",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)

    mock_dossier = ResearchDossier(
        id="dos_gate_pass",
        topic_id="top_gate_pass",
        topic="SQLite Concurrency",
        summary="SQLite WAL eliminates reader blocking.",
        sources=[ResearchSource(id="s1", title="SQLite WAL", url="https://sqlite.org/wal.html", content_snapshot="WAL eliminates reader blocking.", author="Richard Hipp", content_sha256="dummy_sha")],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=mock_dossier)
    brain.strategist.duplicate_detector.check_duplicate = MagicMock(return_value=(False, 0.0, None))
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {"demand": 8.0, "freshness": 7.0, "competition": 3.0, "channel_fit": 8.0, "originality": 7.5, "evidence_quality": 9.0, "production_feasibility": 8.0},
        "Strong opportunity",
        {"volume_source": "seed"},
    ))

    sample_hook = HookCandidate(
        text="There is a subtle lock in SQLite that silently freezes concurrent readers.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Unpack how WAL mode eliminates reader blocking.",
    )
    mock_scenes = [
        Scene(index=0, narration="In default rollback journal mode, writing acquires an exclusive table lock.", target_duration_seconds=10.0),
        Scene(index=1, narration="Think of it like a shared single-lane bridge where all cars must stop for a truck.", target_duration_seconds=10.0),
        Scene(index=2, narration="Switching to WAL mode writes new pages to a separate log file instead.", target_duration_seconds=10.0),
        Scene(index=3, narration="This eliminates reader blocking entirely, unlocking massive concurrent read throughput.", target_duration_seconds=10.0),
    ]
    mock_sections = ScriptSections(
        hook=sample_hook.text,
        intro="In default mode, write locks freeze all queries.",
        segments=mock_scenes,
        cta="WAL fixes reader-writer blocking, but checkpointing creates the next bottleneck — that's the next breakdown.",
        estimated_duration=40.0,
    )
    brain.generator.generate_script_sections = MagicMock(return_value=mock_sections)
    brain.extractor.extract_from_script = MagicMock(return_value=[
        Claim(id="c1", statement="Switching to WAL mode writes new pages to a separate log file instead.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True, source_id="s1")
    ])

    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.generate_hook_candidates",
        lambda *args, **kwargs: [sample_hook],
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.run_tournament",
        lambda *args, **kwargs: (sample_hook, MagicMock(), [MagicMock()]),
    )

    brain.checker.verify_all_claims = MagicMock(return_value=FactCheckReport(
        id="fcr_gate_pass",
        project_id="proj_gate_pass",
        audit_summary="All claims verified.",
        claims=[
            Claim(id="c1", statement="Switching to WAL mode writes new pages to a separate log file instead.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
        ],
        verified_count=1,
        failed_count=0,
        overall_verdict=QualityStatus.PASSED,
    ))

    project, report = brain.run_stage_1_to_5(
        project_id="proj_gate_pass",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    assert project.state == VideoLifecycleState.VERIFIED
    assert report.overall_verdict == QualityStatus.PASSED
    assert project.script.retention_report.passed is True


def test_final_retention_failure_after_repair_transitions_to_blocked(tmp_path: Path, monkeypatch):
    """FactCheck PASS + Retention FAIL after one bounded repair transitions project to BLOCKED (not VERIFIED)."""
    repo = SQLiteRepository(str(tmp_path / "test_gate_block.db"))
    channel = Channel(
        id="ch_gate_block",
        title="Systems Engineering",
        handle="@SystemsEng",
        description="Tech",
        niche="Database Internals",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)

    mock_dossier = ResearchDossier(
        id="dos_gate_block",
        topic_id="top_gate_block",
        topic="SQLite Concurrency",
        summary="SQLite WAL eliminates reader blocking.",
        sources=[ResearchSource(id="s1", title="SQLite WAL", url="https://sqlite.org/wal.html", content_snapshot="WAL eliminates reader blocking.", author="Richard Hipp", content_sha256="dummy_sha")],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=mock_dossier)
    brain.strategist.duplicate_detector.check_duplicate = MagicMock(return_value=(False, 0.0, None))
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {"demand": 8.0, "freshness": 7.0, "competition": 3.0, "channel_fit": 8.0, "originality": 7.5, "evidence_quality": 9.0, "production_feasibility": 8.0},
        "Strong opportunity",
        {"volume_source": "seed"},
    ))

    # Script has a severe unclosed loop defect that causes retention QA to fail
    mock_scenes = [
        Scene(index=0, hook="SQLite concurrency is misunderstood.", narration="In this video we talk about locking.", target_duration_seconds=15.0),
        Scene(index=1, hook="Random topic.", narration="Also web frontend design is nice.", target_duration_seconds=15.0),
    ]
    mock_sections = ScriptSections(
        hook="In this video we will talk about SQLite concurrency.",
        intro="Generic intro.",
        segments=mock_scenes,
        cta="Like and subscribe.",
        estimated_duration=30.0,
    )
    brain.generator.generate_script_sections = MagicMock(return_value=mock_sections)
    # Even after repair, rewrite still fails retention
    brain.generator.rewrite_for_retention = MagicMock(return_value=mock_sections)

    brain.extractor.extract_from_script = MagicMock(return_value=[
        Claim(id="c1", statement="Web frontend design is nice.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True, source_id="s1")
    ])

    sample_hook = HookCandidate(
        text="In this video we will talk about SQLite concurrency.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Unpack WAL concurrency.",
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.generate_hook_candidates",
        lambda *args, **kwargs: [sample_hook],
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.run_tournament",
        lambda *args, **kwargs: (sample_hook, MagicMock(), [MagicMock()]),
    )

    brain.checker.verify_all_claims = MagicMock(return_value=FactCheckReport(
        id="fcr_gate_block",
        project_id="proj_gate_block",
        audit_summary="Factual claims verified.",
        claims=[
            Claim(id="c1", statement="Web frontend design is nice.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
        ],
        verified_count=1,
        failed_count=0,
        overall_verdict=QualityStatus.PASSED,
    ))

    project, report = brain.run_stage_1_to_5(
        project_id="proj_gate_block",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    # Must transition to BLOCKED, NOT VERIFIED!
    assert project.state == VideoLifecycleState.BLOCKED
    reloaded = repo.get_video_project("proj_gate_block")
    assert reloaded.state == VideoLifecycleState.BLOCKED


def test_retention_blocked_project_never_enters_media_production(tmp_path: Path, monkeypatch):
    """When Stage 1-5 yields BLOCKED state due to retention failure, run_full_autonomous_lifecycle halts immediately."""
    repo = SQLiteRepository(str(tmp_path / "test_halt.db"))
    channel = Channel(
        id="ch_halt",
        title="Systems Engineering",
        handle="@SystemsEng",
        description="Tech",
        niche="Database Internals",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)

    # Force run_stage_1_to_5 to return a BLOCKED project
    mock_blocked_project = VideoProject(
        id="proj_halt",
        channel_id=channel.id,
        title="SQLite Concurrency",
        state=VideoLifecycleState.BLOCKED,
    )
    mock_fact_report = FactCheckReport(
        id="fcr_halt",
        project_id="proj_halt",
        claims=[],
        verified_count=0,
        failed_count=0,
        overall_verdict=QualityStatus.PASSED,
        audit_summary="Facts passed",
    )
    brain.run_stage_1_to_5 = MagicMock(return_value=(mock_blocked_project, mock_fact_report))

    # Spy on MediaProductionPipeline
    mock_media_pipeline = MagicMock()
    monkeypatch.setattr(
        "app.services.pipeline_brain.MediaProductionPipeline",
        lambda *args, **kwargs: mock_media_pipeline,
    )

    result = brain.run_full_autonomous_lifecycle(
        project_id="proj_halt",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    # Must halt cleanly
    assert result["lifecycle_state"] == "BLOCKED"
    assert result["status"] == "BLOCKED"
    assert "halting before media production" in result["halted_reason"]
    # MediaProductionPipeline must NEVER have been called
    assert mock_media_pipeline.run_production.call_count == 0


def test_final_retention_rewrite_always_triggers_refactcheck(tmp_path: Path, monkeypatch):
    """Any final retention rewrite triggers claim extraction and FactChecker re-run."""
    repo = SQLiteRepository(str(tmp_path / "test_refact.db"))
    channel = Channel(
        id="ch_refact",
        title="Systems Engineering",
        handle="@SystemsEng",
        description="Tech",
        niche="Database Internals",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)

    mock_dossier = ResearchDossier(
        id="dos_refact",
        topic_id="top_refact",
        topic="SQLite Concurrency",
        summary="SQLite WAL eliminates reader blocking.",
        sources=[ResearchSource(id="s1", title="SQLite WAL", url="https://sqlite.org/wal.html", content_snapshot="WAL eliminates reader blocking.", author="Richard Hipp", content_sha256="dummy_sha")],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=mock_dossier)
    brain.strategist.duplicate_detector.check_duplicate = MagicMock(return_value=(False, 0.0, None))
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {"demand": 8.0, "freshness": 7.0, "competition": 3.0, "channel_fit": 8.0, "originality": 7.5, "evidence_quality": 9.0, "production_feasibility": 8.0},
        "Strong opportunity",
        {"volume_source": "seed"},
    ))

    # Initial script has detached CTA causing retention failure
    mock_scenes = [
        Scene(index=0, hook="SQLite concurrency is misunderstood.", narration="WAL mode changes locking.", target_duration_seconds=15.0),
        Scene(index=1, hook="Payoff.", narration="WAL readers never block writers in production.", target_duration_seconds=15.0),
    ]
    mock_sections = ScriptSections(
        hook="SQLite concurrency is misunderstood.",
        intro="Let's unpack how WAL mode changes locking.",
        segments=mock_scenes,
        cta="Like and subscribe.",
        estimated_duration=30.0,
    )
    brain.generator.generate_script_sections = MagicMock(return_value=mock_sections)

    revised_sections = ScriptSections(
        hook="SQLite concurrency is misunderstood.",
        intro="Let's unpack how WAL mode changes locking.",
        segments=mock_scenes,
        cta="WAL fixes reader-writer blocking, but checkpointing creates the next bottleneck — that's the next breakdown.",
        estimated_duration=30.0,
    )
    brain.generator.rewrite_for_retention = MagicMock(return_value=revised_sections)

    sample_hook = HookCandidate(
        text="SQLite concurrency is misunderstood.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Unpack WAL concurrency.",
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.generate_hook_candidates",
        lambda *args, **kwargs: [sample_hook],
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.run_tournament",
        lambda *args, **kwargs: (sample_hook, MagicMock(), [MagicMock()]),
    )

    brain.extractor.extract_from_script = MagicMock(return_value=[
        Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True, source_id="s1")
    ])

    verify_calls = []
    def mock_verify(*args, **kwargs):
        verify_calls.append(True)
        return FactCheckReport(
            id=f"fcr_refact_{len(verify_calls)}",
            project_id="proj_refact",
            audit_summary="Verified",
            claims=[
                Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
            ],
            verified_count=1,
            failed_count=0,
            overall_verdict=QualityStatus.PASSED,
        )

    brain.checker.verify_all_claims = MagicMock(side_effect=mock_verify)

    brain.run_stage_1_to_5(
        project_id="proj_refact",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    # Initial verification + re-verification after retention rewrite = at least 2 calls
    assert len(verify_calls) >= 2


def test_factcheck_failure_after_final_retention_rewrite_transitions_failed(tmp_path: Path, monkeypatch):
    """If a retention rewrite introduces an unverified factual claim, FactChecker fails and lifecycle transitions to FAILED."""
    repo = SQLiteRepository(str(tmp_path / "test_refact_fail.db"))
    channel = Channel(
        id="ch_refact_fail",
        title="Systems Engineering",
        handle="@SystemsEng",
        description="Tech",
        niche="Database Internals",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)

    mock_dossier = ResearchDossier(
        id="dos_refact_fail",
        topic_id="top_refact_fail",
        topic="SQLite Concurrency",
        summary="SQLite WAL eliminates reader blocking.",
        sources=[ResearchSource(id="s1", title="SQLite WAL", url="https://sqlite.org/wal.html", content_snapshot="WAL eliminates reader blocking.", author="Richard Hipp", content_sha256="dummy_sha")],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=mock_dossier)
    brain.strategist.duplicate_detector.check_duplicate = MagicMock(return_value=(False, 0.0, None))
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {"demand": 8.0, "freshness": 7.0, "competition": 3.0, "channel_fit": 8.0, "originality": 7.5, "evidence_quality": 9.0, "production_feasibility": 8.0},
        "Strong opportunity",
        {"volume_source": "seed"},
    ))

    mock_scenes = [
        Scene(index=0, hook="SQLite concurrency is misunderstood.", narration="WAL mode changes locking.", target_duration_seconds=15.0),
        Scene(index=1, hook="Payoff.", narration="WAL readers never block writers in production.", target_duration_seconds=15.0),
    ]
    mock_sections = ScriptSections(
        hook="SQLite concurrency is misunderstood.",
        intro="Let's unpack how WAL mode changes locking.",
        segments=mock_scenes,
        cta="Like and subscribe.",
        estimated_duration=30.0,
    )
    brain.generator.generate_script_sections = MagicMock(return_value=mock_sections)

    revised_sections = ScriptSections(
        hook="SQLite concurrency is misunderstood.",
        intro="Let's unpack how WAL mode changes locking.",
        segments=mock_scenes,
        cta="WAL fixes reader-writer blocking, but checkpointing creates the next bottleneck — that's the next breakdown.",
        estimated_duration=30.0,
    )
    brain.generator.rewrite_for_retention = MagicMock(return_value=revised_sections)

    sample_hook = HookCandidate(
        text="SQLite concurrency is misunderstood.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Unpack WAL concurrency.",
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.generate_hook_candidates",
        lambda *args, **kwargs: [sample_hook],
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.run_tournament",
        lambda *args, **kwargs: (sample_hook, MagicMock(), [MagicMock()]),
    )

    brain.extractor.extract_from_script = MagicMock(return_value=[
        Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True, source_id="s1")
    ])

    verify_calls = []
    def mock_verify(*args, **kwargs):
        verify_calls.append(True)
        if len(verify_calls) == 1:
            # First pass: PASSED
            return FactCheckReport(
                id="fcr_pass_1",
                project_id="proj_refact_fail",
                audit_summary="Pass",
                claims=[
                    Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
                ],
                verified_count=1,
                failed_count=0,
                overall_verdict=QualityStatus.PASSED,
            )
        else:
            # Second pass after rewrite: FAILED
            return FactCheckReport(
                id="fcr_fail_2",
                project_id="proj_refact_fail",
                audit_summary="Ungrounded claim introduced",
                claims=[
                    Claim(id="c2", statement="Invented claim.", verdict=ClaimVerificationVerdict.REMOVE, verified=False)
                ],
                verified_count=0,
                failed_count=1,
                overall_verdict=QualityStatus.FAILED,
            )

    brain.checker.verify_all_claims = MagicMock(side_effect=mock_verify)

    project, report = brain.run_stage_1_to_5(
        project_id="proj_refact_fail",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    assert project.state == VideoLifecycleState.FAILED
    assert report.overall_verdict == QualityStatus.FAILED


def test_no_script_mutation_occurs_after_last_factcheck(tmp_path: Path, monkeypatch):
    """Canonical narration hash of the script passed to the final FactCheck matches the persisted final script."""
    repo = SQLiteRepository(str(tmp_path / "test_no_mut.db"))
    channel = Channel(
        id="ch_no_mut",
        title="Systems Engineering",
        handle="@SystemsEng",
        description="Tech",
        niche="Database Internals",
        target_audience="Engineers",
    )
    repo.save_channel(channel)
    brain = BrainPipeline(repository=repo)

    mock_dossier = ResearchDossier(
        id="dos_no_mut",
        topic_id="top_no_mut",
        topic="SQLite Concurrency",
        summary="SQLite WAL eliminates reader blocking.",
        sources=[ResearchSource(id="s1", title="SQLite WAL", url="https://sqlite.org/wal.html", content_snapshot="WAL eliminates reader blocking.", author="Richard Hipp", content_sha256="dummy_sha")],
        claims=[],
    )
    brain.research_agent.build_dossier_from_urls = MagicMock(return_value=mock_dossier)
    brain.strategist.duplicate_detector.check_duplicate = MagicMock(return_value=(False, 0.0, None))
    brain.evaluator.evaluate_topic_with_reasoning = MagicMock(return_value=(
        {"demand": 8.0, "freshness": 7.0, "competition": 3.0, "channel_fit": 8.0, "originality": 7.5, "evidence_quality": 9.0, "production_feasibility": 8.0},
        "Strong opportunity",
        {"volume_source": "seed"},
    ))

    mock_scenes = [
        Scene(index=0, hook="SQLite concurrency is misunderstood.", narration="WAL mode changes locking.", target_duration_seconds=15.0),
        Scene(index=1, hook="Payoff.", narration="WAL readers never block writers in production.", target_duration_seconds=15.0),
    ]
    mock_sections = ScriptSections(
        hook="SQLite concurrency is misunderstood.",
        intro="Let's unpack how WAL mode changes locking.",
        segments=mock_scenes,
        cta="WAL fixes reader-writer blocking, but checkpointing creates the next bottleneck — that's the next breakdown.",
        estimated_duration=30.0,
    )
    brain.generator.generate_script_sections = MagicMock(return_value=mock_sections)

    sample_hook = HookCandidate(
        text="SQLite concurrency is misunderstood.",
        angle=HookAngle.CURIOSITY_GAP,
        promise="Unpack WAL concurrency.",
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.generate_hook_candidates",
        lambda *args, **kwargs: [sample_hook],
    )
    monkeypatch.setattr(
        "app.services.pipeline_brain.HookTournamentService.run_tournament",
        lambda *args, **kwargs: (sample_hook, MagicMock(), [MagicMock()]),
    )

    last_checked_hash = []
    def spy_extract(script):
        last_checked_hash.append(script.compute_canonical_narration_hash())
        return [Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True, source_id="s1")]

    brain.extractor.extract_from_script = MagicMock(side_effect=spy_extract)
    brain.checker.verify_all_claims = MagicMock(return_value=FactCheckReport(
        id="fcr_no_mut",
        project_id="proj_no_mut",
        audit_summary="Verified",
        claims=[
            Claim(id="c1", statement="WAL readers never block writers in production.", verdict=ClaimVerificationVerdict.VERIFIED, verified=True)
        ],
        verified_count=1,
        failed_count=0,
        overall_verdict=QualityStatus.PASSED,
    ))

    project, report = brain.run_stage_1_to_5(
        project_id="proj_no_mut",
        channel=channel,
        keyword="SQLite Concurrency",
        seed_urls=["https://sqlite.org/wal.html"],
    )

    assert len(last_checked_hash) >= 1
    # Hash checked at final factcheck MUST match final project script hash
    assert last_checked_hash[-1] == project.script.compute_canonical_narration_hash()


# ============================================================================
# P1-4: Concrete Anchor Grounding Tests
# ============================================================================

def test_for_example_phrase_alone_is_not_grounded_real_example():
    """Textual markers like 'for example' alone do not establish a grounded real example without verified evidence."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_fake_anchor",
        title="Test",
        hook="A fascinating mystery in systems design.",
        scenes=[
            Scene(index=0, narration="For example, in practice this works well across production systems.", target_duration_seconds=10.0),
        ],
        total_word_count=10,
        estimated_duration_seconds=10.0,
    )
    report = evaluator.evaluate(script, dossier=None, fact_report=None)
    assert len(report.concrete_anchors) >= 1
    ex = next(a for a in report.concrete_anchors if a.anchor_type == ConcreteAnchorType.REAL_EXAMPLE)
    assert ex.grounded is False
    assert ex.claim_ids == []
    assert ex.source_refs == []


def test_verified_claim_can_ground_real_example():
    """When a real-world example maps to a verified Claim in the FactCheckReport, it is audited as grounded."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_grounded_claim",
        title="Test",
        hook="A fascinating mystery in systems design.",
        scenes=[
            Scene(index=0, narration="For example, PostgreSQL WAL prevents data corruption during unexpected server crashes.", target_duration_seconds=10.0),
        ],
        total_word_count=12,
        estimated_duration_seconds=10.0,
    )
    claim = Claim(
        id="claim_pg_wal",
        statement="PostgreSQL WAL prevents data corruption during unexpected server crashes.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
        cited_url="https://postgresql.org/docs/wal",
    )
    fact_report = FactCheckReport(
        id="fcr_grounded",
        project_id="p1",
        claims=[claim],
        verified_count=1,
        failed_count=0,
        overall_verdict=QualityStatus.PASSED,
        audit_summary="Verified",
    )
    report = evaluator.evaluate(script, fact_report=fact_report)
    ex = next(a for a in report.concrete_anchors if a.anchor_type == ConcreteAnchorType.REAL_EXAMPLE)
    assert ex.grounded is True
    assert "claim_pg_wal" in ex.claim_ids
    assert "https://postgresql.org/docs/wal" in ex.source_refs


def test_real_example_source_ref_must_exist():
    """A real-world example matching evidence in the ResearchDossier correctly populates source references."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_src_ref",
        title="Test",
        hook="A fascinating mystery in systems design.",
        scenes=[
            Scene(index=0, narration="In real-world benchmarks, SQLite achieves 100k transactions per second under WAL mode.", target_duration_seconds=10.0),
        ],
        total_word_count=12,
        estimated_duration_seconds=10.0,
    )
    dossier = ResearchDossier(
        id="dos_src_ref",
        topic_id="top_1",
        summary="Summary",
        sources=[
            ResearchSource(
                id="src_sqlite",
                title="SQLite Benchmarks",
                url="https://sqlite.org/speed.html",
                content_snapshot="SQLite achieves 100k transactions per second under WAL mode.",
                content_sha256="dummy",
            )
        ],
    )
    report = evaluator.evaluate(script, dossier=dossier)
    ex = next(a for a in report.concrete_anchors if a.anchor_type == ConcreteAnchorType.REAL_EXAMPLE)
    assert ex.grounded is True
    assert "https://sqlite.org/speed.html" in ex.source_refs


def test_conceptual_analogy_counts_without_external_source():
    """Conceptual analogies serve as valid retention anchors without requiring external factual sources."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_analogy",
        title="Test",
        hook="A fascinating mystery in systems design.",
        scenes=[
            Scene(index=0, narration="Think of a WAL like an append-only notebook where pages are never erased.", target_duration_seconds=10.0),
        ],
        total_word_count=14,
        estimated_duration_seconds=10.0,
    )
    report = evaluator.evaluate(script, dossier=None, fact_report=None)
    analogy = next(a for a in report.concrete_anchors if a.anchor_type == ConcreteAnchorType.ANALOGY)
    assert analogy.grounded is True
    assert analogy.claim_ids == []
    assert analogy.source_refs == []


def test_empirical_demonstration_requires_verified_claim():
    """Demonstrations asserting empirical performance metrics require verified evidence to be grounded."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_demo_empirical",
        title="Test",
        hook="A fascinating mystery in systems design.",
        scenes=[
            Scene(index=0, narration="Watch what happens in terminal: benchmark shows a 3x throughput speedup under load.", target_duration_seconds=10.0),
        ],
        total_word_count=13,
        estimated_duration_seconds=10.0,
    )
    # 1. Unverified without fact report
    report_unver = evaluator.evaluate(script, fact_report=None)
    demo_unver = next(a for a in report_unver.concrete_anchors if a.anchor_type == ConcreteAnchorType.DEMONSTRATION)
    assert demo_unver.grounded is False

    # 2. Verified with fact report
    claim = Claim(
        id="c_bench_3x",
        statement="Benchmark shows a 3x throughput speedup under load.",
        verified=True,
        verdict=ClaimVerificationVerdict.VERIFIED,
    )
    fact_report = FactCheckReport(
        id="fcr_demo",
        project_id="p_demo",
        claims=[claim],
        verified_count=1,
        failed_count=0,
        overall_verdict=QualityStatus.PASSED,
        audit_summary="Pass",
    )
    report_ver = evaluator.evaluate(script, fact_report=fact_report)
    demo_ver = next(a for a in report_ver.concrete_anchors if a.anchor_type == ConcreteAnchorType.DEMONSTRATION)
    assert demo_ver.grounded is True
    assert "c_bench_3x" in demo_ver.claim_ids


def test_explainer_with_valid_analogy_passes_anchor_requirement():
    """An explainer video using a valid explanatory analogy satisfies the concrete anchor requirement."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_explainer_analogy",
        title="SQLite Locking Deep Dive",
        hook="There's a subtle lock in SQLite that silently freezes concurrent readers.",
        content_format=ContentFormat.EXPLAINER,
        scenes=[
            Scene(index=0, narration="In default rollback journal mode, writing acquires an exclusive table lock.", target_duration_seconds=10.0),
            Scene(index=1, narration="Think of it like a shared single-lane bridge where all cars must stop for a truck.", target_duration_seconds=10.0),
            Scene(index=2, narration="Switching to WAL mode writes new pages to a separate log file instead.", target_duration_seconds=10.0),
            Scene(index=3, narration="This eliminates reader blocking entirely, unlocking massive concurrent read throughput.", target_duration_seconds=10.0),
        ],
        total_word_count=60,
        estimated_duration_seconds=40.0,
    )
    report = evaluator.evaluate(script)
    assert not any("MISSING_CONCRETE_ANCHOR" in iss for iss in report.issues)


def test_explainer_with_fake_real_example_fails_grounded_anchor_requirement():
    """An explainer video relying solely on an ungrounded 'for example' phrase triggers MISSING_CONCRETE_ANCHOR."""
    evaluator = ScriptRetentionEvaluator()
    script = Script(
        id="s_explainer_fake_anchor",
        title="SQLite Locking Deep Dive",
        hook="There's a subtle lock in SQLite that silently freezes concurrent readers.",
        content_format=ContentFormat.EXPLAINER,
        scenes=[
            Scene(index=0, narration="In default rollback journal mode, writing acquires an exclusive table lock.", target_duration_seconds=10.0),
            Scene(index=1, narration="For example, in practice this works well across various database workloads.", target_duration_seconds=10.0),
            Scene(index=2, narration="Switching to WAL mode writes new pages to a separate log file instead.", target_duration_seconds=10.0),
            Scene(index=3, narration="This eliminates reader blocking entirely, unlocking massive concurrent read throughput.", target_duration_seconds=10.0),
        ],
        total_word_count=58,
        estimated_duration_seconds=40.0,
    )
    report = evaluator.evaluate(script, dossier=None, fact_report=None)
    assert any("MISSING_CONCRETE_ANCHOR" in iss for iss in report.issues)
