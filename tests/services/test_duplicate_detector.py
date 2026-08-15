"""Unit tests for duplicate detection using normalized title and token similarity."""

import pytest
from app.services.duplicate_detector import DuplicateDetector


@pytest.fixture
def detector():
    return DuplicateDetector(similarity_threshold=0.65)


def test_exact_match_detected_as_duplicate(detector):
    recent = ["How to Build Local AI Agents in Python", "Understanding SQLite WAL Mode"]
    candidate = "How to Build Local AI Agents in Python"
    is_dup, score, matched = detector.check_duplicate(candidate, recent)
    assert is_dup is True
    assert score >= 0.99
    assert matched == "How to Build Local AI Agents in Python"


def test_near_duplicate_with_punctuation_and_case(detector):
    recent = ["How to Build Local AI Agents in Python!"]
    candidate = "how to build local ai agents in python?"
    is_dup, score, matched = detector.check_duplicate(candidate, recent)
    assert is_dup is True
    assert score >= 0.90


def test_token_reordered_near_duplicate(detector):
    recent = ["Python AI Agents: Build Local Systems"]
    candidate = "Build Local AI Agents in Python"
    is_dup, score, matched = detector.check_duplicate(candidate, recent)
    assert is_dup is True
    assert score >= 0.65


def test_distinct_topics_not_flagged(detector):
    recent = ["How to Build Local AI Agents in Python", "Understanding SQLite WAL Mode"]
    candidate = "Top 5 High-Paying Cloud Certifications in 2026"
    is_dup, score, matched = detector.check_duplicate(candidate, recent)
    assert is_dup is False
    assert score < 0.40
    assert matched is None


def test_empty_recent_history_returns_false(detector):
    is_dup, score, matched = detector.check_duplicate("Any Topic", [])
    assert is_dup is False
    assert score == 0.0
    assert matched is None
