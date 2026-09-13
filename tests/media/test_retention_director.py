"""Tests for retention-aware visual direction, TTS timestamp mapping, and trust boundary enforcement."""

import pytest
from pathlib import Path
from pydantic import ValidationError

from app.domain.enums import ContentFormat, RetentionCueType
from app.domain.models import (
    Claim,
    FactCheckReport,
    HookAngle,
    HookCandidate,
    ResearchDossier,
    ResearchSource,
    RetentionBlueprint,
    RetentionCue,
    Scene,
    Script,
    ScriptSections,
    TimedRetentionCue,
    VideoCreativeBrief,
)
from app.media.director.models import (
    ChartDatum,
    EvidenceBinding,
    NarrativeBeat,
    ProposedNarrativeBeat,
    ShotSpec,
    Storyboard,
    VisualIntent as DirVisualIntent,
    VisualModality as DirVisualModality,
)
from app.media.director.quality_evaluator import VisualShotEvaluator
from app.media.director.storyboard_planner import StoryboardPlanner
from app.services.retention_planner import map_retention_cues_to_timestamps


def test_tts_maps_retention_cues_to_real_timestamps():
    """Verify ratio-based retention cues map accurately to actual TTS duration and word timings."""
    cues = [
        RetentionCue(
            cue_id="cue_hook",
            cue_type=RetentionCueType.OPEN_LOOP,
            target_position_ratio=0.10,
            purpose="Open curiosity loop",
            anchor_text="locking mechanism",
        ),
        RetentionCue(
            cue_id="cue_rehook",
            cue_type=RetentionCueType.REHOOK,
            target_position_ratio=0.42,
            purpose="Re-engage audience before drop-off",
            anchor_text="bottleneck",
        ),
        RetentionCue(
            cue_id="cue_climax",
            cue_type=RetentionCueType.CLIMAX,
            target_position_ratio=0.85,
            purpose="Deliver definitive resolution",
            anchor_text="unrelated_term",
        ),
    ]

    total_audio_duration = 38.7
    # Word timing events from TTS
    timing_events = [
        {"word": "the", "start": 0.2, "end": 0.4},
        {"word": "locking", "start": 3.85, "end": 4.2},
        {"word": "mechanism", "start": 4.2, "end": 4.6},
        {"word": "bottleneck", "start": 16.25, "end": 16.8},
    ]

    timed_cues = map_retention_cues_to_timestamps(
        cues=cues,
        total_duration_seconds=total_audio_duration,
        timing_events=timing_events,
        canonical_narration="The locking mechanism causes a bottleneck before the final solution.",
    )

    assert len(timed_cues) == 3
    # cue_hook matched word "locking" at 3.85s
    assert timed_cues[0].timestamp_seconds == 3.85
    assert timed_cues[0].cue_id == "cue_hook"

    # cue_rehook matched word "bottleneck" at 16.25s
    assert timed_cues[1].timestamp_seconds == 16.25
    assert timed_cues[1].cue_type == RetentionCueType.REHOOK

    # cue_climax did not match word timing, fell back to 0.85 * 38.7 = 32.895s
    assert timed_cues[2].timestamp_seconds == pytest.approx(32.895, abs=0.01)
    assert timed_cues[2].cue_type == RetentionCueType.CLIMAX


def test_pattern_interrupt_requests_semantic_modality_change():
    """StoryboardPlanner executes semantically justified modality shift at PATTERN_INTERRUPT cues."""
    planner = StoryboardPlanner()

    script = Script(
        id="scr_pattern_test",
        title="SQLite WAL Mode",
        hook="Why SQLite WAL is misunderstood.",
        scenes=[
            Scene(
                index=0,
                hook="Architecture diagram",
                narration="In default rollback journal mode, SQLite uses exclusive locking for writers.",
                target_duration_seconds=10.0,
                visual_prompt="Architecture diagram of SQLite rollback locking",
            ),
            Scene(
                index=1,
                hook="Code proof",
                narration="Watch this Python worker script execute concurrent queries against the database.",
                target_duration_seconds=10.0,
                visual_prompt="Terminal running python test script",
            ),
        ],
        total_word_count=30,
        estimated_duration_seconds=20.0,
        content_format=ContentFormat.EXPLAINER,
    )

    beats = [
        NarrativeBeat(
            beat_id="b_01",
            scene_index=0,
            narration=script.scenes[0].narration,
            start_hint=0.0,
            duration_hint=10.0,
            preferred_modalities=[DirVisualModality.DIAGRAM],
            key_entities=["SQLite", "rollback journal"],
        ),
        NarrativeBeat(
            beat_id="b_02",
            scene_index=1,
            narration=script.scenes[1].narration,
            start_hint=10.0,
            duration_hint=10.0,
            preferred_modalities=[DirVisualModality.DIAGRAM],  # Without cue, would route to DIAGRAM
            key_entities=["Python worker", "database query"],
        ),
    ]

    retention_cues = [
        TimedRetentionCue(
            cue_id="cue_pi_1",
            cue_type=RetentionCueType.PATTERN_INTERRUPT,
            timestamp_seconds=10.0,
            narration_anchor="Python worker",
        )
    ]

    storyboard = planner.plan_storyboard(
        project_id="proj_pi_test",
        script=script,
        beats=beats,
        total_audio_duration=20.0,
        content_format=ContentFormat.EXPLAINER,
        retention_cues=retention_cues,
    )

    assert len(storyboard.shots) == 2
    # Shot 0 is DIAGRAM
    assert storyboard.shots[0].visual_modality == DirVisualModality.DIAGRAM
    # Shot 1 under PATTERN_INTERRUPT shifts to CODE_ANIMATION (semantic code/terminal transition)
    assert storyboard.shots[1].visual_modality == DirVisualModality.CODE_ANIMATION
    assert storyboard.shots[1].camera_motion == "PATTERN_INTERRUPT_SNAP"
    assert storyboard.shots[1].importance >= 0.85


def test_retention_cue_does_not_override_grounding_requirements():
    """Retention cues cannot force DOCUMENT_EVIDENCE or DATA_VISUALIZATION without verified bindings."""
    planner = StoryboardPlanner()

    script = Script(
        id="scr_ground_test",
        title="Database Performance",
        hook="Discover the hidden truth.",
        scenes=[
            Scene(
                index=0,
                narration="Motion graphics introducing the database problem.",
                target_duration_seconds=10.0,
                visual_prompt="Show motion graphics text",
            ),
            Scene(
                index=1,
                narration="Here is an ungrounded claim about performance without citation.",
                target_duration_seconds=10.0,
                visual_prompt="Show official document evidence",
            ),
        ],
        total_word_count=20,
        estimated_duration_seconds=20.0,
        content_format=ContentFormat.EXPLAINER,
    )

    beats = [
        NarrativeBeat(
            beat_id="b_01",
            scene_index=0,
            narration=script.scenes[0].narration,
            start_hint=0.0,
            duration_hint=10.0,
            preferred_modalities=[DirVisualModality.MOTION_GRAPHICS],
        ),
        NarrativeBeat(
            beat_id="b_02",
            scene_index=1,
            narration=script.scenes[1].narration,
            start_hint=10.0,
            duration_hint=10.0,
            preferred_modalities=[DirVisualModality.MOTION_GRAPHICS],
            source_refs=[],  # NO verified sources!
        ),
    ]

    # Cue requests PATTERN_INTERRUPT at beat 1 (which normally prefers DOCUMENT_EVIDENCE from motion graphics)
    retention_cues = [
        TimedRetentionCue(
            cue_id="cue_pi_ground",
            cue_type=RetentionCueType.PATTERN_INTERRUPT,
            timestamp_seconds=10.0,
        )
    ]

    storyboard = planner.plan_storyboard(
        project_id="proj_ground_test",
        script=script,
        beats=beats,
        total_audio_duration=20.0,
        content_format=ContentFormat.EXPLAINER,
        dossier=None,  # No verified dossier
        fact_report=None,  # No verified fact report
        retention_cues=retention_cues,
    )

    # Must NOT select DOCUMENT_EVIDENCE without binding; safely falls back to UI_SIMULATION
    shot_1 = storyboard.shots[1]
    assert shot_1.visual_modality != DirVisualModality.DOCUMENT_EVIDENCE
    assert shot_1.evidence_binding is None


def test_retention_layer_cannot_create_trusted_provenance():
    """Retention planning objects (RetentionCue, RetentionBlueprint, TimedRetentionCue) cannot assert trusted provenance."""
    # RetentionCue schema must not contain trust fields
    cue_fields = set(RetentionCue.model_fields.keys())
    assert "claim_id" not in cue_fields
    assert "source_ref" not in cue_fields
    assert "evidence_binding" not in cue_fields
    assert "chart_data" not in cue_fields
    assert "verified" not in cue_fields

    # TimedRetentionCue schema must not contain trust fields
    timed_fields = set(TimedRetentionCue.model_fields.keys())
    assert "claim_id" not in timed_fields
    assert "source_ref" not in timed_fields
    assert "evidence_binding" not in timed_fields
    assert "chart_data" not in timed_fields
    assert "verified" not in timed_fields

    # RetentionBlueprint schema must not contain trust fields
    bp_fields = set(RetentionBlueprint.model_fields.keys())
    assert "claim_id" not in bp_fields
    assert "source_ref" not in bp_fields
    assert "evidence_binding" not in bp_fields
    assert "chart_data" not in bp_fields
    assert "verified" not in bp_fields


def test_retention_visual_qa_detects_missed_cue_or_no_modality_change():
    """VisualShotEvaluator flags PATTERN_INTERRUPT_NO_MODALITY_CHANGE and RETENTION_CUE_MISSED."""
    evaluator = VisualShotEvaluator()

    storyboard = Storyboard(
        project_id="proj_qa_test",
        total_duration=20.0,
        content_format=ContentFormat.EXPLAINER,
        beats=[],
        shots=[
            ShotSpec(
                shot_id="shot_01",
                beat_id="b_01",
                narration_segment="First explanation scene.",
                duration_seconds=10.0,
                visual_modality=DirVisualModality.DIAGRAM,
            ),
            ShotSpec(
                shot_id="shot_02",
                beat_id="b_02",
                narration_segment="Second explanation scene unchanged.",
                duration_seconds=10.0,
                visual_modality=DirVisualModality.DIAGRAM,  # Failed to change modality
            ),
        ],
    )

    cues = [
        TimedRetentionCue(
            cue_id="cue_pi_fail",
            cue_type=RetentionCueType.PATTERN_INTERRUPT,
            timestamp_seconds=10.0,
        ),
        TimedRetentionCue(
            cue_id="cue_out_of_bounds",
            cue_type=RetentionCueType.REHOOK,
            timestamp_seconds=45.0,  # Beyond total duration of 20.0s
        ),
    ]

    report = evaluator.generate_quality_report(
        timeline=None,
        storyboard=storyboard,
        retention_cues=cues,
    )

    assert any("PATTERN_INTERRUPT_NO_MODALITY_CHANGE" in w for w in report.warnings)
    assert any("RETENTION_CUE_MISSED" in w for w in report.warnings)
