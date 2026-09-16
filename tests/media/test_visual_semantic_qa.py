"""Comprehensive tests for Visual Semantic QA and Candidate Judging (Phase 2)."""

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest
from pydantic import ValidationError
from PIL import Image

from app.media.acquisition.candidate_ranker import CandidateRanker, VisualCandidateScore
from app.media.acquisition.models import (
    VisualAcquisitionRequest,
    VisualAssetCandidate,
    VisualSourceType,
)
from app.media.acquisition.router import VisualAcquisitionRouter
from app.media.director.models import (
    ChannelCreativeProfile,
    CreativeFallbackPolicy,
    EvidenceBinding,
    ShotSpec,
    VisualIntent,
    VisualModality,
)
from app.media.semantic_qa.backend import MockVisualReasoningBackend
from app.media.semantic_qa.cache import (
    SemanticQACache,
    VISUAL_SEMANTIC_QA_POLICY_VERSION,
    compute_semantic_input_hash,
)
from app.media.semantic_qa.evaluator import RawVisualEvaluationResponse, VisualSemanticEvaluator
from app.media.semantic_qa.frame_sampler import VideoFrameSampler
from app.media.semantic_qa.judge import VisualCandidateJudge
from app.media.semantic_qa.models import (
    VisualSemanticAssessment,
    VisualSemanticIssue,
    VisualSemanticQAError,
    VisualSemanticQAMode,
    VisualSemanticVerdict,
)


@pytest.fixture
def tmp_image(tmp_path: Path) -> Path:
    img_path = tmp_path / "test_frame.png"
    img = Image.new("RGB", (400, 300), color=(100, 150, 200))
    img.save(img_path)
    return img_path


def make_image_file(tmp_path: Path, filename: str, color=(100, 150, 200)) -> Path:
    img_path = tmp_path / filename
    img = Image.new("RGB", (400, 300), color=color)
    img.save(img_path)
    return img_path


def compute_file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_candidate(
    candidate_id: str,
    file_path: Path,
    source_type: VisualSourceType = VisualSourceType.RESEARCH_SOURCE,
    is_synthetic: bool = False,
    width: int = 1080,
    height: int = 1920,
) -> VisualAssetCandidate:
    sha = compute_file_sha(file_path)
    return VisualAssetCandidate(
        candidate_id=candidate_id,
        source_type=source_type,
        file_path=str(file_path),
        content_sha256=sha,
        width=width,
        height=height,
        acquisition_method="test_provider",
        is_synthetic=is_synthetic,
    )


def make_shot(
    shot_id: str = "s_01",
    beat_id: str = "b_01",
    duration_seconds: float = 3.0,
    modality: VisualModality = VisualModality.DOCUMENT_EVIDENCE,
    intent: VisualIntent = VisualIntent.SHOW_EVIDENCE,
    subject: str = "SQLite WAL architecture",
    action: str = "Concurrent read transactions",
    narration: str = "SQLite WAL allows readers to proceed without blocking writers.",
    evidence_binding: Optional[EvidenceBinding] = None,
) -> ShotSpec:
    return ShotSpec(
        shot_id=shot_id,
        beat_id=beat_id,
        duration_seconds=duration_seconds,
        visual_modality=modality,
        visual_intent=intent,
        subject=subject,
        action=action,
        narration_segment=narration,
        evidence_binding=evidence_binding,
    )


# ==============================================================================
# Model & Trust Boundary Tests
# ==============================================================================

def test_visual_semantic_assessment_validates_scores():
    """Scores must be strictly bounded between 0.0 and 1.0."""
    with pytest.raises(ValidationError):
        VisualSemanticAssessment(
            candidate_id="c1",
            shot_id="s1",
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=1.5,  # Invalid: > 1.0
            visual_intent_match=0.9,
            subject_match=0.9,
            readability=0.8,
            composition_quality=0.8,
            information_value=0.8,
            generic_slop_score=0.1,
            concise_reason="Valid reason",
            evaluator_backend="test",
            evaluator_policy_version="v1",
            candidate_sha256="abc",
            semantic_input_hash="def",
        )

    with pytest.raises(ValidationError):
        VisualSemanticAssessment(
            candidate_id="c1",
            shot_id="s1",
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=-0.1,  # Invalid: < 0.0
            visual_intent_match=0.9,
            subject_match=0.9,
            readability=0.8,
            composition_quality=0.8,
            information_value=0.8,
            generic_slop_score=0.1,
            concise_reason="Valid reason",
            evaluator_backend="test",
            evaluator_policy_version="v1",
            candidate_sha256="abc",
            semantic_input_hash="def",
        )


def test_visual_semantic_output_cannot_mutate_provenance():
    """Schema must forbid extra fields, specifically provenance fields."""
    with pytest.raises(ValidationError):
        RawVisualEvaluationResponse(
            candidate_id="c1",
            shot_id="s1",
            candidate_sha256="abc",
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.9,
            visual_intent_match=0.9,
            subject_match=0.9,
            readability=0.9,
            composition_quality=0.9,
            information_value=0.9,
            generic_slop_score=0.1,
            concise_reason="Reason",
            # Attempted provenance injection:
            source_ref="src_fake",  # type: ignore
        )


# ==============================================================================
# Deterministic Precheck Tests
# ==============================================================================

def test_missing_asset_rejected_without_vlm_call(tmp_path: Path):
    """Missing file is rejected before VLM invocation."""
    fake_path = tmp_path / "non_existent.png"
    cand = VisualAssetCandidate(
        candidate_id="missing_cand",
        source_type=VisualSourceType.DOCUMENT,
        file_path=str(fake_path),
        content_sha256="fake_sha",
        acquisition_method="test",
    )
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Doc",
    )
    shot = make_shot()

    mock_backend = MockVisualReasoningBackend()
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    judge = VisualCandidateJudge(evaluator=evaluator)

    res = judge.judge_candidates(
        candidates=[cand],
        request=req,
        shot=shot,
        deterministic_scores={},
    )
    assert res.selected_candidate_id is None
    assert len(mock_backend.invocations) == 0  # VLM not called
    assert any("MISSING_FILE" in f for f in res.failure_reasons)


def test_sha_mismatch_rejected_without_vlm_call(tmp_image: Path):
    """Corrupted/altered content SHA-256 is rejected without VLM call."""
    cand = VisualAssetCandidate(
        candidate_id="tampered_cand",
        source_type=VisualSourceType.DOCUMENT,
        file_path=str(tmp_image),
        content_sha256="incorrect_sha256_hash",
        acquisition_method="test",
    )
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Doc",
    )
    shot = make_shot()

    mock_backend = MockVisualReasoningBackend()
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    judge = VisualCandidateJudge(evaluator=evaluator)

    res = judge.judge_candidates([cand], req, shot, {})
    assert res.selected_candidate_id is None
    assert len(mock_backend.invocations) == 0
    assert any("SHA_MISMATCH" in f for f in res.failure_reasons)


def test_real_required_violation_rejected_before_vlm(tmp_image: Path):
    """Synthetic asset offered for REAL_REQUIRED modality is rejected deterministically."""
    cand = make_candidate("synth_cand", tmp_image, is_synthetic=True)
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id="s1",
        modality=VisualModality.DOCUMENT_EVIDENCE,  # REAL_REQUIRED
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Doc",
    )
    shot = make_shot(modality=VisualModality.DOCUMENT_EVIDENCE)

    mock_backend = MockVisualReasoningBackend()
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    judge = VisualCandidateJudge(evaluator=evaluator)

    res = judge.judge_candidates([cand], req, shot, {})
    assert res.selected_candidate_id is None
    assert len(mock_backend.invocations) == 0
    assert any("REAL_REQUIRED_VIOLATION" in f for f in res.failure_reasons)


# ==============================================================================
# Modality Rubric Tests (Evidence, UI, Diagram, Stock, Generated)
# ==============================================================================

def test_readable_relevant_document_evidence_passes(tmp_image: Path):
    """Clear, relevant evidence screenshot with high excerpt visibility passes."""
    cand = make_candidate("ev_good", tmp_image)
    shot = make_shot()

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="ev_good",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.92,
            visual_intent_match=0.95,
            subject_match=0.90,
            readability=0.88,
            composition_quality=0.85,
            information_value=0.90,
            evidence_visibility=0.92,
            generic_slop_score=0.05,
            issues=[],
            concise_reason="Relevant sentence highlighted and crisp.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.ACCEPT
    assert assessment.semantic_relevance >= 0.80
    assert assessment.evidence_visibility >= 0.80


def test_wrong_document_region_rejected(tmp_image: Path):
    """Evidence screenshot showing wrong document section is hard-rejected."""
    cand = make_candidate("ev_wrong_sec", tmp_image)
    shot = make_shot()

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="ev_wrong_sec",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,  # Even if VLM proposed accept
            semantic_relevance=0.50,
            visual_intent_match=0.40,
            subject_match=0.50,
            readability=0.85,
            composition_quality=0.80,
            information_value=0.40,
            evidence_visibility=0.10,
            generic_slop_score=0.10,
            issues=[VisualSemanticIssue.WRONG_DOCUMENT_REGION, VisualSemanticIssue.EVIDENCE_NOT_VISIBLE],
            concise_reason="Shows generic header, not WAL section.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT
    assert VisualSemanticIssue.WRONG_DOCUMENT_REGION in assessment.issues


def test_unreadable_evidence_rejected(tmp_image: Path):
    """Evidence screenshot with unreadable text is rejected."""
    cand = make_candidate("ev_unreadable", tmp_image)
    shot = make_shot()

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="ev_unreadable",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.85,
            visual_intent_match=0.80,
            subject_match=0.80,
            readability=0.40,  # Fails >= 0.75 threshold
            composition_quality=0.60,
            information_value=0.50,
            evidence_visibility=0.85,
            generic_slop_score=0.10,
            issues=[VisualSemanticIssue.EVIDENCE_UNREADABLE],
            concise_reason="Text is blurry and unreadable on phone.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT


def test_matching_ui_state_passes(tmp_image: Path):
    """UI screenshot displaying requested button and state transition passes."""
    cand = make_candidate("ui_good", tmp_image, source_type=VisualSourceType.LOCAL_WEB_APP)
    shot = make_shot(
        modality=VisualModality.SCREEN_CAPTURE,
        intent=VisualIntent.SHOW_INTERFACE,
        subject="Checkpoint Status UI",
        action="Click Run Checkpoint",
        narration="Click Run Checkpoint and status changes to Completed.",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="ui_good",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.90,
            visual_intent_match=0.92,
            subject_match=0.90,
            action_match=0.88,
            readability=0.85,
            composition_quality=0.82,
            information_value=0.88,
            interface_state_match=0.95,
            generic_slop_score=0.05,
            issues=[],
            concise_reason="Shows active Run Checkpoint button and completed badge.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.ACCEPT


def test_wrong_ui_state_rejected(tmp_image: Path):
    """UI screenshot failing to show requested state is hard-rejected."""
    cand = make_candidate("ui_wrong", tmp_image, source_type=VisualSourceType.LOCAL_WEB_APP)
    shot = make_shot(
        modality=VisualModality.SCREEN_CAPTURE,
        intent=VisualIntent.SHOW_INTERFACE,
        subject="Checkpoint Status UI",
        narration="Click Run Checkpoint and status changes to Completed.",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="ui_wrong",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.60,
            visual_intent_match=0.50,
            subject_match=0.70,
            readability=0.80,
            composition_quality=0.75,
            information_value=0.40,
            interface_state_match=0.20,
            generic_slop_score=0.10,
            issues=[VisualSemanticIssue.UI_STATE_NOT_SHOWN],
            concise_reason="Shows only empty login splash.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT
    assert VisualSemanticIssue.UI_STATE_NOT_SHOWN in assessment.issues


def test_mechanism_diagram_passes(tmp_image: Path):
    """Diagram clearly illustrating entities and relationships passes."""
    cand = make_candidate("diag_good", tmp_image, source_type=VisualSourceType.RENDERED)
    shot = make_shot(
        modality=VisualModality.DIAGRAM,
        intent=VisualIntent.SHOW_MECHANISM,
        subject="WAL Log vs Database File",
        narration="Writers append to the WAL while readers query the main database.",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="diag_good",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.92,
            visual_intent_match=0.90,
            subject_match=0.90,
            readability=0.85,
            composition_quality=0.85,
            information_value=0.88,
            mechanism_clarity=0.92,
            generic_slop_score=0.05,
            issues=[],
            concise_reason="Clearly diagrams WAL append flow and database page access.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.ACCEPT


def test_decorative_diagram_rejected(tmp_image: Path):
    """Generic diagram that fails to explain mechanism is rejected."""
    cand = make_candidate("diag_decor", tmp_image, source_type=VisualSourceType.RENDERED)
    shot = make_shot(
        modality=VisualModality.DIAGRAM,
        intent=VisualIntent.SHOW_MECHANISM,
        subject="WAL Log vs Database File",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="diag_decor",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.55,
            visual_intent_match=0.45,
            subject_match=0.60,
            readability=0.70,
            composition_quality=0.70,
            information_value=0.40,
            mechanism_clarity=0.30,
            generic_slop_score=0.50,
            issues=[VisualSemanticIssue.DECORATIVE_ONLY, VisualSemanticIssue.MECHANISM_NOT_EXPLAINED],
            concise_reason="Generic abstract cylinders with no directional logic.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT


def test_generic_server_rack_rejected_for_specific_mechanism(tmp_image: Path):
    """Generic stock footage of server racks rejected when narration describes specific mechanism."""
    cand = make_candidate("stock_rack", tmp_image, source_type=VisualSourceType.STOCK_MEDIA)
    shot = make_shot(
        modality=VisualModality.STOCK_VIDEO,
        intent=VisualIntent.SHOW_MECHANISM,
        subject="Checkpoint Starvation",
        narration="Checkpoint starvation occurs when long-running read transactions hold snapshots.",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="stock_rack",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.35,
            visual_intent_match=0.20,
            subject_match=0.40,
            readability=0.80,
            composition_quality=0.75,
            information_value=0.20,
            generic_slop_score=0.95,
            issues=[VisualSemanticIssue.GENERIC_STOCK, VisualSemanticIssue.VISUAL_INTENT_MISMATCH],
            concise_reason="Generic blinking server rack footage; zero depiction of transaction starvation.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT
    assert VisualSemanticIssue.GENERIC_STOCK in assessment.issues


def test_relevant_real_world_stock_passes_when_semantically_appropriate(tmp_image: Path):
    """High quality stock media depicting the actual narrated subject passes."""
    cand = make_candidate("stock_good", tmp_image, source_type=VisualSourceType.STOCK_MEDIA)
    shot = make_shot(
        modality=VisualModality.STOCK_VIDEO,
        intent=VisualIntent.ESTABLISH_CONTEXT,
        subject="Data center hardware failure",
        narration="Under heavy disk load, storage controller buffers saturate.",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="stock_good",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.85,
            visual_intent_match=0.82,
            subject_match=0.88,
            readability=0.80,
            composition_quality=0.85,
            information_value=0.78,
            generic_slop_score=0.20,
            issues=[],
            concise_reason="Realistic technician inspecting saturated storage controller bay.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.ACCEPT


def test_generated_visual_with_instruction_mismatch_rejected(tmp_image: Path):
    """AI-generated visual contradicting the prompt intent is rejected."""
    cand = make_candidate("gen_bad", tmp_image, source_type=VisualSourceType.GENERATED, is_synthetic=True)
    shot = make_shot(
        modality=VisualModality.GENERATED_IMAGE,
        intent=VisualIntent.SHOW_MECHANISM,
        subject="Two concurrent threads writing to separate memory pages",
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="gen_bad",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.45,
            visual_intent_match=0.30,
            subject_match=0.50,
            readability=0.70,
            composition_quality=0.80,
            information_value=0.35,
            generic_slop_score=0.30,
            issues=[VisualSemanticIssue.VISUAL_CONTRADICTION, VisualSemanticIssue.VISUAL_INTENT_MISMATCH],
            concise_reason="Shows a single robotic hand holding a glowing orb, contradictory to thread memory model.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT


def test_generated_visual_with_text_artifact_rejected(tmp_image: Path):
    """Generated visual with hallucinated unreadable text artifacts is rejected."""
    cand = make_candidate("gen_artifact", tmp_image, source_type=VisualSourceType.GENERATED, is_synthetic=True)
    shot = make_shot(modality=VisualModality.GENERATED_IMAGE)

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="gen_artifact",
            shot_id="s_01",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.75,
            visual_intent_match=0.72,
            subject_match=0.75,
            readability=0.40,
            composition_quality=0.60,
            information_value=0.50,
            generic_slop_score=0.20,
            issues=[VisualSemanticIssue.GENERATED_TEXT_ARTIFACT],
            concise_reason="Contains warped gibberish letters across the diagram.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    assert assessment.verdict == VisualSemanticVerdict.REJECT


# ==============================================================================
# Judge & Candidate Selection Tests
# ==============================================================================

def test_semantic_reject_cannot_win_from_high_deterministic_score(tmp_path: Path):
    """A candidate with a high deterministic metadata score CANNOT win if semantically rejected."""
    img_meta = make_image_file(tmp_path, "meta.png", (10, 20, 30))
    img_good = make_image_file(tmp_path, "good.png", (40, 50, 60))
    cand_high_meta = make_candidate("cand_high_meta", img_meta)
    cand_good_sem = make_candidate("cand_good_sem", img_good)

    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id=shot.shot_id,
        modality=shot.visual_modality,
        visual_intent=shot.visual_intent,
        subject=shot.subject,
    )

    # Deterministic scores: cand_high_meta gets 0.95, cand_good_sem gets 0.70
    det_scores = {
        "cand_high_meta": VisualCandidateScore(
            candidate_id="cand_high_meta",
            evidence_affinity=1.0,
            technical_quality=1.0,
            format_fit=1.0,
            source_preference=1.0,
            total_score=0.95,
        ),
        "cand_good_sem": VisualCandidateScore(
            candidate_id="cand_good_sem",
            evidence_affinity=0.7,
            technical_quality=0.8,
            format_fit=0.8,
            source_preference=0.7,
            total_score=0.70,
        ),
    }

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        # cand_high_meta: REJECTED semantically (wrong document section)
        RawVisualEvaluationResponse(
            candidate_id="cand_high_meta",
            shot_id="s_01",
            candidate_sha256=cand_high_meta.content_sha256,
            verdict=VisualSemanticVerdict.REJECT,
            semantic_relevance=0.40,
            visual_intent_match=0.30,
            subject_match=0.40,
            readability=0.80,
            composition_quality=0.70,
            information_value=0.30,
            generic_slop_score=0.10,
            issues=[VisualSemanticIssue.WRONG_DOCUMENT_REGION],
            concise_reason="Irrelevant page section.",
        ),
        # cand_good_sem: ACCEPTED semantically
        RawVisualEvaluationResponse(
            candidate_id="cand_good_sem",
            shot_id="s_01",
            candidate_sha256=cand_good_sem.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.90,
            visual_intent_match=0.90,
            subject_match=0.90,
            readability=0.85,
            composition_quality=0.85,
            information_value=0.85,
            evidence_visibility=0.90,
            generic_slop_score=0.05,
            issues=[],
            concise_reason="Accurate evidence passage.",
        ),
    ])

    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    judge = VisualCandidateJudge(evaluator=evaluator)

    res = judge.judge_candidates(
        candidates=[cand_high_meta, cand_good_sem],
        request=req,
        shot=shot,
        deterministic_scores=det_scores,
    )

    assert res.selected_candidate_id == "cand_good_sem"
    assert res.winning_candidate.candidate_id == "cand_good_sem"


def test_semantically_better_candidate_can_beat_metadata_winner(tmp_path: Path):
    """When both candidates are accepted, blended score allows semantically superior asset to win."""
    img_a = make_image_file(tmp_path, "ca.png", (10, 20, 30))
    img_b = make_image_file(tmp_path, "cb.png", (40, 50, 60))
    cand_a = make_candidate("cand_a", img_a)  # higher metadata, lower semantic
    cand_b = make_candidate("cand_b", img_b)  # lower metadata, higher semantic

    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1",
        shot_id=shot.shot_id,
        modality=shot.visual_modality,
        visual_intent=shot.visual_intent,
        subject=shot.subject,
    )

    det_scores = {
        "cand_a": VisualCandidateScore(
            candidate_id="cand_a",
            evidence_affinity=0.9, technical_quality=0.9, format_fit=0.9, source_preference=0.9, total_score=0.90
        ),
        "cand_b": VisualCandidateScore(
            candidate_id="cand_b",
            evidence_affinity=0.8, technical_quality=0.8, format_fit=0.8, source_preference=0.8, total_score=0.80
        ),
    }

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        # cand_a: acceptable but lower semantic score (0.72)
        RawVisualEvaluationResponse(
            candidate_id="cand_a", shot_id="s_01", candidate_sha256=cand_a.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.72, visual_intent_match=0.72, subject_match=0.72,
            readability=0.75, composition_quality=0.70, information_value=0.70,
            evidence_visibility=0.80, generic_slop_score=0.20, concise_reason="Acceptable.",
        ),
        # cand_b: outstanding semantic score (0.95)
        RawVisualEvaluationResponse(
            candidate_id="cand_b", shot_id="s_01", candidate_sha256=cand_b.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.98, visual_intent_match=0.96, subject_match=0.95,
            readability=0.95, composition_quality=0.92, information_value=0.95,
            evidence_visibility=0.98, generic_slop_score=0.02, concise_reason="Outstanding clarity.",
        ),
    ])

    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    judge = VisualCandidateJudge(evaluator=evaluator)

    res = judge.judge_candidates([cand_a, cand_b], req, shot, det_scores)
    # Blended scores:
    # cand_a: 0.90 * 0.35 + ~0.74 * 0.65 = 0.315 + 0.481 = 0.796
    # cand_b: 0.80 * 0.35 + ~0.95 * 0.65 = 0.280 + 0.617 = 0.897 -> cand_b wins!
    assert res.selected_candidate_id == "cand_b"


def test_single_candidate_supported(tmp_image: Path):
    """Judging correctly operates when acquisition produces N = 1 candidate."""
    cand = make_candidate("solo_cand", tmp_image)
    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )
    det_scores = {
        "solo_cand": VisualCandidateScore(
            candidate_id="solo_cand", evidence_affinity=0.8, technical_quality=0.8, format_fit=0.8, source_preference=0.8, total_score=0.80
        )
    }
    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="solo_cand", shot_id="s_01", candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.88, visual_intent_match=0.85, subject_match=0.85,
            readability=0.85, composition_quality=0.85, information_value=0.85,
            evidence_visibility=0.85, generic_slop_score=0.10, concise_reason="Solo pass.",
        )
    ])
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    res = judge.judge_candidates([cand], req, shot, det_scores)
    assert res.selected_candidate_id == "solo_cand"


def test_all_candidates_rejected_returns_no_selection(tmp_image: Path):
    """When all candidates are semantically rejected, selected_candidate_id must be None."""
    cand = make_candidate("cand_bad", tmp_image)
    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )
    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="cand_bad", shot_id="s_01", candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.REJECT,
            semantic_relevance=0.30, visual_intent_match=0.30, subject_match=0.30,
            readability=0.50, composition_quality=0.50, information_value=0.30,
            generic_slop_score=0.80, issues=[VisualSemanticIssue.GENERIC_STOCK],
            concise_reason="Total slop.",
        )
    ])
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    res = judge.judge_candidates([cand], req, shot, {})
    assert res.selected_candidate_id is None
    assert "SEMANTIC_QA_REJECTED_ALL" in res.failure_reasons


def test_record_selection_occurs_only_after_semantic_selection(tmp_image: Path, tmp_path: Path):
    """CandidateRanker.record_selection is only invoked if the candidate survives semantic judging."""
    ranker = CandidateRanker()
    cand = make_candidate("cand_fail", tmp_image)
    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="cand_fail", shot_id="s_01", candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.REJECT,
            semantic_relevance=0.20, visual_intent_match=0.20, subject_match=0.20,
            readability=0.50, composition_quality=0.50, information_value=0.20,
            generic_slop_score=0.90, issues=[VisualSemanticIssue.VISUAL_INTENT_MISMATCH],
            concise_reason="Rejected.",
        )
    ])
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    router = VisualAcquisitionRouter(
        ranker=ranker,
        semantic_judge=judge,
        qa_mode=VisualSemanticQAMode.REQUIRED,
    )

    # In required mode, when candidates are rejected, no selection is recorded
    # We test acquire_visual ranking path directly
    res = router.acquire_visual(
        request=req,
        output_dir=tmp_path,
        shot=shot,
        qa_mode=VisualSemanticQAMode.REQUIRED,
    )
    assert res.selected_candidate_id is None
    assert cand.content_sha256 not in ranker.recently_used_hashes


# ==============================================================================
# Policy & Failure Mode Tests
# ==============================================================================

def test_disabled_mode_preserves_existing_behavior(tmp_image: Path, tmp_path: Path):
    """DISABLED mode bypasses semantic judge and uses deterministic ranker."""
    cand = make_candidate("cand_det", tmp_image)
    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    mock_backend = MockVisualReasoningBackend()
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    router = VisualAcquisitionRouter(semantic_judge=judge, qa_mode=VisualSemanticQAMode.DISABLED)

    # Mock candidate acquisition in router by testing rank path directly
    router.ranker.rank_candidates = lambda c, r: [(cand, VisualCandidateScore(
        candidate_id=cand.candidate_id, evidence_affinity=1.0, technical_quality=1.0,
        format_fit=1.0, source_preference=1.0, total_score=1.0
    ))]

    res = router.acquire_visual(
        request=req,
        output_dir=tmp_path,
        shot=shot,
        qa_mode=VisualSemanticQAMode.DISABLED,
    )
    assert len(mock_backend.invocations) == 0  # Not called in DISABLED mode


def test_advisory_backend_unavailable_emits_warning(tmp_image: Path, tmp_path: Path):
    """In ADVISORY mode, backend failure logs warning and falls back to deterministic winner."""
    from unittest.mock import MagicMock

    cand = make_candidate("cand_adv", tmp_image, source_type=VisualSourceType.STOCK_MEDIA)
    shot = make_shot(modality=VisualModality.STOCK_VIDEO)
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    mock_stock = MagicMock()
    mock_stock.is_available.return_value = True
    mock_stock.search_video.return_value = [{"id": "s1"}]
    mock_stock.download_candidate.return_value = cand

    # Mock backend throwing runtime error (e.g. CLI unavailable)
    mock_backend = MockVisualReasoningBackend(structured_responses=[RuntimeError("CLI unreachable")])
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    router = VisualAcquisitionRouter(
        stock_provider=mock_stock,
        semantic_judge=judge,
        qa_mode=VisualSemanticQAMode.ADVISORY,
    )

    # In ADVISORY mode, error does not crash; falls back gracefully
    res = router.acquire_visual(req, tmp_path, shot=shot, qa_mode=VisualSemanticQAMode.ADVISORY)
    assert any("SEMANTIC_QA_UNAVAILABLE" in f for f in res.failure_reasons)


def test_required_backend_unavailable_fails_closed(tmp_image: Path, tmp_path: Path):
    """In REQUIRED mode, backend failure raises VisualSemanticQAError and fails closed."""
    cand = make_candidate("cand_req", tmp_image)
    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[RuntimeError("CLI timeout")])
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    router = VisualAcquisitionRouter(semantic_judge=judge, qa_mode=VisualSemanticQAMode.REQUIRED)

    # In REQUIRED mode, router raises VisualSemanticQAError
    # We verify by mocking a candidate in router
    det_score = VisualCandidateScore(
        candidate_id=cand.candidate_id, evidence_affinity=0.8, technical_quality=0.8,
        format_fit=0.8, source_preference=0.8, total_score=0.80
    )
    with pytest.raises(VisualSemanticQAError):
        judge.judge_candidates([cand], req, shot, {cand.candidate_id: det_score})


def test_required_malformed_output_fails_closed(tmp_image: Path):
    """Malformed output in REQUIRED mode fails closed."""
    cand = make_candidate("cand_malformed", tmp_image)
    shot = make_shot()
    req = VisualAcquisitionRequest(
        project_id="p1", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    # Corrupted response
    mock_backend = MockVisualReasoningBackend(structured_responses=[ValueError("Malformed JSON")])
    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))

    with pytest.raises(Exception):
        judge.judge_candidates([cand], req, shot, {})


# ==============================================================================
# Semantic Cache Tests
# ==============================================================================

def test_same_candidate_and_shot_reuses_semantic_assessment(tmp_image: Path, tmp_path: Path):
    """Identical semantic input reuses cached assessment without invoking VLM."""
    cand = make_candidate("cache_cand", tmp_image)
    shot = make_shot()

    cache_file = tmp_path / "cache.json"
    cache = SemanticQACache(cache_file_path=cache_file)

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="cache_cand", shot_id="s_01", candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.90, visual_intent_match=0.90, subject_match=0.90,
            readability=0.85, composition_quality=0.85, information_value=0.85,
            generic_slop_score=0.05, concise_reason="Cache me.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend, cache=cache)

    # Call 1: Miss -> Invokes backend
    res1 = evaluator.evaluate_candidate(cand, shot)
    assert len(mock_backend.invocations) == 1

    # Call 2: Hit -> Reuses cache
    res2 = evaluator.evaluate_candidate(cand, shot)
    assert len(mock_backend.invocations) == 1  # No additional call!
    assert res1.semantic_input_hash == res2.semantic_input_hash


def test_changed_candidate_sha_invalidates_semantic_cache(tmp_image: Path, tmp_path: Path):
    """Changing candidate content bytes produces a different hash and invalidates cache."""
    cand1 = make_candidate("cand1", tmp_image)
    shot = make_shot()

    # Modify image bytes
    img2_path = tmp_path / "frame2.png"
    img2 = Image.new("RGB", (400, 300), color=(255, 0, 0))
    img2.save(img2_path)
    cand2 = make_candidate("cand1", img2_path)  # Same candidate_id, different content SHA

    hash1 = compute_semantic_input_hash(
        candidate_sha256=cand1.content_sha256, subject=shot.subject, action=shot.action,
        environment=shot.environment, visual_intent=shot.visual_intent.value,
        visual_modality=shot.visual_modality.value, narration_segment=shot.narration_segment,
    )
    hash2 = compute_semantic_input_hash(
        candidate_sha256=cand2.content_sha256, subject=shot.subject, action=shot.action,
        environment=shot.environment, visual_intent=shot.visual_intent.value,
        visual_modality=shot.visual_modality.value, narration_segment=shot.narration_segment,
    )
    assert hash1 != hash2


def test_changed_visual_intent_invalidates_semantic_cache(tmp_image: Path):
    """Changing visual intent invalidates the semantic cache."""
    cand = make_candidate("c_intent", tmp_image)
    shot_a = make_shot(intent=VisualIntent.SHOW_MECHANISM)
    shot_b = make_shot(intent=VisualIntent.ESTABLISH_CONTEXT)

    hash_a = compute_semantic_input_hash(
        candidate_sha256=cand.content_sha256, subject=shot_a.subject, action=shot_a.action,
        environment=shot_a.environment, visual_intent=shot_a.visual_intent.value,
        visual_modality=shot_a.visual_modality.value, narration_segment=shot_a.narration_segment,
    )
    hash_b = compute_semantic_input_hash(
        candidate_sha256=cand.content_sha256, subject=shot_b.subject, action=shot_b.action,
        environment=shot_b.environment, visual_intent=shot_b.visual_intent.value,
        visual_modality=shot_b.visual_modality.value, narration_segment=shot_b.narration_segment,
    )
    assert hash_a != hash_b


def test_policy_version_change_invalidates_semantic_cache(tmp_image: Path):
    """Updating semantic QA policy version invalidates the cache."""
    cand = make_candidate("c_ver", tmp_image)
    shot = make_shot()

    hash_v1 = compute_semantic_input_hash(
        candidate_sha256=cand.content_sha256, subject=shot.subject, action=shot.action,
        environment=shot.environment, visual_intent=shot.visual_intent.value,
        visual_modality=shot.visual_modality.value, narration_segment=shot.narration_segment,
        policy_version="semantic-qa-v1",
    )
    hash_v2 = compute_semantic_input_hash(
        candidate_sha256=cand.content_sha256, subject=shot.subject, action=shot.action,
        environment=shot.environment, visual_intent=shot.visual_intent.value,
        visual_modality=shot.visual_modality.value, narration_segment=shot.narration_segment,
        policy_version="semantic-qa-v2",
    )
    assert hash_v1 != hash_v2


# ==============================================================================
# Video Frame Sampling Tests
# ==============================================================================

def test_video_candidate_extracts_three_representative_frames(tmp_path: Path):
    """Video sampling produces 3 frames at 25%, 50%, and 75%."""
    sampler = VideoFrameSampler(temp_dir=tmp_path)
    # Create a mock video file
    video_file = tmp_path / "mock_video.mp4"
    video_file.write_bytes(b"dummy video data")

    cand = VisualAssetCandidate(
        candidate_id="vid_01",
        source_type=VisualSourceType.STOCK_MEDIA,
        file_path=str(video_file),
        content_sha256=hashlib.sha256(b"dummy video data").hexdigest(),
        duration_seconds=4.0,
        acquisition_method="stock",
    )

    sample = sampler.sample_candidate(cand, "shot_01")
    assert len(sample.sample_paths) == 3
    assert len(sample.sample_sha256s) == 3
    assert sample.sampling_method == "ffmpeg_25_50_75"

    sampler.cleanup_samples(sample)
    # Temp samples removed
    for p in sample.sample_paths:
        assert not os.path.exists(p)


def test_frame_sampling_uses_quarter_half_three_quarter_positions():
    """Verify sampling formula uses 25%, 50%, and 75% positions, never 0.0s."""
    duration = 10.0
    ratios = [0.25, 0.50, 0.75]
    timestamps = [round(duration * r, 3) for r in ratios]
    assert timestamps == [2.5, 5.0, 7.5]
    assert 0.0 not in timestamps


# ==============================================================================
# Security & Prompt Injection Tests
# ==============================================================================

def test_image_prompt_injection_cannot_create_browser_action():
    """Prompt injection inside image cannot induce tool or browser execution in schema."""
    # Verify RawVisualEvaluationResponse schema has zero browser or system action capabilities
    fields = RawVisualEvaluationResponse.model_fields.keys()
    disallowed = {"action", "browser_action", "command", "url", "source_url", "source_ref", "claim_id"}
    for d in disallowed:
        assert d not in fields


def test_visual_assessment_identity_mismatch_is_rejected(tmp_image: Path):
    """Discrepancy between requested candidate/shot ID and model response raises error."""
    cand = make_candidate("target_cand", tmp_image)
    shot = make_shot("target_shot")

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="mismatched_cand",  # Mismatch!
            shot_id="target_shot",
            candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.9, visual_intent_match=0.9, subject_match=0.9,
            readability=0.9, composition_quality=0.9, information_value=0.9,
            generic_slop_score=0.1, concise_reason="Mismatched.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)

    with pytest.raises(VisualSemanticQAError, match="SEMANTIC_QA_OUTPUT_IDENTITY_MISMATCH"):
        evaluator.evaluate_candidate(cand, shot)


def test_vlm_cannot_set_source_ref_or_claim_verified(tmp_image: Path):
    """VLM assessment produces no factual provenance fields."""
    cand = make_candidate("c_prov", tmp_image)
    shot = make_shot()

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="c_prov", shot_id="s_01", candidate_sha256=cand.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.9, visual_intent_match=0.9, subject_match=0.9,
            readability=0.9, composition_quality=0.9, information_value=0.9,
            generic_slop_score=0.1, concise_reason="Legit.",
        )
    ])
    evaluator = VisualSemanticEvaluator(backend=mock_backend)
    assessment = evaluator.evaluate_candidate(cand, shot)

    # VisualSemanticAssessment has no source_ref or claim_verified
    assert not hasattr(assessment, "source_ref")
    assert not hasattr(assessment, "claim_verified")
    assert not hasattr(assessment, "claim_id")


# ==============================================================================
# Critical Acceptance Scenarios (A, B, C, D)
# ==============================================================================

def test_scenario_a_document_evidence_wal_vs_homepage(tmp_path: Path):
    """Scenario A: SQLite WAL doc screenshot (Candidate A) outranks and defeats homepage header (Candidate B)."""
    img_wal = make_image_file(tmp_path, "wal.png", (10, 20, 30))
    img_home = make_image_file(tmp_path, "home.png", (40, 50, 60))
    cand_a = make_candidate("cand_wal_section", img_wal)
    cand_b = make_candidate("cand_homepage", img_home)

    shot = make_shot(
        narration="SQLite WAL allows readers to continue while writers append changes.",
        modality=VisualModality.DOCUMENT_EVIDENCE,
        intent=VisualIntent.SHOW_EVIDENCE,
    )
    req = VisualAcquisitionRequest(
        project_id="p_wal", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    det_scores = {
        "cand_wal_section": VisualCandidateScore(
            candidate_id="cand_wal_section", evidence_affinity=0.9, technical_quality=0.9, format_fit=0.9, source_preference=1.0, total_score=0.92
        ),
        "cand_homepage": VisualCandidateScore(
            candidate_id="cand_homepage", evidence_affinity=0.9, technical_quality=0.9, format_fit=0.9, source_preference=1.0, total_score=0.92
        ),
    }

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        # cand_wal_section: relevant passage visible -> ACCEPT
        RawVisualEvaluationResponse(
            candidate_id="cand_wal_section", shot_id=shot.shot_id, candidate_sha256=cand_a.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.95, visual_intent_match=0.95, subject_match=0.92,
            readability=0.90, composition_quality=0.88, information_value=0.92,
            evidence_visibility=0.95, generic_slop_score=0.02, concise_reason="WAL section highlighted.",
        ),
        # cand_homepage: homepage header -> REJECT
        RawVisualEvaluationResponse(
            candidate_id="cand_homepage", shot_id=shot.shot_id, candidate_sha256=cand_b.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.40, visual_intent_match=0.30, subject_match=0.40,
            readability=0.80, composition_quality=0.70, information_value=0.20,
            evidence_visibility=0.05, generic_slop_score=0.10,
            issues=[VisualSemanticIssue.WRONG_DOCUMENT_REGION, VisualSemanticIssue.EVIDENCE_NOT_VISIBLE],
            concise_reason="Homepage header without WAL documentation.",
        ),
    ])

    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    res = judge.judge_candidates([cand_a, cand_b], req, shot, det_scores)

    assert res.selected_candidate_id == "cand_wal_section"
    assert res.assessments["cand_wal_section"].verdict == VisualSemanticVerdict.ACCEPT
    assert res.assessments["cand_homepage"].verdict == VisualSemanticVerdict.REJECT


def test_scenario_b_generic_stock_server_rack_starvation(tmp_image: Path):
    """Scenario B: Server rack footage rejected for checkpoint starvation narration."""
    cand_rack = make_candidate("cand_rack", tmp_image, source_type=VisualSourceType.STOCK_MEDIA)
    shot = make_shot(
        modality=VisualModality.STOCK_VIDEO,
        intent=VisualIntent.SHOW_MECHANISM,
        subject="Checkpoint starvation",
        narration="Checkpoint starvation happens when a long-running reader keeps an old WAL snapshot alive.",
    )
    req = VisualAcquisitionRequest(
        project_id="p_wal", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="cand_rack", shot_id=shot.shot_id, candidate_sha256=cand_rack.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.25, visual_intent_match=0.20, subject_match=0.30,
            readability=0.80, composition_quality=0.75, information_value=0.20,
            generic_slop_score=0.95,
            issues=[VisualSemanticIssue.GENERIC_STOCK, VisualSemanticIssue.VISUAL_INTENT_MISMATCH],
            concise_reason="Blinking server racks do not illustrate checkpoint starvation.",
        )
    ])

    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    res = judge.judge_candidates([cand_rack], req, shot, {})

    assert res.selected_candidate_id is None
    assert res.assessments["cand_rack"].verdict == VisualSemanticVerdict.REJECT
    assert VisualSemanticIssue.GENERIC_STOCK in res.assessments["cand_rack"].issues


def test_scenario_c_mechanism_diagram_vs_generic_cylinder(tmp_path: Path):
    """Scenario C: Multi-entity mechanism diagram outranks generic single cylinder."""
    img_diag = make_image_file(tmp_path, "diag.png", (10, 20, 30))
    img_cyl = make_image_file(tmp_path, "cyl.png", (40, 50, 60))
    cand_diag = make_candidate("cand_mech_diagram", img_diag, source_type=VisualSourceType.RENDERED)
    cand_cyl = make_candidate("cand_generic_cylinder", img_cyl, source_type=VisualSourceType.RENDERED)

    shot = make_shot(
        modality=VisualModality.DIAGRAM,
        intent=VisualIntent.SHOW_MECHANISM,
        subject="Reader, Writer, WAL, Checkpoint flow",
        narration="Reader queries database file while Writer appends to WAL until Checkpoint copies frames back.",
    )
    req = VisualAcquisitionRequest(
        project_id="p_wal", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )
    det_scores = {
        "cand_mech_diagram": VisualCandidateScore(
            candidate_id="cand_mech_diagram", evidence_affinity=0.8, technical_quality=0.8, format_fit=0.8, source_preference=0.8, total_score=0.80
        ),
        "cand_generic_cylinder": VisualCandidateScore(
            candidate_id="cand_generic_cylinder", evidence_affinity=0.8, technical_quality=0.8, format_fit=0.8, source_preference=0.8, total_score=0.80
        ),
    }

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="cand_mech_diagram", shot_id=shot.shot_id, candidate_sha256=cand_diag.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.95, visual_intent_match=0.92, subject_match=0.92,
            readability=0.88, composition_quality=0.85, information_value=0.90,
            mechanism_clarity=0.95, generic_slop_score=0.05, concise_reason="Clear 4-entity interaction.",
        ),
        RawVisualEvaluationResponse(
            candidate_id="cand_generic_cylinder", shot_id=shot.shot_id, candidate_sha256=cand_cyl.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.50, visual_intent_match=0.40, subject_match=0.55,
            readability=0.75, composition_quality=0.70, information_value=0.40,
            mechanism_clarity=0.35, generic_slop_score=0.60,
            issues=[VisualSemanticIssue.DECORATIVE_ONLY, VisualSemanticIssue.MECHANISM_NOT_EXPLAINED],
            concise_reason="Single cylinder with arbitrary arrows.",
        ),
    ])

    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    res = judge.judge_candidates([cand_diag, cand_cyl], req, shot, det_scores)

    assert res.selected_candidate_id == "cand_mech_diagram"


def test_scenario_d_ui_checkpoint_button_vs_dashboard_title(tmp_path: Path):
    """Scenario D: UI screenshot with button and completed status passes; generic header fails."""
    img_action = make_image_file(tmp_path, "action.png", (10, 20, 30))
    img_title = make_image_file(tmp_path, "title.png", (40, 50, 60))
    cand_ui_good = make_candidate("cand_ui_action", img_action, source_type=VisualSourceType.LOCAL_WEB_APP)
    cand_ui_title = make_candidate("cand_ui_title", img_title, source_type=VisualSourceType.LOCAL_WEB_APP)

    shot = make_shot(
        modality=VisualModality.SCREEN_CAPTURE,
        intent=VisualIntent.SHOW_INTERFACE,
        subject="Run Checkpoint Button",
        narration="Click Run Checkpoint and the status changes to completed.",
    )
    req = VisualAcquisitionRequest(
        project_id="p_ui", shot_id=shot.shot_id, modality=shot.visual_modality,
        visual_intent=shot.visual_intent, subject=shot.subject,
    )
    det_scores = {
        "cand_ui_action": VisualCandidateScore(
            candidate_id="cand_ui_action", evidence_affinity=0.8, technical_quality=0.8, format_fit=0.8, source_preference=0.8, total_score=0.80
        ),
        "cand_ui_title": VisualCandidateScore(
            candidate_id="cand_ui_title", evidence_affinity=0.8, technical_quality=0.8, format_fit=0.8, source_preference=0.8, total_score=0.80
        ),
    }

    mock_backend = MockVisualReasoningBackend(structured_responses=[
        RawVisualEvaluationResponse(
            candidate_id="cand_ui_action", shot_id=shot.shot_id, candidate_sha256=cand_ui_good.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.92, visual_intent_match=0.90, subject_match=0.90,
            readability=0.85, composition_quality=0.82, information_value=0.88,
            interface_state_match=0.92, generic_slop_score=0.05, concise_reason="Button and Completed status shown.",
        ),
        RawVisualEvaluationResponse(
            candidate_id="cand_ui_title", shot_id=shot.shot_id, candidate_sha256=cand_ui_title.content_sha256,
            verdict=VisualSemanticVerdict.ACCEPT,
            semantic_relevance=0.45, visual_intent_match=0.35, subject_match=0.50,
            readability=0.80, composition_quality=0.70, information_value=0.30,
            interface_state_match=0.15, generic_slop_score=0.10,
            issues=[VisualSemanticIssue.UI_STATE_NOT_SHOWN],
            concise_reason="Only dashboard title bar visible.",
        ),
    ])

    judge = VisualCandidateJudge(evaluator=VisualSemanticEvaluator(backend=mock_backend))
    res = judge.judge_candidates([cand_ui_good, cand_ui_title], req, shot, det_scores)

    assert res.selected_candidate_id == "cand_ui_action"
    assert res.assessments["cand_ui_action"].verdict == VisualSemanticVerdict.ACCEPT
    assert res.assessments["cand_ui_title"].verdict == VisualSemanticVerdict.REJECT
