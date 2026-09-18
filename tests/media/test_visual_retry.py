"""Comprehensive test suite for Selective Visual Retry and Reacquisition.

Validates:
1. Bounded Generated Retry: Attempt 0 rejected, Attempt 1 accepted, total attempts = 2, retry_count = 1.
2. Second Rejection Fails Closed: Attempt 0 rejected, Attempt 1 rejected, raises VisualSemanticQAError, no Attempt 3.
3. Duplicate Retry Output Detection: Attempt 1 returns identical SHA-256 to Attempt 0, detected and fails closed.
4. Stock to Diagram Fallback: SWITCH_TO_DIAGRAM routes stock rejection to technical diagram rendering.
5. Actual Modality Drives Policy: Candidate's actual modality governs retry action regardless of requested modality.
6. Trust / Integrity Failures Never Retried: Security, trust, and integrity errors fail closed immediately with retry_count = 0.
7. Document Evidence Authority Preserved: Canonical claim IDs, source URLs, and refs are immutable across reacquisition.
8. Only Failed Shot is Regenerated: Successful shots are untouched (1 call), failed shots retry (2 calls).
9. No-Op Retries Are Rejected: Modalities lacking alternative targeting (fixed chart, identical document anchor) return NO_RETRY.
10. Single Shot Regeneration: regenerate_single_shot propagates retry metadata to TimelineShot.
"""

import hashlib
from pathlib import Path
import re
from unittest.mock import MagicMock
from PIL import Image
import pytest

from app.domain.models import Script
from app.media.director.director_service import AutoDirectorService
from app.media.director.models import (
    ChannelCreativeProfile,
    CreativeFallbackPolicy,
    EvidenceBinding,
    ShotAssetResult,
    ShotSpec,
    Storyboard,
    TimelineShot,
    VisualIntent,
    VisualModality,
)
from app.media.director.visual_retry import (
    MAX_SEMANTIC_CORRECTIVE_RETRIES_PER_SHOT,
    VisualRetryAction,
    VisualRetryDecision,
    VisualRetryPolicy,
)
from app.media.semantic_qa.evaluator import VisualSemanticEvaluator
from app.media.semantic_qa.judge import VisualCandidateJudge
from app.media.semantic_qa.models import (
    VisualSemanticIssue,
    VisualSemanticQAError,
    VisualSemanticVerdict,
)


class SequentialMockBackend:
    """Mock reasoning backend returning a predefined sequence of verdicts/issues per invocation."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.invocation_count = 0
        self.recorded_prompts = []
        self.recorded_image_paths = []

    def evaluate_visual(self, prompt, image_paths, schema_cls):
        self.recorded_prompts.append(prompt)
        self.recorded_image_paths.append(list(image_paths))
        idx = min(self.invocation_count, len(self.responses) - 1)
        resp = self.responses[idx]
        self.invocation_count += 1

        verdict = resp.get("verdict", VisualSemanticVerdict.ACCEPT)
        issues = resp.get("issues", [])
        reason = resp.get("reason", "Evaluation complete")

        cand_m = re.search(r"-\s*Candidate ID:\s*([^\s\r\n]+)", prompt)
        shot_m = re.search(r"-\s*Shot ID:\s*([^\s\r\n]+)", prompt)
        sha_m = re.search(r"-\s*Content SHA-256:\s*([^\s\r\n]+)", prompt)
        cand_id = cand_m.group(1) if cand_m else "placeholder_cand"
        shot_id = shot_m.group(1) if shot_m else "placeholder_shot"
        sha = sha_m.group(1) if sha_m else "0" * 64

        return schema_cls(
            candidate_id=cand_id,
            shot_id=shot_id,
            candidate_sha256=sha,
            verdict=verdict,
            semantic_relevance=0.95 if verdict == VisualSemanticVerdict.ACCEPT else 0.40,
            visual_intent_match=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.40,
            subject_match=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.35,
            action_match=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.35,
            readability=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.40,
            composition_quality=0.85 if verdict == VisualSemanticVerdict.ACCEPT else 0.40,
            information_value=0.85 if verdict == VisualSemanticVerdict.ACCEPT else 0.40,
            generic_slop_score=0.05 if verdict == VisualSemanticVerdict.ACCEPT else 0.80,
            mechanism_clarity=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.30,
            interface_state_match=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.30,
            evidence_visibility=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.30,
            data_readability=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.30,
            comparison_clarity=0.90 if verdict == VisualSemanticVerdict.ACCEPT else 0.30,
            issues=issues,
            concise_reason=reason,
        )


# ==============================================================================
# Test 1: Bounded Generated Retry (Attempt 0 Reject -> Attempt 1 Accept)
# ==============================================================================

def test_bounded_generated_retry_succeeds_on_second_attempt(tmp_path: Path):
    """Generated media rejected on Attempt 0 with SUBJECT_MISMATCH is retried with
    a corrected prompt; Attempt 1 produces different content and is ACCEPTED.
    Total provider calls = 2, total QA evaluations = 2, retry_count = 1."""
    img_a = tmp_path / "img_a.png"
    img_b = tmp_path / "img_b.png"
    Image.new("RGB", (320, 240), color=(255, 0, 0)).save(img_a)
    Image.new("RGB", (320, 240), color=(0, 255, 0)).save(img_b)
    sha_a = hashlib.sha256(img_a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(img_b.read_bytes()).hexdigest()
    assert sha_a != sha_b

    mock_gflow = MagicMock()
    # Return img_a on call 1, img_b on call 2
    mock_gflow.generate_image.side_effect = [
        (str(img_a), sha_a, {}),
        (str(img_b), sha_b, {}),
    ]

    backend = SequentialMockBackend([
        {"verdict": VisualSemanticVerdict.REJECT, "issues": [VisualSemanticIssue.SUBJECT_MISMATCH], "reason": "Wrong subject in image"},
        {"verdict": VisualSemanticVerdict.ACCEPT, "issues": [], "reason": "Corrected subject matches perfectly"},
    ])

    profile = ChannelCreativeProfile.production_profile(name="retry_prod_profile")
    director = AutoDirectorService(profile=profile, gflow_provider=mock_gflow, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id="shot_gen_retry",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.GENERATED_IMAGE,
        requested_modality=VisualModality.GENERATED_IMAGE,
        visual_intent=VisualIntent.ESTABLISH_CONTEXT,
        subject="Neural Network Architecture",
        narration_segment="Deep neural network with self-attention mechanism.",
        duration_seconds=3.0,
        generation_prompt="Initial prompt of a computer network",
    )

    asset_res = director._generate_shot_asset(
        shot=shot,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Deep Learning",
        channel_name="AI Channel",
    )

    # Verifications
    assert mock_gflow.generate_image.call_count == 2
    assert backend.invocation_count == 2
    assert asset_res.retry_attempted is True
    assert asset_res.retry_action == VisualRetryAction.REGENERATE.value
    assert asset_res.retry_count == 1
    assert asset_res.sha256 == sha_b
    assert asset_res.semantic_qa_performed is True
    assert asset_res.semantic_audit["verdict"] == "ACCEPT"

    # Verify corrective prompt was passed on retry
    call_args_1 = mock_gflow.generate_image.call_args_list[0]
    call_args_2 = mock_gflow.generate_image.call_args_list[1]
    assert "Initial prompt" in call_args_1.kwargs["prompt"]
    assert "Neural Network Architecture" in call_args_2.kwargs["prompt"]
    assert "CORRECTIVE RETRY" in call_args_2.kwargs["prompt"]


# ==============================================================================
# Test 2: Second Rejection Fails Closed (Attempts Budget = 2 Exhausted)
# ==============================================================================

def test_second_rejection_fails_closed_without_unbounded_loops(tmp_path: Path):
    """When Attempt 0 is rejected and Attempt 1 is also rejected, the pipeline
    fails closed immediately. No third attempt is ever executed."""
    img_a = tmp_path / "img_a.png"
    img_b = tmp_path / "img_b.png"
    Image.new("RGB", (320, 240), color=(10, 20, 30)).save(img_a)
    Image.new("RGB", (320, 240), color=(40, 50, 60)).save(img_b)
    sha_a = hashlib.sha256(img_a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(img_b.read_bytes()).hexdigest()

    mock_gflow = MagicMock()
    mock_gflow.generate_image.side_effect = [
        (str(img_a), sha_a, {}),
        (str(img_b), sha_b, {}),
    ]

    backend = SequentialMockBackend([
        {"verdict": VisualSemanticVerdict.REJECT, "issues": [VisualSemanticIssue.SUBJECT_MISMATCH], "reason": "Wrong subject attempt 0"},
        {"verdict": VisualSemanticVerdict.REJECT, "issues": [VisualSemanticIssue.ACTION_MISMATCH], "reason": "Action mismatch attempt 1"},
    ])

    profile = ChannelCreativeProfile.production_profile(name="fail_closed_profile")
    director = AutoDirectorService(profile=profile, gflow_provider=mock_gflow, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id="shot_two_rejects",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.GENERATED_IMAGE,
        requested_modality=VisualModality.GENERATED_IMAGE,
        visual_intent=VisualIntent.ESTABLISH_CONTEXT,
        subject="Quantum Processor",
        narration_segment="Superconducting qubits suspended in a dilution refrigerator.",
        duration_seconds=3.0,
        generation_prompt="Quantum processor dilution refrigerator",
    )

    with pytest.raises(VisualSemanticQAError, match="attempts budget exhausted"):
        director._generate_shot_asset(
            shot=shot,
            shot_index=0,
            output_dir=tmp_path,
            script_title="Quantum Computing",
            channel_name="Tech Channel",
        )

    # Strictly bounded: exactly 2 generation calls, 2 QA calls
    assert mock_gflow.generate_image.call_count == 2
    assert backend.invocation_count == 2
    assert MAX_SEMANTIC_CORRECTIVE_RETRIES_PER_SHOT == 1


# ==============================================================================
# Test 3: Duplicate Retry Output Detection (Identical SHA Fails Closed)
# ==============================================================================

def test_duplicate_retry_output_detected_and_blocked(tmp_path: Path):
    """When a corrective retry produces the identical content SHA-256 as the rejected
    initial attempt, DUPLICATE_RETRY_OUTPUT is logged and fails closed before evaluation."""
    img = tmp_path / "identical.png"
    Image.new("RGB", (320, 240), color=(128, 128, 128)).save(img)
    sha = hashlib.sha256(img.read_bytes()).hexdigest()

    mock_gflow = MagicMock()
    # Provider deterministically returns identical content hash
    mock_gflow.generate_image.return_value = (str(img), sha, {})

    backend = SequentialMockBackend([
        {"verdict": VisualSemanticVerdict.REJECT, "issues": [VisualSemanticIssue.SUBJECT_MISMATCH], "reason": "Subject mismatch"},
        # Second response would not be called because duplicate detection triggers before QA evaluation
        {"verdict": VisualSemanticVerdict.ACCEPT, "issues": [], "reason": "Should not reach here"},
    ])

    profile = ChannelCreativeProfile.production_profile(name="dup_profile")
    director = AutoDirectorService(profile=profile, gflow_provider=mock_gflow, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id="shot_dup_output",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.GENERATED_IMAGE,
        requested_modality=VisualModality.GENERATED_IMAGE,
        visual_intent=VisualIntent.ESTABLISH_CONTEXT,
        subject="Data Center",
        narration_segment="Rows of server racks blinking in cool air.",
        duration_seconds=3.0,
        generation_prompt="Server racks data center",
    )

    with pytest.raises(VisualSemanticQAError, match="DUPLICATE_RETRY_OUTPUT"):
        director._generate_shot_asset(
            shot=shot,
            shot_index=0,
            output_dir=tmp_path,
            script_title="Cloud Infrastructure",
            channel_name="Tech Channel",
        )

    # Provider called twice (Attempt 0 + Attempt 1), but QA only evaluated Attempt 0
    assert mock_gflow.generate_image.call_count == 2
    assert backend.invocation_count == 1


# ==============================================================================
# Test 4: Stock Video to Technical Diagram Fallback (SWITCH_TO_DIAGRAM)
# ==============================================================================

def test_stock_video_generic_rejection_switches_to_technical_diagram(tmp_path: Path):
    """When stock video is rejected due to GENERIC_STOCK or VISUAL_INTENT_MISMATCH,
    the retry policy switches the shot to VisualModality.DIAGRAM and rerenders it."""
    # Create dummy stock video
    stock_vid = tmp_path / "generic_stock.mp4"
    stock_vid.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"\x00" * 200)
    stock_sha = hashlib.sha256(stock_vid.read_bytes()).hexdigest()

    mock_stock_cand = MagicMock()
    mock_stock_cand.file_path = str(stock_vid)
    mock_stock_cand.content_sha256 = stock_sha
    mock_stock_cand.actual_modality = VisualModality.STOCK_VIDEO
    mock_stock_cand.acquisition_method = "pexels_stock_api"
    mock_stock_cand.source_type = MagicMock(value="STOCK_MEDIA")
    mock_stock_cand.source_url = "https://pexels.com/video/123"
    mock_stock_cand.source_ref = "stock_123"
    mock_stock_cand.license_type = "Pexels License"
    mock_stock_cand.attribution = "Pexels Creator"
    mock_stock_cand.is_synthetic = False
    mock_stock_cand.evidence_claim_ids = []

    mock_acq_res = MagicMock()
    mock_acq_res.selected_candidate = None
    mock_acq_res.candidates = [mock_stock_cand]
    mock_acq_res.failure_reasons = ["SEMANTIC_QA_REJECTED_ALL"]
    mock_acq_res.semantic_audit = {
        "performed": True,
        "verdict": "REJECT",
        "issues": ["GENERIC_STOCK", "VISUAL_INTENT_MISMATCH"],
        "reason": "Stock footage shows generic people smiling, not database engine architecture",
        "candidate_id": "cand_stock_01",
    }

    mock_router = MagicMock()
    mock_router.stock_provider = MagicMock(is_available=lambda: True)
    mock_router.build_acquisition_request.return_value = MagicMock()
    mock_router.acquire_visual.return_value = mock_acq_res

    backend = SequentialMockBackend([
        # Attempt 1 (diagram render) evaluation
        {"verdict": VisualSemanticVerdict.ACCEPT, "issues": [], "reason": "Technical diagram clearly shows database engine mechanism"},
    ])

    profile = ChannelCreativeProfile.production_profile(name="stock_fallback_prof")
    director = AutoDirectorService(profile=profile, reasoning_backend=backend)
    director.acquisition_router = mock_router
    evaluator = VisualSemanticEvaluator(backend=backend)
    mock_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    shot = ShotSpec(
        shot_id="shot_stock_switch",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.STOCK_VIDEO,
        requested_modality=VisualModality.STOCK_VIDEO,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject="Database Storage Engine",
        narration_segment="The storage engine writes dirty pages to the write-ahead log.",
        duration_seconds=3.0,
    )

    asset_res = director._generate_shot_asset(
        shot=shot,
        shot_index=0,
        output_dir=tmp_path,
        script_title="Database Internals",
        channel_name="Tech Channel",
    )

    # Verifications
    assert asset_res.requested_modality == VisualModality.STOCK_VIDEO
    assert asset_res.actual_modality == VisualModality.DIAGRAM
    assert asset_res.provider == "diagram_renderer"
    assert asset_res.retry_attempted is True
    assert asset_res.retry_action == VisualRetryAction.SWITCH_TO_DIAGRAM.value
    assert asset_res.retry_count == 1
    assert asset_res.semantic_audit["verdict"] == "ACCEPT"


# ==============================================================================
# Test 5: Candidate Actual Modality Drives Policy (Not Requested Modality)
# ==============================================================================

def test_actual_modality_drives_retry_policy_decision():
    """If requested modality was STOCK_VIDEO, but candidate resolved to DIAGRAM,
    a rejection with MECHANISM_NOT_EXPLAINED must trigger RERENDER (diagram action),
    NOT SWITCH_TO_DIAGRAM."""
    shot = ShotSpec(
        shot_id="shot_modality_drive",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.STOCK_VIDEO,
        requested_modality=VisualModality.STOCK_VIDEO,
        visual_intent=VisualIntent.SHOW_MECHANISM,
        subject="LSM Tree Compaction",
        narration_segment="Memtables flush to SSTables and trigger compaction.",
        duration_seconds=3.0,
        diagram_instruction="MemTable -> SSTable Level 0 -> SSTable Level 1",
    )

    decision = VisualRetryPolicy.evaluate_decision(
        shot=shot,
        actual_modality=VisualModality.DIAGRAM,  # Candidate is actually a diagram!
        issues=[VisualSemanticIssue.MECHANISM_NOT_EXPLAINED],
        concise_reason="Diagram does not show compaction step clearly",
        attempt_index=0,
    )

    assert decision.action == VisualRetryAction.RERENDER
    assert decision.target_modality == VisualModality.DIAGRAM
    assert "MemTable" in decision.corrected_instruction
    assert "SSTable" in decision.corrected_instruction


# ==============================================================================
# Test 6: Trust and Integrity Failures Never Retried
# ==============================================================================

@pytest.mark.parametrize("failure_keyword", [
    "UNTRUSTED_VISUAL_SOURCE",
    "UNTRUSTED_SOURCE_URL",
    "PRIVATE_IP_BLOCKED",
    "VISUAL_ASSET_SHA_MISMATCH",
    "MISSING_FILE",
    "EMPTY_FILE",
])
def test_trust_and_integrity_failures_never_retried(failure_keyword: str):
    """Security, trust, and asset integrity failures are non-retryable and must fail closed immediately."""
    shot = ShotSpec(
        shot_id="shot_security_fail",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        requested_modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="SEC 10-K Report",
        narration_segment="Revenue grew by 24 percent year over year.",
        duration_seconds=3.0,
    )

    decision = VisualRetryPolicy.evaluate_decision(
        shot=shot,
        actual_modality=VisualModality.DOCUMENT_EVIDENCE,
        issues=[VisualSemanticIssue.VISUAL_CONTRADICTION],
        concise_reason=f"Security failure detected: {failure_keyword}",
        attempt_index=0,
        failure_reasons=[f"Acquisition error: {failure_keyword}"],
    )

    assert decision.action == VisualRetryAction.NO_RETRY
    assert "Trust, security, or integrity failure" in decision.reason


# ==============================================================================
# Test 7: Document Evidence Canonical Authority Preserved
# ==============================================================================

def test_document_evidence_canonical_authority_preserved_across_reacquisition():
    """Document evidence reacquisition uses alternative claim text targeting without
    mutating canonical source_ref, source_url, or claim_id."""
    binding = EvidenceBinding(
        claim_id="claim_sec_456",
        source_ref="sec_filing_q3",
        source_title="Alphabet Q3 2024 Form 10-Q",
        source_url="https://sec.gov/edgar/data/123/q3_2024.htm",
        claim_text="Google Cloud revenue grew 35% to $11.35 billion.",
        source_excerpt="Initial unhighlighted excerpt on page",
        claim_verified=True,
    )

    shot = ShotSpec(
        shot_id="shot_doc_authority",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        requested_modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Cloud Revenue Growth",
        narration_segment="Google Cloud reached 11.35 billion in quarterly revenue.",
        duration_seconds=3.0,
        evidence_binding=binding,
    )

    decision = VisualRetryPolicy.evaluate_decision(
        shot=shot,
        actual_modality=VisualModality.DOCUMENT_EVIDENCE,
        issues=[VisualSemanticIssue.EVIDENCE_NOT_VISIBLE],
        concise_reason="Highlight was scrolled out of viewport",
        attempt_index=0,
    )

    assert decision.action == VisualRetryAction.REACQUIRE
    assert decision.corrected_instruction == "Google Cloud revenue grew 35% to $11.35 billion."

    # Verify canonical authority remains immutable
    assert shot.evidence_binding.claim_id == "claim_sec_456"
    assert shot.evidence_binding.source_ref == "sec_filing_q3"
    assert shot.evidence_binding.source_url == "https://sec.gov/edgar/data/123/q3_2024.htm"


# ==============================================================================
# Test 8: Only Failed Shot is Regenerated (Unit of Correction)
# ==============================================================================

def test_only_failed_shot_is_retried_on_timeline(tmp_path: Path):
    """In a multi-shot timeline, Shot 1 passes on Attempt 0. Shot 2 fails on Attempt 0 and
    succeeds on Attempt 1. Shot 1 has retry_count = 0 (1 call), Shot 2 has retry_count = 1 (2 calls)."""
    img_1 = tmp_path / "shot_1.png"
    img_2a = tmp_path / "shot_2a.png"
    img_2b = tmp_path / "shot_2b.png"
    Image.new("RGB", (320, 240), color=(100, 100, 100)).save(img_1)
    Image.new("RGB", (320, 240), color=(150, 150, 150)).save(img_2a)
    Image.new("RGB", (320, 240), color=(200, 200, 200)).save(img_2b)

    sha_1 = hashlib.sha256(img_1.read_bytes()).hexdigest()
    sha_2a = hashlib.sha256(img_2a.read_bytes()).hexdigest()
    sha_2b = hashlib.sha256(img_2b.read_bytes()).hexdigest()

    mock_gflow = MagicMock()
    mock_gflow.generate_image.side_effect = [
        (str(img_1), sha_1, {}),     # Shot 1 attempt 0
        (str(img_2a), sha_2a, {}),   # Shot 2 attempt 0
        (str(img_2b), sha_2b, {}),   # Shot 2 attempt 1 (retry)
    ]

    backend = SequentialMockBackend([
        {"verdict": VisualSemanticVerdict.ACCEPT, "issues": [], "reason": "Shot 1 accepted"},
        {"verdict": VisualSemanticVerdict.REJECT, "issues": [VisualSemanticIssue.SUBJECT_MISMATCH], "reason": "Shot 2 rejected on attempt 0"},
        {"verdict": VisualSemanticVerdict.ACCEPT, "issues": [], "reason": "Shot 2 accepted on attempt 1"},
    ])

    profile = ChannelCreativeProfile.production_profile(name="unit_of_correction_prof")
    director = AutoDirectorService(profile=profile, gflow_provider=mock_gflow, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    script = Script(
        id="script_test_01",
        title="Multi-Shot Pipeline",
        hook="Shot one explains concept.",
        total_word_count=50,
        estimated_duration_seconds=6.0,
    )

    storyboard = Storyboard(
        project_id="proj_multi_shot",
        total_duration=6.0,
        shots=[
            ShotSpec(
                shot_id="shot_01",
                scene_index=0,
                beat_id="beat_01",
                visual_modality=VisualModality.GENERATED_IMAGE,
                requested_modality=VisualModality.GENERATED_IMAGE,
                visual_intent=VisualIntent.ESTABLISH_CONTEXT,
                subject="Concept Art",
                narration_segment="Shot one explains concept.",
                duration_seconds=3.0,
                generation_prompt="Concept art illustration",
            ),
            ShotSpec(
                shot_id="shot_02",
                scene_index=1,
                beat_id="beat_02",
                visual_modality=VisualModality.GENERATED_IMAGE,
                requested_modality=VisualModality.GENERATED_IMAGE,
                visual_intent=VisualIntent.SHOW_MECHANISM,
                subject="System Architecture",
                narration_segment="Shot two shows implementation.",
                duration_seconds=3.0,
                generation_prompt="Architecture diagram artwork",
            ),
        ],
    )

    director.planner.plan_storyboard = MagicMock(return_value=storyboard)
    timeline, _ = director.plan_and_render_timeline(
        project_id="proj_multi_shot",
        script=script,
        channel_name="Tech Channel",
        total_audio_duration=6.0,
        output_dir=tmp_path,
    )

    assert len(timeline.shots) == 2

    t_shot_1 = timeline.shots[0]
    t_shot_2 = timeline.shots[1]

    # Shot 1: passed on initial attempt
    assert t_shot_1.shot_id == "shot_01"
    assert t_shot_1.asset_retry_attempted is False
    assert t_shot_1.asset_retry_count == 0
    assert t_shot_1.asset_retry_action is None

    # Shot 2: retried once and passed
    assert t_shot_2.shot_id == "shot_02"
    assert t_shot_2.asset_retry_attempted is True
    assert t_shot_2.asset_retry_count == 1
    assert t_shot_2.asset_retry_action == VisualRetryAction.REGENERATE.value

    # Total provider calls = 3 (1 for Shot 1, 2 for Shot 2)
    assert mock_gflow.generate_image.call_count == 3
    assert backend.invocation_count == 3


# ==============================================================================
# Test 9: No-Op Retries Are Rejected
# ==============================================================================

def test_noop_retries_rejected_for_uncontrollable_modalities():
    """Modalities without actionable corrective controls return NO_RETRY to prevent fake recovery."""
    # 9a. Document evidence with identical anchor
    doc_binding = EvidenceBinding(
        claim_id="claim_identical",
        source_ref="ref_1",
        source_title="Document Title",
        source_url="https://example.org/doc",
        claim_text="Identical text",
        source_excerpt="Identical text",  # Exactly matches claim_text: no alternative anchor
        claim_verified=True,
    )
    doc_shot = ShotSpec(
        shot_id="shot_noop_doc",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.DOCUMENT_EVIDENCE,
        requested_modality=VisualModality.DOCUMENT_EVIDENCE,
        visual_intent=VisualIntent.SHOW_EVIDENCE,
        subject="Identical Document",
        narration_segment="Identical text segment",
        duration_seconds=3.0,
        evidence_binding=doc_binding,
    )
    doc_decision = VisualRetryPolicy.evaluate_decision(
        shot=doc_shot,
        actual_modality=VisualModality.DOCUMENT_EVIDENCE,
        issues=[VisualSemanticIssue.EVIDENCE_NOT_VISIBLE],
        attempt_index=0,
    )
    assert doc_decision.action == VisualRetryAction.NO_RETRY
    assert "no-op retry rejected" in doc_decision.reason

    # 9b. Data visualization / Chart renderer (fixed layout and fonts)
    chart_shot = ShotSpec(
        shot_id="shot_noop_chart",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.DATA_VISUALIZATION,
        requested_modality=VisualModality.DATA_VISUALIZATION,
        visual_intent=VisualIntent.SHOW_DATA,
        subject="Chart Data",
        narration_segment="Chart shows values",
        duration_seconds=3.0,
    )
    chart_decision = VisualRetryPolicy.evaluate_decision(
        shot=chart_shot,
        actual_modality=VisualModality.DATA_VISUALIZATION,
        issues=[VisualSemanticIssue.DATA_UNREADABLE],
        attempt_index=0,
    )
    assert chart_decision.action == VisualRetryAction.NO_RETRY
    assert "Chart renderer does not support dynamic label resizing" in chart_decision.reason

    # 9c. Screen capture without alternate interaction plan
    screen_shot = ShotSpec(
        shot_id="shot_noop_screen",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.SCREEN_CAPTURE,
        requested_modality=VisualModality.SCREEN_CAPTURE,
        visual_intent=VisualIntent.SHOW_INTERFACE,
        subject="Terminal Screen",
        narration_segment="Terminal output",
        duration_seconds=3.0,
    )
    screen_decision = VisualRetryPolicy.evaluate_decision(
        shot=screen_shot,
        actual_modality=VisualModality.SCREEN_CAPTURE,
        issues=[VisualSemanticIssue.UI_STATE_NOT_SHOWN],
        attempt_index=0,
        provider_capabilities={"has_alternate_interaction": False},
    )
    assert screen_decision.action == VisualRetryAction.NO_RETRY
    assert "Screen capture lacks an alternate interaction plan" in screen_decision.reason


# ==============================================================================
# Test 10: Single Shot Regeneration Propagates Retry Provenance
# ==============================================================================

def test_regenerate_single_shot_propagates_retry_metadata(tmp_path: Path):
    """regenerate_single_shot propagates asset_retry_attempted, asset_retry_action,
    and asset_retry_count to the targeted TimelineShot."""
    img_a = tmp_path / "regen_a.png"
    img_b = tmp_path / "regen_b.png"
    Image.new("RGB", (320, 240), color=(1, 2, 3)).save(img_a)
    Image.new("RGB", (320, 240), color=(4, 5, 6)).save(img_b)
    sha_a = hashlib.sha256(img_a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(img_b.read_bytes()).hexdigest()

    mock_gflow = MagicMock()
    mock_gflow.generate_image.side_effect = [
        (str(img_a), sha_a, {}),
        (str(img_b), sha_b, {}),
    ]

    backend = SequentialMockBackend([
        {"verdict": VisualSemanticVerdict.REJECT, "issues": [VisualSemanticIssue.SUBJECT_MISMATCH], "reason": "Wrong subject on single shot regen"},
        {"verdict": VisualSemanticVerdict.ACCEPT, "issues": [], "reason": "Accepted on retry single shot regen"},
    ])

    profile = ChannelCreativeProfile.production_profile(name="single_shot_regen_prof")
    director = AutoDirectorService(profile=profile, gflow_provider=mock_gflow, reasoning_backend=backend)
    evaluator = VisualSemanticEvaluator(backend=backend)
    director.acquisition_router.semantic_judge = VisualCandidateJudge(evaluator=evaluator)

    target_shot = ShotSpec(
        shot_id="shot_single_target",
        scene_index=0,
        beat_id="beat_01",
        visual_modality=VisualModality.GENERATED_IMAGE,
        requested_modality=VisualModality.GENERATED_IMAGE,
        visual_intent=VisualIntent.ESTABLISH_CONTEXT,
        subject="Neural Net",
        narration_segment="Neural network weights.",
        duration_seconds=3.0,
        generation_prompt="Neural network weights",
    )

    storyboard = Storyboard(
        project_id="proj_single_regen",
        total_duration=3.0,
        shots=[target_shot],
    )

    initial_t_shot = TimelineShot(
        shot_id="shot_single_target",
        scene_index=0,
        beat_id="beat_01",
        start=0.0,
        end=3.0,
        duration=3.0,
        asset_path=str(img_a),
        asset_sha256=sha_a,
        modality=VisualModality.GENERATED_IMAGE,
    )
    from app.media.director.models import ShotTimeline
    timeline = ShotTimeline(shots=[initial_t_shot], total_duration=3.0)

    updated_timeline, _ = director.regenerate_single_shot(
        project_id="proj_single_regen",
        shot_id="shot_single_target",
        timeline=timeline,
        storyboard=storyboard,
        output_dir=tmp_path,
        new_instruction="High resolution neural network diagram",
    )

    t_shot = updated_timeline.shots[0]
    assert t_shot.asset_retry_attempted is True
    assert t_shot.asset_retry_action == VisualRetryAction.REGENERATE.value
    assert t_shot.asset_retry_count == 1
    assert t_shot.asset_sha256 == sha_b
