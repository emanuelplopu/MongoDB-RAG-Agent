"""
Lightweight PII pseudonymizer for telemetry data.

Adapted from pe-pseudonymizer detection engines.
Provides cross-reference consistent pseudonymization within a session.

Detection engines:
- Regex: Always available, handles emails, phones, IBANs, addresses
- spaCy NER: Optional, provides person/org/location detection
- Presidio: Optional, provides comprehensive PII detection

Graceful degradation: if presidio or spaCy are not installed,
falls back to regex-only mode.
"""

import re
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class PIIType:
    """PII entity type constants."""

    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    COMPANY = "COMPANY"
    IBAN = "IBAN"


@dataclass
class PIISpan:
    """A detected PII span within text."""

    start: int
    end: int
    text: str
    pii_type: str
    confidence: float
    engine: str


@dataclass
class ResolvedEntity:
    """A resolved entity with cross-reference consistency."""

    entity_id: str
    entity_type: str
    canonical_form: str
    variants: set = field(default_factory=set)
    alias: str = ""


class EntityRegistry:
    """Maintains cross-reference consistency: same person always gets same alias.

    Within a session, the same detected text always resolves to the same alias,
    ensuring consistent pseudonymization across multiple interactions.
    """

    def __init__(self) -> None:
        self.entities: dict[str, ResolvedEntity] = {}
        self._variant_index: dict[str, str] = {}  # lowercase(text) -> entity_id
        self._next_id: dict[str, int] = {}  # type -> counter

    def find_or_create(self, text: str, pii_type: str) -> ResolvedEntity:
        """Find existing entity by variant text, or create new one.

        Args:
            text: The detected PII text.
            pii_type: The type of PII entity.

        Returns:
            The resolved entity with a consistent alias.
        """
        key = text.lower().strip()
        if key in self._variant_index:
            return self.entities[self._variant_index[key]]

        # Create new entity
        counter = self._next_id.get(pii_type, 0) + 1
        self._next_id[pii_type] = counter
        entity_id = f"{pii_type.lower()}_{counter:03d}"

        # Generate alias
        alias = self._generate_alias(pii_type, counter)

        entity = ResolvedEntity(
            entity_id=entity_id,
            entity_type=pii_type,
            canonical_form=text,
            variants={text},
            alias=alias,
        )
        self.entities[entity_id] = entity
        self._variant_index[key] = entity_id
        return entity

    def add_variant(self, entity_id: str, text: str) -> None:
        """Register additional variant for existing entity.

        Args:
            entity_id: The entity to add a variant to.
            text: The variant text form.
        """
        key = text.lower().strip()
        self._variant_index[key] = entity_id
        if entity_id in self.entities:
            self.entities[entity_id].variants.add(text)

    def _generate_alias(self, pii_type: str, counter: int) -> str:
        """Generate deterministic alias like Person_A, Company_B.

        Args:
            pii_type: The PII type for labeling.
            counter: Sequential counter for suffix generation.

        Returns:
            A human-readable alias string.
        """
        # Use letters for first 26, then Letter+number
        if counter <= 26:
            suffix = chr(64 + counter)  # A, B, C...
        else:
            suffix = f"{chr(64 + ((counter - 1) % 26) + 1)}{(counter - 1) // 26 + 1}"

        type_labels = {
            PIIType.PERSON: "Person",
            PIIType.EMAIL: "Email",
            PIIType.PHONE: "Phone",
            PIIType.ADDRESS: "Address",
            PIIType.COMPANY: "Company",
            PIIType.IBAN: "IBAN",
        }
        label = type_labels.get(pii_type, "Entity")
        return f"{label}_{suffix}"

    def get_stats(self) -> dict:
        """Get summary statistics about resolved entities.

        Returns:
            Dict with total_entities count and types_found list.
        """
        return {
            "total_entities": len(self.entities),
            "types_found": list(set(e.entity_type for e in self.entities.values())),
        }


class RegexDetector:
    """Regex-based PII detection for Austrian/German legal context.

    Always available — no external dependencies required.
    """

    # Austrian/German patterns
    PATTERNS: dict[str, str] = {
        PIIType.EMAIL: r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        PIIType.PHONE: r"(?:\+43|0043|0)\s*\d{1,4}[\s/-]?\d{3,4}[\s/-]?\d{2,4}",
        PIIType.IBAN: r"\bAT\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b",
    }

    # Austrian address patterns
    ADDRESS_SUFFIXES = r"(?:straße|strasse|gasse|weg|platz|ring|allee|damm|ufer|promenade)"
    ADDRESS_PATTERN = rf"\b\w+{ADDRESS_SUFFIXES}\s+\d{{1,4}}(?:\s*/\s*\d{{1,4}})?\b"

    def detect(self, text: str) -> list[PIISpan]:
        """Detect PII entities using regex patterns.

        Args:
            text: Input text to scan.

        Returns:
            List of detected PII spans.
        """
        spans: list[PIISpan] = []
        for pii_type, pattern in self.PATTERNS.items():
            for match in re.finditer(pattern, text, re.IGNORECASE):
                spans.append(
                    PIISpan(
                        start=match.start(),
                        end=match.end(),
                        text=match.group(),
                        pii_type=pii_type,
                        confidence=0.90,
                        engine="regex",
                    )
                )
        # Address detection
        for match in re.finditer(self.ADDRESS_PATTERN, text, re.IGNORECASE):
            spans.append(
                PIISpan(
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                    pii_type=PIIType.ADDRESS,
                    confidence=0.75,
                    engine="regex",
                )
            )
        return spans


class SpacyDetector:
    """spaCy NER-based detection. Gracefully unavailable if spaCy not installed."""

    def __init__(self) -> None:
        self._nlp = None
        self._available = False
        try:
            import spacy

            self._nlp = spacy.load("de_core_news_lg")
            self._available = True
            logger.info("spaCy detector initialized with de_core_news_lg")
        except (ImportError, OSError) as e:
            logger.warning(f"spaCy detector unavailable: {e}")

    @property
    def available(self) -> bool:
        """Whether spaCy NER detection is available."""
        return self._available

    def detect(self, text: str) -> list[PIISpan]:
        """Detect PII entities using spaCy NER.

        Args:
            text: Input text to scan.

        Returns:
            List of detected PII spans, or empty if spaCy unavailable.
        """
        if not self._available:
            return []
        doc = self._nlp(text)
        spans: list[PIISpan] = []
        label_map = {
            "PER": PIIType.PERSON,
            "ORG": PIIType.COMPANY,
            "LOC": PIIType.ADDRESS,
            "GPE": PIIType.ADDRESS,
        }
        for ent in doc.ents:
            pii_type = label_map.get(ent.label_)
            if pii_type:
                spans.append(
                    PIISpan(
                        start=ent.start_char,
                        end=ent.end_char,
                        text=ent.text,
                        pii_type=pii_type,
                        confidence=0.75,
                        engine="spacy",
                    )
                )
        return spans


class PresidioDetector:
    """Presidio-based detection. Gracefully unavailable if not installed."""

    def __init__(self) -> None:
        self._analyzer = None
        self._available = False
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "de", "model_name": "de_core_news_lg"}],
                }
            )
            self._analyzer = AnalyzerEngine(
                nlp_engine=provider.create_engine(),
                supported_languages=["de", "en"],
            )
            self._available = True
            logger.info("Presidio detector initialized")
        except (ImportError, OSError) as e:
            logger.warning(f"Presidio detector unavailable: {e}")

    @property
    def available(self) -> bool:
        """Whether Presidio detection is available."""
        return self._available

    def detect(self, text: str, language: str = "de") -> list[PIISpan]:
        """Detect PII entities using Presidio analyzer.

        Args:
            text: Input text to scan.
            language: Language code for analysis.

        Returns:
            List of detected PII spans, or empty if Presidio unavailable.
        """
        if not self._available:
            return []
        results = self._analyzer.analyze(
            text=text,
            language=language,
            entities=[
                "PERSON",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "IBAN_CODE",
                "LOCATION",
                "ORGANIZATION",
            ],
        )
        type_map = {
            "PERSON": PIIType.PERSON,
            "EMAIL_ADDRESS": PIIType.EMAIL,
            "PHONE_NUMBER": PIIType.PHONE,
            "IBAN_CODE": PIIType.IBAN,
            "LOCATION": PIIType.ADDRESS,
            "ORGANIZATION": PIIType.COMPANY,
        }
        spans: list[PIISpan] = []
        for r in results:
            pii_type = type_map.get(r.entity_type)
            if pii_type:
                spans.append(
                    PIISpan(
                        start=r.start,
                        end=r.end,
                        text=text[r.start : r.end],
                        pii_type=pii_type,
                        confidence=r.score,
                        engine="presidio",
                    )
                )
        return spans


class TelemetryPseudonymizer:
    """Main pseudonymizer for telemetry data.

    Maintains per-session entity registry for cross-reference consistency.
    Uses ensemble voting from available detection engines.
    Falls back gracefully to regex-only if optional engines are unavailable.
    """

    def __init__(self) -> None:
        self._regex = RegexDetector()
        self._spacy = SpacyDetector()
        self._presidio = PresidioDetector()
        self._session_registries: dict[str, EntityRegistry] = {}

        engines = ["regex"]
        if self._spacy.available:
            engines.append("spacy")
        if self._presidio.available:
            engines.append("presidio")
        logger.info(f"TelemetryPseudonymizer initialized with engines: {engines}")

    def get_or_create_registry(self, session_id: str) -> EntityRegistry:
        """Get or create entity registry for a session.

        Args:
            session_id: The session identifier.

        Returns:
            EntityRegistry for the session.
        """
        if session_id not in self._session_registries:
            self._session_registries[session_id] = EntityRegistry()
        return self._session_registries[session_id]

    def cleanup_session(self, session_id: str) -> None:
        """Remove session registry when no longer needed.

        Args:
            session_id: The session to clean up.
        """
        self._session_registries.pop(session_id, None)

    def pseudonymize(
        self,
        text: str,
        session_id: str,
        language: str = "de",
        include_markers: bool = False,
    ) -> tuple[str, dict]:
        """Pseudonymize text with cross-reference consistency within a session.

        Args:
            text: Text to pseudonymize.
            session_id: Session ID for cross-reference tracking.
            language: Language code for detection engines.
            include_markers: When True, wrap replacements with ``[PII:{TYPE}]...[/PII]``
                tags so the protected output shows exactly what was redacted.

        Returns:
            Tuple of (pseudonymized_text, stats_dict).
        """
        if not text or not text.strip():
            return text, {"entities_detected": 0, "types_found": []}

        registry = self.get_or_create_registry(session_id)

        # Detect PII with all available engines
        all_spans: list[PIISpan] = []
        all_spans.extend(self._regex.detect(text))
        all_spans.extend(self._spacy.detect(text))
        all_spans.extend(self._presidio.detect(text, language))

        # Deduplicate overlapping spans (prefer higher confidence, longer span)
        merged = self._merge_spans(all_spans)

        # Sort by position (reverse for safe replacement)
        merged.sort(key=lambda s: s.start, reverse=True)

        # Replace with consistent aliases
        result = text
        for span in merged:
            entity = registry.find_or_create(span.text, span.pii_type)
            if include_markers:
                replacement = f"[PII:{entity.entity_type}]{entity.alias}[/PII]"
            else:
                replacement = entity.alias
            result = result[:span.start] + replacement + result[span.end:]

        stats = registry.get_stats()
        stats["entities_detected"] = len(merged)
        return result, stats

    def _merge_spans(self, spans: list[PIISpan]) -> list[PIISpan]:
        """Merge overlapping spans, preferring higher confidence and longer spans.

        Args:
            spans: All detected spans from all engines.

        Returns:
            Non-overlapping list of best spans.
        """
        if not spans:
            return []

        # Sort by start position, then by length (longer first)
        spans.sort(key=lambda s: (s.start, -(s.end - s.start)))

        merged: list[PIISpan] = []
        for span in spans:
            # Check if overlaps with any already accepted span
            overlaps = False
            for existing in merged:
                if span.start < existing.end and span.end > existing.start:
                    overlaps = True
                    # Replace if higher confidence and same or longer
                    if span.confidence > existing.confidence and (
                        span.end - span.start
                    ) >= (existing.end - existing.start):
                        merged.remove(existing)
                        merged.append(span)
                    break
            if not overlaps:
                merged.append(span)

        return merged
