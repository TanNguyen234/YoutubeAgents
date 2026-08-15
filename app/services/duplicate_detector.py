"""Lightweight deterministic duplicate detection using normalized title and token similarity."""

import re
import string
from difflib import SequenceMatcher
from typing import List, Optional, Set, Tuple


class DuplicateDetector:
    """Detects duplicate or near-duplicate topics without heavyweight vector databases."""

    def __init__(self, similarity_threshold: float = 0.65):
        self.similarity_threshold = similarity_threshold

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text by converting to lowercase, stripping punctuation, and collapsing whitespace."""
        text = text.lower().strip()
        # Remove punctuation
        text = text.translate(str.maketrans("", "", string.punctuation))
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @classmethod
    def extract_tokens(cls, text: str) -> Set[str]:
        """Extract clean token set from normalized string."""
        normalized = cls.normalize_text(text)
        return set(normalized.split()) if normalized else set()

    @classmethod
    def token_jaccard_similarity(cls, text_a: str, text_b: str) -> float:
        """Calculate Jaccard index between token sets of two strings."""
        tokens_a = cls.extract_tokens(text_a)
        tokens_b = cls.extract_tokens(text_b)
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = len(tokens_a.intersection(tokens_b))
        union = len(tokens_a.union(tokens_b))
        return float(intersection) / float(union) if union > 0 else 0.0

    @classmethod
    def sequence_similarity(cls, text_a: str, text_b: str) -> float:
        """Calculate character-level Levenshtein-like SequenceMatcher ratio on normalized text."""
        norm_a = cls.normalize_text(text_a)
        norm_b = cls.normalize_text(text_b)
        if not norm_a or not norm_b:
            return 0.0
        return SequenceMatcher(None, norm_a, norm_b).ratio()

    def calculate_similarity(self, candidate: str, target: str) -> float:
        """Calculate similarity score combining token overlap (order-independent) and sequence ratio."""
        jaccard = self.token_jaccard_similarity(candidate, target)
        sequence = self.sequence_similarity(candidate, target)
        blended = (0.6 * jaccard) + (0.4 * sequence)
        # Use max to capture either high token overlap (reordered keywords) or high sequence similarity
        return max(jaccard, blended)

    def check_duplicate(
        self, candidate: str, existing_topics: List[str]
    ) -> Tuple[bool, float, Optional[str]]:
        """Check if candidate topic is a duplicate of any existing topic.

        Returns:
            (is_duplicate, max_similarity_score, matched_topic)
        """
        if not existing_topics or not candidate.strip():
            return False, 0.0, None

        max_score = 0.0
        matched_topic = None

        for existing in existing_topics:
            if not existing.strip():
                continue
            # Direct exact match check
            if self.normalize_text(candidate) == self.normalize_text(existing):
                return True, 1.0, existing

            score = self.calculate_similarity(candidate, existing)
            if score > max_score:
                max_score = score
                matched_topic = existing

        is_dup = max_score >= self.similarity_threshold
        return is_dup, round(max_score, 4), (matched_topic if is_dup else None)
