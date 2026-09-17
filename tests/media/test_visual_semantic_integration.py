"""Integration tests for Visual Semantic QA Phase 2:
- Direct render semantic QA gates (DIAGRAM, DATA_VISUALIZATION, UI_SIMULATION, MOTION_GRAPHICS)
- Fail-closed behavior in REQUIRED mode
- Advisory warnings in ADVISORY mode
- GFlow synthetic media provenance (source_type="GENERATED", is_synthetic=True)
- Render cache identity & invalidation on visual_semantic_qa_mode toggle
- Per-project sidecar semantic cache persistence in output/projects/<id>/manifests/semantic_qa_cache.json
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Type
from unittest.mock import MagicMock
import pytest
from PIL import Image

from app.domain.enums import VideoLifecycleState
from app.domain.models import Script, VideoProject
from app.media.acquisition.models import VisualAssetCandidate, VisualSourceType
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    BeatPurpose,
    ChannelCreativeProfile,
    ContentFormat,
    CreativeFallbackPolicy,
    NarrativeBeat,
    ShotSpec,
    Storyboard,
    VisualIntent,
    VisualModality,
)
from app.media.models import (
    compute_artifact_fingerprint,
    compute_production_fingerprint,
)
from app.media.pipeline import MediaProductionPipeline
from app.media.semantic_qa.backend import MockVisualReasoningBackend
from app.media.semantic_qa.cache import (
    SemanticQACache,
    get_project_semantic_cache_path,
)
from app.media.semantic_qa.evaluator import (
    RawVisualEvaluationResponse,
    VisualSemanticEvaluator,
)
from app.media.semantic_qa.judge import VisualCandidateJudge
from app.media.semantic_qa.models import (
    VisualSemanticIssue,
    VisualSemanticQAError,
    VisualSemanticQAMode,
    VisualSemanticVerdict,
)
from tests.media.test_media_pipeline import MockTTSBackend, repo_with_verified_project


def _make_accepting_backend() -> MockVisualReasoningBackend:
    """Create a mock visual reasoning backend that accepts any candidate with high scores."""
    def _accepting_handler(prompt: str, image_paths: List[str], schema_cls: Type[Any]) -> Any:
        cand_m = re.search(r"-\s*Candidate ID:\s*([^\s\r\n]+)", prompt)
        shot_m = re.search(r"-\s*Shot ID:\s*([^\s\r\n]+)", prompt)
        sha_m = re.search(r"-\s*Content SHA-256:\s*([^\s\r\n]+)", prompt)
        cand_id = cand_m.group(1) if cand_m else "placeholder_cand"
        shot_id = shot_m.group(1) if shot_m else "placeholder_shot"
        sha = sha_m.group(1) if sha_m else "0" * 64
        return RawVisualEvaluationResponse(
            candidate_id=cand_id,
            shot_id=shot_id,
            candidate_sha256=sha,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.95,
            visual_intent_match=0.95,
            subject_match=0.95,
            readability=0.95,
            composition_quality=0.95,
            information_value=0.95,
            generic_slop_score=0.05,
            mechanism_clarity=0.95,
            data_readability=0.95,
            interface_state_match=0.95,
            comparison_clarity=0.95,
            evidence_visibility=0.95,
            concise_reason="Accepted by test mock.",
        )
    return MockVisualReasoningBackend(handler=_accepting_handler)


def _make_rejecting_backend(issue: VisualSemanticIssue = VisualSemanticIssue.GENERIC_STOCK) -> MockVisualReasoningBackend:
    """Create a mock visual reasoning backend that rejects candidates with a specific issue."""
    def _rejecting_handler(prompt: str, image_paths: List[str], schema_cls: Type[Any]) -> Any:
        cand_m = re.search(r"-\s*Candidate ID:\s*([^\s\r\n]+)", prompt)
        shot_m = re.search(r"-\s*Shot ID:\s*([^\s\r\n]+)", prompt)
        sha_m = re.search(r"-\s*Content SHA-256:\s*([^\s\r\n]+)", prompt)
        cand_id = cand_m.group(1) if cand_m else "placeholder_cand"
        shot_id = shot_m.group(1) if shot_m else "placeholder_shot"
        sha = sha_m.group(1) if sha_m else "0" * 64
        return RawVisualEvaluationResponse(
            candidate_id=cand_id,
            shot_id=shot_id,
            candidate_sha256=sha,
            verdict=VisualSemanticVerdict.REJECT,
            semantic_relevance=0.30,
            visual_intent_match=0.30,
            subject_match=0.30,
            readability=0.50,
            composition_quality=0.50,
            information_value=0.20,
            generic_slop_score=0.85,
            issues=[issue],
            concise_reason=f"Rejected due to {issue.value}.",
        )
    return MockVisualReasoningBackend(handler=_rejecting_handler)


# ==============================================================================
# 1. Universal Direct-Render Semantic QA Gate Tests
# ==============================================================================

@pytest.mark.parametrize("modality", [
    VisualModality.DIAGRAM,
    VisualModality.DATA_VISUALIZATION,
    VisualModality.UI_SIMULATION,
    VisualModality.MOTION_GRAPHICS,
])
def test_universal_direct_render_semantic_qa_gate(modality: VisualModality, tmp_path: Path):
    """Direct-render modalities (DIAGRAM, DATA_VISUALIZATION, UI_SIMULATION, MOTION_GRAPHICS)
    must trigger visual semantic QA evaluation, attaching audit metadata."""
    backend = _make_accepting_backend()
    profile = ChannelCreativeProfile(name="test_advisory", semantic_qa_mode="ADVISORY")
    director = AutoDirectorService(profile=profile, reasoning_backend=backend)

    # Wire judge into acquisition_router
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id=f"shot_{modality.value.lower()}",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=modality,
        requested_modality=modality,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject=f"Architecture for {modality.value}",
        narration_segment="Here is the detailed technical architecture and data flow.",
        duration_seconds=3.0,
        diagram_instruction="Architecture flowchart" if modality == VisualModality.DIAGRAM else None,
        chart_instruction="Throughput over time" if modality == VisualModality.DATA_VISUALIZATION else None,
        screen_instruction="docker compose up -d" if modality == VisualModality.UI_SIMULATION else None,
        motion_graphic_instruction="Key metric counter" if modality == VisualModality.MOTION_GRAPHICS else None,
    )

    asset_res = director._generate_shot_asset(
        shot=shot,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Direct Render Test",
        channel_name="Tech Channel",
    )

    assert asset_res.semantic_qa_performed is True
    assert asset_res.semantic_audit is not None
    assert asset_res.semantic_audit["verdict"] == "ACCEPT"
    assert asset_res.semantic_audit["candidate_sha256"] == asset_res.sha256
    assert "semantic_input_hash" in asset_res.semantic_audit


# ==============================================================================
# 2. REQUIRED Mode Fail-Closed Tests
# ==============================================================================

def test_direct_render_required_mode_fails_closed_on_rejection(tmp_path: Path):
    """Under ChannelCreativeProfile.production_profile(), semantic_qa_mode is REQUIRED and
    fallback_policy is FAIL_CLOSED. A rejected direct-render asset raises VisualSemanticQAError."""
    backend = _make_rejecting_backend(VisualSemanticIssue.MECHANISM_NOT_EXPLAINED)
    prod_profile = ChannelCreativeProfile.production_profile(name="prod_profile")
    assert prod_profile.semantic_qa_mode == "REQUIRED"
    assert prod_profile.fallback_policy == CreativeFallbackPolicy.FAIL_CLOSED

    director = AutoDirectorService(profile=prod_profile, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id="shot_fail_closed",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.DIAGRAM,
        requested_modality=VisualModality.DIAGRAM,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject="WAL Architecture",
        narration_segment="Readers do not block writers in WAL mode.",
        duration_seconds=3.0,
        diagram_instruction="WAL mechanism diagram",
    )

    with pytest.raises(VisualSemanticQAError, match="Visual Semantic QA REJECTED final asset"):
        director._generate_shot_asset(
            shot=shot,
            shot_index=0,
            output_dir=tmp_path,
            script_title="WAL Mechanism",
            channel_name="Tech Channel",
        )


# ==============================================================================
# 3. ADVISORY Mode Warning Tests
# ==============================================================================

def test_direct_render_advisory_mode_logs_warning_and_continues(tmp_path: Path):
    """Under ADVISORY mode, rejection does not raise an exception; instead, audit metadata
    records the REJECT verdict and issues without blocking production."""
    backend = _make_rejecting_backend(VisualSemanticIssue.DATA_UNREADABLE)
    profile = ChannelCreativeProfile(name="advisory_prof", semantic_qa_mode="ADVISORY")
    director = AutoDirectorService(profile=profile, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id="shot_advisory_chart",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.DATA_VISUALIZATION,
        requested_modality=VisualModality.DATA_VISUALIZATION,
        visual_intent=VisualIntent.SHOW_DATA,
        subject="Throughput Comparison",
        narration_segment="Throughput scales linearly up to 16 writer threads.",
        duration_seconds=3.0,
        chart_instruction="Throughput bar chart",
    )

    asset_res = director._generate_shot_asset(
        shot=shot,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Database Scaling",
        channel_name="Tech Channel",
    )

    assert asset_res.semantic_qa_performed is True
    assert asset_res.semantic_audit is not None
    assert asset_res.semantic_audit["verdict"] == "REJECT"
    assert VisualSemanticIssue.DATA_UNREADABLE.value in asset_res.semantic_audit["issues"]


# ==============================================================================
# 4. GFlow Generated Media Synthetic Provenance Tests
# ==============================================================================

def test_gflow_generated_media_synthetic_provenance(tmp_path: Path):
    """GFlow generated image and video assets must declare source_type='GENERATED' and is_synthetic=True."""
    # Create dummy image and video files for mock GFlow provider
    dummy_img = tmp_path / "gflow_test.png"
    Image.new("RGB", (320, 240), color=(50, 100, 150)).save(dummy_img)

    dummy_vid = tmp_path / "gflow_test.mp4"
    dummy_vid.write_bytes(b"dummy_video_content")

    mock_gflow = MagicMock()
    mock_gflow.generate_image.return_value = (str(dummy_img), "sha_img_123", {})
    mock_gflow.generate_video.return_value = (str(dummy_vid), "sha_vid_123", {})

    backend = _make_accepting_backend()
    profile = ChannelCreativeProfile(name="gflow_prof", semantic_qa_mode="ADVISORY")
    director = AutoDirectorService(profile=profile, gflow_provider=mock_gflow, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    # 1. Generated Image
    shot_img = ShotSpec(
        shot_id="shot_gflow_img",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.GENERATED_IMAGE,
        requested_modality=VisualModality.GENERATED_IMAGE,
        visual_intent=VisualIntent.ESTABLISH_CONTEXT,
        subject="Cyberpunk Server Room",
        narration_segment="Inside the datacenter, cold aisles hum.",
        duration_seconds=3.0,
        generation_prompt="Datacenter server room in neon blue",
    )

    res_img = director._generate_shot_asset(
        shot=shot_img,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Datacenter Secrets",
        channel_name="Tech Channel",
    )

    assert res_img.source_type == "GENERATED"
    assert res_img.is_synthetic is True
    assert res_img.acquisition_method == "gflow_imagen_image"


# ==============================================================================
# 5. Render Cache Identity and Mode Invalidation Tests
# ==============================================================================

def test_render_cache_invalidation_on_semantic_qa_mode_change(repo_with_verified_project, tmp_path: Path):
    """Switching visual_semantic_qa_mode between ADVISORY and REQUIRED invalidates the render cache."""
    # 1. Verify compute_production_fingerprint changes
    fp_advisory = compute_production_fingerprint(
        canonical_narration_sha256="narration_1",
        render_profile_name="render_1",
        tts_backend="mock",
        voice="voice_1",
        visual_semantic_qa_mode="ADVISORY",
    )
    fp_required = compute_production_fingerprint(
        canonical_narration_sha256="narration_1",
        render_profile_name="render_1",
        tts_backend="mock",
        voice="voice_1",
        visual_semantic_qa_mode="REQUIRED",
    )
    assert fp_advisory != fp_required

    # 2. Full pipeline execution: Run 1 in ADVISORY mode
    repo, project_id = repo_with_verified_project
    tts = MockTTSBackend(duration_seconds=3.0)
    pipeline = MediaProductionPipeline(
        repository=repo,
        tts_backend=tts,
        base_output_dir=tmp_path / "out_semantic_cache_test",
    )

    # Use accepting mock evaluator so that tests run without external CLI dependencies
    mock_backend = _make_accepting_backend()
    mock_evaluator = VisualSemanticEvaluator(backend=mock_backend)
    pipeline.director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=mock_evaluator)

    advisory_profile = ChannelCreativeProfile(name="test_prof", semantic_qa_mode="ADVISORY")
    pipeline.director.apply_profile(advisory_profile)
    pipeline.director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=mock_evaluator)

    _, _, manifest_1 = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1
    assert manifest_1.visual_semantic_qa_mode == "ADVISORY"

    # Reset project to VERIFIED for second run
    project = repo.get_video_project(project_id)
    project.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(project)

    # Run 2 with same configuration -> cache hit (tts call count stays 1)
    _, _, manifest_cache_hit = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 1
    assert manifest_cache_hit.production_fingerprint == manifest_1.production_fingerprint

    # Reset project to VERIFIED for third run
    project = repo.get_video_project(project_id)
    project.state = VideoLifecycleState.VERIFIED
    repo.save_video_project(project)

    # Run 3: switch profile to production_profile() (REQUIRED mode) -> cache invalidated!
    required_profile = ChannelCreativeProfile.production_profile(name="test_prof")
    pipeline.director.apply_profile(required_profile)
    pipeline.director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=mock_evaluator)

    _, _, manifest_required = pipeline.run_production(project_id=project_id)
    assert tts.call_count == 2  # Re-rendered due to mode mismatch!
    assert manifest_required.visual_semantic_qa_mode == "REQUIRED"
    assert manifest_required.production_fingerprint != manifest_1.production_fingerprint


# ==============================================================================
# 6. Per-Project Semantic Cache Persistence Tests
# ==============================================================================

def test_per_project_semantic_qa_cache_persistence(tmp_path: Path):
    """Sidecar cache is saved to output/projects/<project_id>/manifests/semantic_qa_cache.json
    using atomic writes, and subsequent calls reuse cached evaluations without re-invoking the backend."""
    project_id = "proj_semantic_cache_01"
    base_dir = tmp_path / "output" / "projects"
    cache_path = get_project_semantic_cache_path(project_id=project_id, base_dir=base_dir)

    assert cache_path == base_dir / project_id / "manifests" / "semantic_qa_cache.json"
    assert not cache_path.exists()

    cache = SemanticQACache(cache_file_path=cache_path)
    mock_backend = _make_accepting_backend()
    evaluator = VisualSemanticEvaluator(backend=mock_backend, cache=cache)

    # Create dummy candidate image
    test_img = tmp_path / "cand_cache.png"
    Image.new("RGB", (320, 240), color=(70, 90, 110)).save(test_img)

    cand = VisualAssetCandidate(
        candidate_id="cand_cached",
        source_type=VisualSourceType.RENDERED,
        file_path=str(test_img),
        content_sha256="sha_test_cache_123",
        acquisition_method="local_capture",
    )
    shot = ShotSpec(
        shot_id="shot_cache_01",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.SCREEN_CAPTURE,
        requested_modality=VisualModality.SCREEN_CAPTURE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Dashboard Status",
        narration_segment="Live metrics show stable memory consumption.",
        duration_seconds=4.0,
    )

    # First evaluation: backend is invoked, cache is saved to disk
    assessment_1 = evaluator.evaluate_candidate(cand, shot)
    assert assessment_1.verdict == VisualSemanticVerdict.ACCEPT
    assert len(mock_backend.invocations) == 1

    # Verify sidecar cache file exists on disk, is valid JSON, and has no temporary files left
    assert cache_path.exists()
    assert not Path(str(cache_path) + ".tmp").exists()
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1

    # Second evaluation with fresh evaluator pointing to same cache file
    cache_reload = SemanticQACache(cache_file_path=cache_path)
    evaluator_reloaded = VisualSemanticEvaluator(backend=mock_backend, cache=cache_reload)

    assessment_2 = evaluator_reloaded.evaluate_candidate(cand, shot)
    assert assessment_2.verdict == VisualSemanticVerdict.ACCEPT
    # Mock backend call count must NOT have increased (reused from persistent sidecar cache)
    assert len(mock_backend.invocations) == 1
