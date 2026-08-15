"""Unit tests for the 16-stage video lifecycle state machine."""

import pytest
from app.domain.enums import VideoLifecycleState
from app.domain.state_machine import LifecycleStateMachine, InvalidStateTransitionError


def test_all_16_states_defined() -> None:
    """Verify all 16 specified lifecycle states are present."""
    expected_states = {
        "CREATED",
        "RESEARCHING",
        "PLANNED",
        "SCRIPTED",
        "VERIFIED",
        "PRODUCING",
        "RENDERED",
        "QA_FAILED",
        "READY_FOR_REVIEW",
        "APPROVED",
        "REJECTED",
        "UPLOADING",
        "SCHEDULED",
        "PUBLISHED",
        "FAILED",
        "BLOCKED",
    }
    actual_states = {state.value for state in VideoLifecycleState}
    assert actual_states == expected_states


def test_valid_forward_transitions() -> None:
    """Verify standard happy-path progression through the pipeline."""
    sm = LifecycleStateMachine(current_state=VideoLifecycleState.CREATED)

    sm.transition_to(VideoLifecycleState.RESEARCHING, reason="Starting niche research")
    assert sm.current_state == VideoLifecycleState.RESEARCHING

    sm.transition_to(VideoLifecycleState.PLANNED, reason="Topic selected & planned")
    assert sm.current_state == VideoLifecycleState.PLANNED

    sm.transition_to(VideoLifecycleState.SCRIPTED, reason="Script generation completed")
    assert sm.current_state == VideoLifecycleState.SCRIPTED

    sm.transition_to(VideoLifecycleState.VERIFIED, reason="Fact checking passed")
    assert sm.current_state == VideoLifecycleState.VERIFIED

    sm.transition_to(VideoLifecycleState.PRODUCING, reason="Media & TTS synthesis started")
    assert sm.current_state == VideoLifecycleState.PRODUCING

    sm.transition_to(VideoLifecycleState.RENDERED, reason="FFmpeg composition completed")
    assert sm.current_state == VideoLifecycleState.RENDERED

    sm.transition_to(VideoLifecycleState.READY_FOR_REVIEW, reason="Automated QA passed")
    assert sm.current_state == VideoLifecycleState.READY_FOR_REVIEW

    sm.transition_to(VideoLifecycleState.APPROVED, reason="Human operator approved")
    assert sm.current_state == VideoLifecycleState.APPROVED

    sm.transition_to(VideoLifecycleState.UPLOADING, reason="Initiating private upload")
    assert sm.current_state == VideoLifecycleState.UPLOADING

    sm.transition_to(VideoLifecycleState.PUBLISHED, reason="Upload completed successfully")
    assert sm.current_state == VideoLifecycleState.PUBLISHED


def test_qa_failure_and_retry_transitions() -> None:
    """Verify QA failure loop and human rejection handling."""
    sm = LifecycleStateMachine(current_state=VideoLifecycleState.RENDERED)

    # QA failure -> QA_FAILED
    sm.transition_to(VideoLifecycleState.QA_FAILED, reason="Loudness failed LUFS target")
    assert sm.current_state == VideoLifecycleState.QA_FAILED

    # Retry producing from QA_FAILED
    sm.transition_to(VideoLifecycleState.PRODUCING, reason="Adjusting audio normalization")
    assert sm.current_state == VideoLifecycleState.PRODUCING


def test_human_rejection_and_blocking() -> None:
    """Verify review rejection and blocking transitions."""
    sm = LifecycleStateMachine(current_state=VideoLifecycleState.READY_FOR_REVIEW)

    # Rejection
    sm.transition_to(VideoLifecycleState.REJECTED, reason="Hook lacks emotional resonance")
    assert sm.current_state == VideoLifecycleState.REJECTED

    # From REJECTED back to PLANNED for revision
    sm.transition_to(VideoLifecycleState.PLANNED, reason="Revising angle")
    assert sm.current_state == VideoLifecycleState.PLANNED


def test_invalid_state_transition_raises_typed_error() -> None:
    """Verify attempting an illegal transition raises InvalidStateTransitionError."""
    sm = LifecycleStateMachine(current_state=VideoLifecycleState.CREATED)

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        sm.transition_to(VideoLifecycleState.PUBLISHED, reason="Cannot publish uncreated video")

    assert "Invalid transition from CREATED to PUBLISHED" in str(exc_info.value)


def test_failure_and_blocked_can_be_reached_from_active_states() -> None:
    """Verify any active processing state can transition to FAILED or BLOCKED."""
    for active_state in [
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.UPLOADING,
    ]:
        sm_fail = LifecycleStateMachine(current_state=active_state)
        sm_fail.transition_to(VideoLifecycleState.FAILED, reason="Fatal unrecoverable error")
        assert sm_fail.current_state == VideoLifecycleState.FAILED

        sm_block = LifecycleStateMachine(current_state=active_state)
        sm_block.transition_to(VideoLifecycleState.BLOCKED, reason="Missing API quota")
        assert sm_block.current_state == VideoLifecycleState.BLOCKED
