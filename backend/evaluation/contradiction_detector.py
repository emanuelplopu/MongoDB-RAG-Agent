"""Contradiction detection between multi-turn responses."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional

from backend.evaluation.models import (
    ClaimExtraction,
    Contradiction,
    ContradictionAttribution,
    ContradictionResult,
)

logger = logging.getLogger(__name__)

# Performance budgets
DETECTION_BUDGET_MS = 2000
CIRCUIT_BREAKER_MS = 3000

# Negation markers for conflict detection
_NEGATION_WORDS = {"not", "no", "never", "neither", "nor", "none", "nothing", "nowhere", "cannot"}
_NEGATION_PREFIXES = ("no longer", "not any", "is not", "are not", "was not", "were not", "does not", "did not")

# Sentence boundary pattern
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Numeric extraction pattern
_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")


class ContradictionDetector:
    """
    Detects contradictions between responses in multi-turn sessions.

    Only activates for turn > 1. Extracts claims from both responses,
    performs pairwise comparison for semantic conflicts.

    Performance budget: 2000ms with 3000ms circuit breaker.
    """

    async def detect(
        self,
        previous_response: str,
        current_response: str,
        session_id: str,
        turn: int,
        timeout_ms: float = CIRCUIT_BREAKER_MS,
    ) -> ContradictionResult:
        """
        Detect contradictions between previous and current response.

        Args:
            previous_response: The previous turn's response text.
            current_response: The current turn's response text.
            session_id: Session identifier.
            turn: Current turn number (must be > 1).
            timeout_ms: Circuit breaker timeout.

        Returns:
            ContradictionResult with any detected contradictions.
        """
        # Skip for first turn — no previous response to compare
        if turn <= 1:
            return ContradictionResult(
                session_id=session_id,
                turn=turn,
                has_contradictions=False,
            )

        start_ms = time.perf_counter() * 1000

        try:
            result = await asyncio.wait_for(
                self._run_detection(previous_response, current_response, session_id, turn),
                timeout=timeout_ms / 1000,
            )
            result.detection_duration_ms = (time.perf_counter() * 1000) - start_ms
            return result
        except asyncio.TimeoutError:
            elapsed = (time.perf_counter() * 1000) - start_ms
            logger.warning(
                "Contradiction detection timed out after %.0fms for session %s turn %d",
                elapsed,
                session_id,
                turn,
            )
            return ContradictionResult(
                session_id=session_id,
                turn=turn,
                has_contradictions=False,
                timed_out=True,
                detection_duration_ms=elapsed,
            )

    async def _run_detection(
        self,
        previous_response: str,
        current_response: str,
        session_id: str,
        turn: int,
    ) -> ContradictionResult:
        """Core detection logic wrapped for timeout control."""
        claims_a = self.extract_claims(previous_response)
        claims_b = self.extract_claims(current_response)

        contradictions = self.compare_claims(claims_a, claims_b)

        # Attribute each contradiction
        for contradiction in contradictions:
            contradiction.attribution = self.attribute_contradiction(contradiction)

        return ContradictionResult(
            session_id=session_id,
            turn=turn,
            contradictions=contradictions,
            has_contradictions=len(contradictions) > 0,
        )

    def extract_claims(self, text: str) -> list[ClaimExtraction]:
        """
        Extract factual claims from response text (rule-based).

        Heuristics:
        - Split into sentences
        - Filter for declarative sentences (not questions, not filler)
        - Score confidence based on assertiveness markers

        TODO: Replace with LLM-based claim extraction in Phase 3.2
        """
        if not text or not text.strip():
            return []

        sentences = _SENTENCE_SPLIT_RE.split(text.strip())
        claims: list[ClaimExtraction] = []

        for sentence in sentences:
            sentence = sentence.strip().rstrip(".")
            if not sentence:
                continue

            # Filter short sentences (less than 5 words)
            words = sentence.split()
            if len(words) < 5:
                continue

            # Only keep declarative sentences
            if not self._is_declarative(sentence):
                continue

            # Score confidence based on assertiveness
            confidence = self._score_confidence(sentence)

            claims.append(
                ClaimExtraction(
                    claim_text=sentence,
                    source_span=sentence[:100] if len(sentence) > 100 else sentence,
                    confidence=confidence,
                )
            )

        return claims

    def compare_claims(
        self,
        claims_a: list[ClaimExtraction],
        claims_b: list[ClaimExtraction],
    ) -> list[Contradiction]:
        """
        Pairwise comparison of claims for contradictions.

        Detection heuristics:
        - Negation patterns (A says X, B says "not X" or "no longer X")
        - Numeric conflicts (A says "3 documents", B says "5 documents")
        - Temporal conflicts (A says "before", B says "after")
        - Direct opposition (antonym detection)

        TODO: Replace with LLM semantic comparison in Phase 3.2
        """
        contradictions: list[Contradiction] = []

        for claim_a in claims_a:
            for claim_b in claims_b:
                conflict_type = self._detect_conflict(claim_a.claim_text, claim_b.claim_text)
                if conflict_type:
                    confidence = min(claim_a.confidence, claim_b.confidence) * 0.8
                    contradictions.append(
                        Contradiction(
                            claim_a=claim_a,
                            claim_b=claim_b,
                            conflict_type=conflict_type,
                            confidence=confidence,
                        )
                    )

        return contradictions

    def attribute_contradiction(self, contradiction: Contradiction) -> ContradictionAttribution:
        """
        Determine the likely source of a contradiction.

        Heuristics:
        - If both claims cite different sources → DATA_RELATED
        - If contradiction involves model-specific language → MODEL_VARIANCE
        - Default → STRATEGY_RELATED
        """
        text_a = contradiction.claim_a.claim_text.lower()
        text_b = contradiction.claim_b.claim_text.lower()

        # Check for source/citation references indicating data conflict
        source_markers = ("according to", "source:", "from the document", "the file", "the record")
        a_has_source = any(marker in text_a for marker in source_markers)
        b_has_source = any(marker in text_b for marker in source_markers)

        if a_has_source and b_has_source:
            return ContradictionAttribution.DATA_RELATED

        # Check for model-specific hedging language suggesting model variance
        model_markers = ("i think", "it seems", "possibly", "likely", "approximately", "i believe")
        a_has_model = any(marker in text_a for marker in model_markers)
        b_has_model = any(marker in text_b for marker in model_markers)

        if a_has_model or b_has_model:
            return ContradictionAttribution.MODEL_VARIANCE

        return ContradictionAttribution.STRATEGY_RELATED

    def _is_declarative(self, sentence: str) -> bool:
        """Check if sentence is a declarative statement (not question/exclamation)."""
        stripped = sentence.strip()
        if not stripped:
            return False
        # Questions end with ? or start with question words
        if stripped.endswith("?"):
            return False
        if stripped.endswith("!"):
            return False
        # Filter filler/greeting patterns
        filler_starts = ("hi ", "hello", "sure", "okay", "well,", "um", "uh")
        if stripped.lower().startswith(filler_starts):
            return False
        return True

    def _detect_conflict(self, claim_a: str, claim_b: str) -> Optional[str]:
        """Detect the type of conflict between two claims, if any."""
        # Check negation conflict
        if self._detect_negation_conflict(claim_a, claim_b):
            return "semantic"

        # Check numeric conflict
        if self._detect_numeric_conflict(claim_a, claim_b):
            return "factual"

        # Check temporal conflict
        if self._detect_temporal_conflict(claim_a, claim_b):
            return "temporal"

        return None

    def _detect_negation_conflict(self, claim_a: str, claim_b: str) -> bool:
        """Check if one claim negates the other."""
        a_lower = claim_a.lower()
        b_lower = claim_b.lower()

        # Find shared keywords (nouns/subjects) between claims
        words_a = set(a_lower.split())
        words_b = set(b_lower.split())
        # Remove common stopwords for overlap check
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "has", "have", "had",
                     "do", "does", "did", "will", "would", "could", "should", "may", "might",
                     "to", "of", "in", "for", "on", "with", "at", "by", "from", "it", "this", "that"}
        meaningful_a = words_a - stopwords
        meaningful_b = words_b - stopwords
        shared = meaningful_a & meaningful_b

        # Need at least one shared keyword to consider them about the same topic
        if not shared:
            return False

        # Check if one has negation markers near shared keywords
        a_has_negation = any(neg in a_lower for neg in _NEGATION_WORDS) or any(
            prefix in a_lower for prefix in _NEGATION_PREFIXES
        )
        b_has_negation = any(neg in b_lower for neg in _NEGATION_WORDS) or any(
            prefix in b_lower for prefix in _NEGATION_PREFIXES
        )

        # Contradiction if exactly one side is negated (XOR)
        return a_has_negation != b_has_negation

    def _detect_numeric_conflict(self, claim_a: str, claim_b: str) -> bool:
        """Check if claims contain conflicting numbers for the same entity."""
        numbers_a = _NUMBER_RE.findall(claim_a)
        numbers_b = _NUMBER_RE.findall(claim_b)

        if not numbers_a or not numbers_b:
            return False

        # Find shared context words
        words_a = set(claim_a.lower().split())
        words_b = set(claim_b.lower().split())
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in", "for", "on", "with"}
        shared = (words_a - stopwords) & (words_b - stopwords)

        # Need shared context to indicate they're about the same thing
        if len(shared) < 2:
            return False

        # Check if numbers differ
        nums_a_set = set(numbers_a)
        nums_b_set = set(numbers_b)

        # If they share context but have different numbers, it's a conflict
        return bool(nums_a_set) and bool(nums_b_set) and not nums_a_set.intersection(nums_b_set)

    def _detect_temporal_conflict(self, claim_a: str, claim_b: str) -> bool:
        """Check for temporal contradictions (before/after, past/future conflicts)."""
        temporal_pairs = [
            ("before", "after"),
            ("previously", "currently"),
            ("earlier", "later"),
            ("past", "future"),
            ("old", "new"),
        ]

        a_lower = claim_a.lower()
        b_lower = claim_b.lower()

        # Check shared keywords
        words_a = set(a_lower.split())
        words_b = set(b_lower.split())
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in", "for", "on", "with"}
        shared = (words_a - stopwords) & (words_b - stopwords)

        if len(shared) < 2:
            return False

        for word_a, word_b in temporal_pairs:
            if (word_a in a_lower and word_b in b_lower) or (word_b in a_lower and word_a in b_lower):
                return True

        return False

    def _score_confidence(self, sentence: str) -> float:
        """Score claim confidence based on assertiveness markers."""
        lower = sentence.lower()

        # High confidence markers
        if any(marker in lower for marker in ("certainly", "definitely", "always", "must be", "confirmed")):
            return 0.95

        # Low confidence markers
        if any(marker in lower for marker in ("maybe", "perhaps", "possibly", "might", "could be")):
            return 0.5

        # Default moderate confidence
        return 0.8
