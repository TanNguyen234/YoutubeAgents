"""Services package for YouTube Autopilot."""

from app.services.claim_extractor import ClaimExtractor
from app.services.duplicate_detector import DuplicateDetector
from app.services.fact_checker import FactChecker
from app.services.pipeline_brain import BrainPipeline
from app.services.research_agent import ResearchAgent, ResearchFetchError
from app.services.script_generator import ScriptGenerator
from app.services.script_writer import ScriptWriter
from app.services.topic_evaluator import TopicEvaluationOutput, TopicEvaluator
from app.services.topic_strategist import TopicStrategist

__all__ = [
    "DuplicateDetector",
    "TopicStrategist",
    "TopicEvaluator",
    "TopicEvaluationOutput",
    "ResearchAgent",
    "ResearchFetchError",
    "ScriptGenerator",
    "ScriptWriter",
    "ClaimExtractor",
    "FactChecker",
    "BrainPipeline",
]
