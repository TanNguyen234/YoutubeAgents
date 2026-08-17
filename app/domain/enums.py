"""Domain enumerations for YouTube Autopilot."""

from enum import Enum


class VideoLifecycleState(str, Enum):
    """The 16 discrete states of the YouTube video production lifecycle."""

    CREATED = "CREATED"
    RESEARCHING = "RESEARCHING"
    PLANNED = "PLANNED"
    SCRIPTED = "SCRIPTED"
    VERIFIED = "VERIFIED"
    PRODUCING = "PRODUCING"
    RENDERED = "RENDERED"
    QA_FAILED = "QA_FAILED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UPLOADING = "UPLOADING"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class PlatformFormat(str, Enum):
    """Supported video aspect ratio and target format."""

    SHORTS_9_16 = "SHORTS_9_16"
    LONG_FORM_16_9 = "LONG_FORM_16_9"
    SQUARE_1_1 = "SQUARE_1_1"


class AssetType(str, Enum):
    """Types of media assets involved in video production."""

    IMAGE = "IMAGE"
    VIDEO_CLIP = "VIDEO_CLIP"
    AUDIO_VOICEOVER = "AUDIO_VOICEOVER"
    AUDIO_BGM = "AUDIO_BGM"
    SUBTITLES = "SUBTITLES"
    THUMBNAIL = "THUMBNAIL"
    METADATA = "METADATA"
    SCENE_CARD = "SCENE_CARD"
    FINAL_VIDEO = "FINAL_VIDEO"


class QualityStatus(str, Enum):
    """Quality gate validation verdicts (defaults safely to PENDING)."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"


class PrivacyStatus(str, Enum):
    """YouTube publication privacy status enum."""

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class PublicationStatus(str, Enum):
    """Publication job status."""

    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExperimentStatus(str, Enum):
    """A/B and strategy experiment status."""

    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    CONCLUDED = "CONCLUDED"
    ARCHIVED = "ARCHIVED"


class ClaimVerificationVerdict(str, Enum):
    """Fact checker resolution verdict for an individual factual claim."""

    VERIFIED = "VERIFIED"
    REWRITE_REQUIRED = "REWRITE_REQUIRED"
    REMOVE = "REMOVE"
    UNVERIFIABLE = "UNVERIFIABLE"
