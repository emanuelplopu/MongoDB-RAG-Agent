"""
Operational Trace module for Strategy OS.

Builds OperationalTraceSummary from strategy execution results,
mapping internal OmittedReason codes to user-facing i18n messages.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

import logging

logger = logging.getLogger(__name__)


# State.metadata key under which per-run trace events are accumulated.
# Each entry is a dict with at least {"event", "payload"}.
TRACE_EVENTS_METADATA_KEY = "trace_events"


def emit_trace_event(
    state: Any,
    event: str,
    payload: dict,
) -> dict:
    """Append a structured trace event to the run state's metadata.

    This is the lightweight, in-process observability channel used by
    node executors to record decisions that don't fit cleanly into
    ``NodeOutput.output_data`` (e.g. budgeter decisions, partial
    fallbacks, retry signals).  The event is also logged at INFO
    level so it shows up in normal application logs.

    Args:
        state: A :class:`~backend.agent.strategy.models.StrategyRunState`
            (or any object exposing a mutable ``metadata`` dict).
            Passing ``None`` is tolerated and turns the call into a
            log-only no-op.
        event: Short snake_case event name, e.g.
            ``"context_budget_applied"``.
        payload: Arbitrary serialisable dict describing the event.

    Returns:
        The event entry that was appended (or would have been if
        ``state`` was ``None``).
    """
    entry = {"event": event, "payload": payload}
    logger.info("strategy_trace_event=%s payload=%s", event, payload)
    if state is not None and hasattr(state, "metadata"):
        events = state.metadata.setdefault(TRACE_EVENTS_METADATA_KEY, [])
        events.append(entry)
    return entry


# OmittedReason i18n message registry
OMITTED_REASON_MESSAGES: dict[str, dict[str, dict[str, str]]] = {
    "boilerplate_filtered": {
        "de": {"title": "Textbaustein gefiltert", "message": "Standardtext wurde aus den Ergebnissen entfernt."},
        "en": {"title": "Boilerplate filtered", "message": "Standard text was removed from results."},
    },
    "forbidden_source": {
        "de": {"title": "Unzulässige Quelle", "message": "Eine Quelle wurde aufgrund von Zugriffsrichtlinien ausgeschlossen."},
        "en": {"title": "Forbidden source", "message": "A source was excluded due to access policies."},
    },
    "cross_matter_blocked": {
        "de": {"title": "Mandatsübergreifend blockiert", "message": "Quelle aus anderem Mandat ausgeschlossen."},
        "en": {"title": "Cross-matter blocked", "message": "Source from different matter excluded."},
    },
    "cross_profile_blocked": {
        "de": {"title": "Profilübergreifend blockiert", "message": "Quelle aus anderem Profil ausgeschlossen."},
        "en": {"title": "Cross-profile blocked", "message": "Source from different profile excluded."},
    },
    "web_blocked": {
        "de": {"title": "Web-Zugriff blockiert", "message": "Externe Webquellen sind für diese Anfrage nicht erlaubt."},
        "en": {"title": "Web access blocked", "message": "External web sources are not permitted for this query."},
    },
    "low_relevance_score": {
        "de": {"title": "Geringe Relevanz", "message": "Ergebnis wegen zu niedriger Relevanzbewertung ausgeschlossen."},
        "en": {"title": "Low relevance", "message": "Result excluded due to low relevance score."},
    },
    "duplicate_version": {
        "de": {"title": "Doppelte Version", "message": "Ältere Version eines Dokuments wurde ausgeschlossen."},
        "en": {"title": "Duplicate version", "message": "Older version of a document was excluded."},
    },
    "context_budget_exceeded": {
        "de": {"title": "Kontextbudget überschritten", "message": "Weitere Quellen konnten nicht berücksichtigt werden."},
        "en": {"title": "Context budget exceeded", "message": "Additional sources could not be included."},
    },
    "personal_data_blocked": {
        "de": {"title": "Persönliche Daten blockiert", "message": "Persönliche Datenquellen sind nicht erlaubt."},
        "en": {"title": "Personal data blocked", "message": "Personal data sources are not permitted."},
    },
}


class OperationalTraceSummary(BaseModel):
    """Summary of operational decisions made during strategy execution."""

    strategy_id: Optional[str] = None
    capability_id: Optional[str] = None
    source_scope_summary: str = ""
    retrieval_count: int = 0
    evidence_card_count: int = 0
    omitted_count: int = 0
    omitted_reasons: list[dict] = Field(default_factory=list)
    validation_passed: bool = True
    warnings: list[str] = Field(default_factory=list)


def get_omitted_reason_message(reason_code: str, language: str = "de") -> dict[str, str]:
    """
    Get localized message for an omitted reason code.

    Args:
        reason_code: The OmittedReason enum value (e.g., "cross_matter_blocked")
        language: ISO language code ("de" or "en")

    Returns:
        Dict with 'title' and 'message' keys in the requested language.
    """
    messages = OMITTED_REASON_MESSAGES.get(reason_code, {})
    lang_messages = messages.get(language, messages.get("en", {}))
    if not lang_messages:
        return {"title": reason_code, "message": f"Result omitted: {reason_code}"}
    return lang_messages


def build_operational_trace(
    strategy_id: Optional[str] = None,
    capability_id: Optional[str] = None,
    retrieved_count: int = 0,
    evidence_card_count: int = 0,
    omitted_results: Optional[list] = None,
    validation_passed: bool = True,
    source_policy_summary: Optional[str] = None,
    language: str = "de",
) -> OperationalTraceSummary:
    """
    Build an OperationalTraceSummary from strategy execution results.

    Args:
        strategy_id: The strategy that was executed
        capability_id: The detected business capability
        retrieved_count: Number of chunks retrieved
        evidence_card_count: Number of evidence cards generated
        omitted_results: List of OmittedResultEntry objects (or dicts with 'reason' key)
        validation_passed: Whether all validations passed
        source_policy_summary: Human-readable summary of active source policy
        language: Language for i18n messages

    Returns:
        OperationalTraceSummary with localized omission messages
    """
    omitted_reasons = []
    warnings = []

    if omitted_results:
        # Group by reason for summarized reporting
        reason_counts: dict[str, int] = {}
        for entry in omitted_results:
            reason = entry.reason if hasattr(entry, "reason") else entry.get("reason", "unknown")
            reason_str = reason.value if hasattr(reason, "value") else str(reason)
            reason_counts[reason_str] = reason_counts.get(reason_str, 0) + 1

        for reason_code, count in reason_counts.items():
            msg = get_omitted_reason_message(reason_code, language)
            omitted_reasons.append({
                "reason_code": reason_code,
                "count": count,
                "title": msg["title"],
                "message": msg["message"],
            })

            # Generate warnings for significant omissions
            if count >= 3:
                warnings.append(
                    f"{msg['title']}: {count} Ergebnisse"
                    if language == "de"
                    else f"{msg['title']}: {count} results"
                )

    return OperationalTraceSummary(
        strategy_id=strategy_id,
        capability_id=capability_id,
        source_scope_summary=source_policy_summary or "",
        retrieval_count=retrieved_count,
        evidence_card_count=evidence_card_count,
        omitted_count=len(omitted_results) if omitted_results else 0,
        omitted_reasons=omitted_reasons,
        validation_passed=validation_passed,
        warnings=warnings,
    )
