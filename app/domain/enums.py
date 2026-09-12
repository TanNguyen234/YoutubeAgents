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


class ReviewAction(str, Enum):
    """Action taken by a human operator during Stage 12 Human Review Gate."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RERENDER = "RERENDER"
    BLOCK = "BLOCK"


class EditorialSlotStatus(str, Enum):
    """Lifecycle status of a scheduled editorial calendar slot."""

    PLANNED = "PLANNED"
    IN_PRODUCTION = "IN_PRODUCTION"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    MISSED = "MISSED"


class TitleVariantType(str, Enum):
    """Psychological angle used for CTR title generation."""

    CURIOSITY_GAP = "CURIOSITY_GAP"
    DIRECT_VALUE = "DIRECT_VALUE"
    PROVOCATIVE_QUESTION = "PROVOCATIVE_QUESTION"


class ContentFormat(str, Enum):
    """Editorial video format archetype informing visual pacing and shot distribution."""

    EXPLAINER = "EXPLAINER"
    DEMO = "DEMO"
    COMPARISON = "COMPARISON"
    EXPERIMENT = "EXPERIMENT"
    CASE_STUDY = "CASE_STUDY"
    BREAKDOWN = "BREAKDOWN"
    MYTH_BUSTING = "MYTH_BUSTING"
    STORY = "STORY"
    CHALLENGE = "CHALLENGE"
    NEWS = "NEWS"
    RANKING = "RANKING"
    BEFORE_AFTER = "BEFORE_AFTER"
    PROBLEM_SOLUTION = "PROBLEM_SOLUTION"


class ApprovalOrigin(str, Enum):
    """Provenance of human or automated review approval."""

    HUMAN = "HUMAN"
    AUTOMATION = "AUTOMATION"
    TEST = "TEST"


class PrimaryVideoGoal(str, Enum):
    """Primary objective of the video."""

    WATCH_TIME = "WATCH_TIME"
    SUBSCRIBE = "SUBSCRIBE"
    EDUCATE = "EDUCATE"
    AUTHORITY = "AUTHORITY"
    LEAD_GENERATION = "LEAD_GENERATION"
    SHAREABILITY = "SHAREABILITY"
    REVENUE = "REVENUE"


class TonePreset(str, Enum):
    """Narrative delivery tone preset."""

    EXCITED = "EXCITED"
    SERIOUS = "SERIOUS"
    CONVERSATIONAL = "CONVERSATIONAL"
    INVESTIGATIVE = "INVESTIGATIVE"
    CINEMATIC_STORY = "CINEMATIC_STORY"
    TECHNICAL = "TECHNICAL"


class HookAngle(str, Enum):
    """Psychological or narrative angle for opening hooks."""

    CURIOSITY_GAP = "CURIOSITY_GAP"
    PAIN_POINT = "PAIN_POINT"
    CONTRARIAN = "CONTRARIAN"
    RESULT_FIRST = "RESULT_FIRST"
    STAKES_FIRST = "STAKES_FIRST"


class RetentionCueType(str, Enum):
    """Discrete retention pacing and structural cues."""

    OPEN_LOOP = "OPEN_LOOP"
    REHOOK = "REHOOK"
    PATTERN_INTERRUPT = "PATTERN_INTERRUPT"
    ESCALATION = "ESCALATION"
    REVEAL = "REVEAL"
    CLIMAX = "CLIMAX"
    LOOP_CLOSE = "LOOP_CLOSE"
    CTA = "CTA"


class ConcreteAnchorType(str, Enum):
    """Concrete grounding anchor archetype providing relatable proof or demonstration."""

    REAL_EXAMPLE = "REAL_EXAMPLE"
    ANALOGY = "ANALOGY"
    COMPARISON = "COMPARISON"
    MINI_CASE = "MINI_CASE"
    DEMONSTRATION = "DEMONSTRATION"


class PsychologicalMechanism(str, Enum):
    """Descriptive label for audience engagement mechanism (no neuroscience claims)."""

    CURIOSITY_GAP = "CURIOSITY_GAP"
    ANTICIPATION = "ANTICIPATION"
    CONTRAST = "CONTRAST"
    STAKES = "STAKES"
    NOVELTY = "NOVELTY"
    PREDICTION_ERROR = "PREDICTION_ERROR"
    PAYOFF = "PAYOFF"
    CALLBACK = "CALLBACK"



