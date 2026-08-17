"""Finite state machine managing the 16-stage video lifecycle transitions."""

from typing import Dict, List, Optional, Set
from app.domain.enums import VideoLifecycleState


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal lifecycle state transition is attempted."""

    def __init__(self, from_state: VideoLifecycleState, to_state: VideoLifecycleState, reason: Optional[str] = None):
        msg = f"Invalid transition from {from_state.value} to {to_state.value}"
        if reason:
            msg += f" (Reason: {reason})"
        super().__init__(msg)
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason


# Explicit transition map defining allowable next states for each lifecycle phase
ALLOWED_TRANSITIONS: Dict[VideoLifecycleState, Set[VideoLifecycleState]] = {
    VideoLifecycleState.CREATED: {
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.RESEARCHING: {
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.PLANNED: {
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.SCRIPTED: {
        VideoLifecycleState.VERIFIED,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.VERIFIED: {
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.PRODUCING: {
        VideoLifecycleState.RENDERED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.RENDERED: {
        VideoLifecycleState.QA_FAILED,
        VideoLifecycleState.READY_FOR_REVIEW,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.QA_FAILED: {
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.READY_FOR_REVIEW: {
        VideoLifecycleState.PRODUCING,  # media-only rerender, canonical narration unchanged
        VideoLifecycleState.APPROVED,
        VideoLifecycleState.REJECTED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.APPROVED: {
        VideoLifecycleState.UPLOADING,
        VideoLifecycleState.SCHEDULED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.REJECTED: {
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.UPLOADING: {
        VideoLifecycleState.PUBLISHED,
        VideoLifecycleState.SCHEDULED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.SCHEDULED: {
        VideoLifecycleState.UPLOADING,
        VideoLifecycleState.PUBLISHED,
        VideoLifecycleState.FAILED,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.PUBLISHED: {
        VideoLifecycleState.FAILED,
    },
    VideoLifecycleState.FAILED: {
        VideoLifecycleState.CREATED,
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.BLOCKED,
    },
    VideoLifecycleState.BLOCKED: {
        VideoLifecycleState.CREATED,
        VideoLifecycleState.RESEARCHING,
        VideoLifecycleState.PLANNED,
        VideoLifecycleState.SCRIPTED,
        VideoLifecycleState.PRODUCING,
        VideoLifecycleState.READY_FOR_REVIEW,
        VideoLifecycleState.APPROVED,
        VideoLifecycleState.UPLOADING,
        VideoLifecycleState.FAILED,
    },
}


class LifecycleStateMachine:
    """Manages the state transitions and maintains an audit log for a video project."""

    def __init__(self, current_state: VideoLifecycleState = VideoLifecycleState.CREATED):
        self.current_state = current_state
        self.history: List[Dict[str, str]] = []

    def can_transition_to(self, to_state: VideoLifecycleState) -> bool:
        """Check if transition to target state is legally permitted."""
        allowed = ALLOWED_TRANSITIONS.get(self.current_state, set())
        return to_state in allowed

    def transition_to(self, to_state: VideoLifecycleState, reason: str = "") -> None:
        """Execute a state transition with validation and audit logging."""
        if not self.can_transition_to(to_state):
            raise InvalidStateTransitionError(self.current_state, to_state, reason=reason)

        prev_state = self.current_state
        self.current_state = to_state
        self.history.append({
            "from_state": prev_state.value,
            "to_state": to_state.value,
            "reason": reason,
        })
