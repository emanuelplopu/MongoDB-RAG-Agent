"""Priority-ordered token budget allocator for LLM context assembly.

This module implements the ``ContextBudgeter`` referenced in
Blueprint 08 §8.  Its job is to decide *what* gets fed into a synthesis
prompt when the raw available material would exceed the active context
budget for the chosen model.

Why this exists
---------------

Local LLMs (e.g. a 26B model running on a laptop) commonly accept a
context window of 8k–32k tokens, but blindly stuffing 20k tokens of
evidence into a prompt for a question whose answer truly only needs
~4k tokens of evidence wastes inference time and blurs the model's
focus.  The budgeter applies a deterministic, priority-ordered packing
algorithm that always keeps the highest-signal items (the answer
contract, the user prompt, the source policy summary) and only sheds
lower-priority items (bulk evidence cards, raw spans, long
conversational history, few-shot examples) once the budget is full.

Tokenizer choice
----------------

By default the budgeter uses a deterministic heuristic of
``ceil(len(text) / 4)`` tokens.  For English and German this hovers
within roughly ±25% of true BPE counts and is fully deterministic and
dependency-free, which is what the unit tests rely on.  Integrating a
real tokenizer (e.g. ``tiktoken`` for OpenAI-compatible models or a
model-specific ``transformers`` tokenizer for local models) is a
follow-up — the constructor accepts a ``tokenizer`` callable to make
that swap drop-in.

Determinism
-----------

For identical inputs the budgeter MUST produce identical outputs.
There is no randomness, no time-based behaviour, and ties on priority
are broken by insertion order (stable sort).
"""
from __future__ import annotations

import math
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BudgetItem",
    "BudgetBreakdown",
    "ContextBudgeter",
    "BudgetItemKind",
]

BudgetItemKind = Literal[
    "answer_contract",
    "user_prompt",
    "source_policy",
    "evidence_card",
    "raw_span",
    "conversation",
    "example",
]


# Default priority per kind. Lower number == higher priority == included first.
# Mirrors Blueprint 08 §8: answer contract > user prompt > source policy
# > evidence cards > raw spans > conversation > examples.
_DEFAULT_PRIORITY: dict[str, int] = {
    "answer_contract": 1,
    "user_prompt": 2,
    "source_policy": 3,
    "evidence_card": 4,
    "raw_span": 5,
    "conversation": 6,
    "example": 7,
}

# Human-readable section header per kind, used when the budgeter
# assembles its final concatenated context string.
_KIND_HEADERS: dict[str, str] = {
    "answer_contract": "Answer contract",
    "user_prompt": "User prompt",
    "source_policy": "Source policy",
    "evidence_card": "Evidence cards",
    "raw_span": "Raw spans",
    "conversation": "Conversation",
    "example": "Examples",
}

# Conservative default budget. The brief calls for 6000 tokens when no
# explicit cap is supplied by the strategy spec.
DEFAULT_TOTAL_BUDGET_TOKENS: int = 6000


def _default_token_estimator(text: str) -> int:
    """Estimate token count for *text* using a deterministic heuristic.

    The heuristic is ``ceil(len(text) / 4)`` which approximates BPE
    token counts for English and German prose.  It is intentionally
    coarse — see the module docstring for rationale — but it never
    returns a negative value and never depends on external state.

    Args:
        text: The text to estimate tokens for.

    Returns:
        A non-negative integer token count estimate.
    """
    if not text:
        return 0
    return math.ceil(len(text) / 4)


class BudgetItem(BaseModel):
    """A single candidate item considered by the :class:`ContextBudgeter`.

    Attributes:
        kind: The semantic role of the item. Drives the default
            priority and the section header used during assembly.
        content: The full string content of the item.
        priority: Effective priority. Lower means higher priority.
        tokens: The pre-computed token cost for ``content``.
        metadata: Free-form metadata preserved for telemetry.
    """

    model_config = ConfigDict(extra="forbid")

    kind: BudgetItemKind
    content: str
    priority: int
    tokens: int
    metadata: dict = Field(default_factory=dict)


class BudgetBreakdown(BaseModel):
    """Summary of a budget allocation, suitable for telemetry.

    Attributes:
        total_budget_tokens: The cap the budgeter was constructed with.
        included: Items that fit within the budget, in inclusion order.
        dropped: Items that did not fit. Each entry carries
            ``{"kind", "tokens", "reason"}``.
        total_tokens_used: Sum of ``tokens`` across all included items.
    """

    model_config = ConfigDict(extra="forbid")

    total_budget_tokens: int
    included: list[BudgetItem] = Field(default_factory=list)
    dropped: list[dict] = Field(default_factory=list)
    total_tokens_used: int = 0


class ContextBudgeter:
    """Greedy, priority-ordered token allocator for LLM prompts.

    Items are added with :meth:`add`; on :meth:`build` the budgeter
    sorts items by ``(priority, insertion_order)`` (stable), then
    greedily includes items until the next item would push the running
    total over ``total_budget_tokens``.  Items that cannot fit are
    recorded in ``BudgetBreakdown.dropped`` with reason
    ``"budget_exhausted"``.

    The budgeter is fully deterministic — the same inputs always
    produce the same output.  No randomness, no time-based behaviour.

    Example:
        >>> bud = ContextBudgeter(total_budget_tokens=200)
        >>> bud.add("user_prompt", "What is the answer?")
        >>> bud.add("evidence_card", "Card 1 — long content...")
        >>> context, breakdown = bud.build()

    Note:
        The default tokenizer is a coarse ``ceil(len/4)`` heuristic.
        Wiring a real tokenizer (``tiktoken`` or a local model
        tokenizer) is a follow-up and is enabled by passing a
        ``tokenizer`` callable to the constructor.
    """

    def __init__(
        self,
        *,
        total_budget_tokens: int,
        tokenizer: Optional[Callable[[str], int]] = None,
    ) -> None:
        """Construct a new budgeter.

        Args:
            total_budget_tokens: Maximum aggregate tokens permitted in
                the assembled context. Must be non-negative.
            tokenizer: Optional callable mapping text to integer token
                counts. Defaults to :func:`_default_token_estimator`.

        Raises:
            ValueError: If ``total_budget_tokens`` is negative.
        """
        if total_budget_tokens < 0:
            raise ValueError(
                f"total_budget_tokens must be non-negative, got "
                f"{total_budget_tokens}"
            )
        self.total_budget_tokens = int(total_budget_tokens)
        self._tokenizer: Callable[[str], int] = (
            tokenizer if tokenizer is not None else _default_token_estimator
        )
        self._items: list[BudgetItem] = []

    # ── public API ────────────────────────────────────────────────────────

    def estimate_tokens(self, text: str) -> int:
        """Return the active tokenizer's estimate for *text*.

        Args:
            text: The text to estimate tokens for.

        Returns:
            A non-negative integer token count.
        """
        if not text:
            return 0
        return int(self._tokenizer(text))

    def add(
        self,
        kind: str,
        content: str,
        *,
        priority: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Register a candidate item with the budgeter.

        Args:
            kind: One of the supported :data:`BudgetItemKind` literals.
            content: The full string content of the item. Empty
                strings are still accepted but contribute zero tokens.
            priority: Optional explicit priority override. Lower means
                higher priority. When ``None``, falls back to the
                kind's default priority.
            metadata: Optional free-form metadata preserved for
                downstream telemetry.

        Raises:
            ValueError: If ``kind`` is not a recognised value.
        """
        if kind not in _DEFAULT_PRIORITY:
            raise ValueError(
                f"Unknown budget item kind '{kind}'. "
                f"Expected one of {sorted(_DEFAULT_PRIORITY)}."
            )
        effective_priority = (
            priority if priority is not None else _DEFAULT_PRIORITY[kind]
        )
        tokens = self.estimate_tokens(content)
        self._items.append(
            BudgetItem(
                kind=kind,  # type: ignore[arg-type]
                content=content,
                priority=int(effective_priority),
                tokens=tokens,
                metadata=dict(metadata) if metadata else {},
            )
        )

    def build(self) -> tuple[str, BudgetBreakdown]:
        """Assemble the context string and the breakdown report.

        The algorithm:

        1. Sort items by ``(priority, insertion_order)`` using a
           stable sort — same-priority items preserve the order they
           were added.
        2. Greedily include items while ``running_total + item.tokens
           <= total_budget_tokens``.
        3. Record skipped items in ``dropped`` with reason
           ``"budget_exhausted"``.
        4. Group included items by ``kind`` (in the canonical kind
           order) and emit a section per kind with a markdown-style
           header and the item contents joined by blank lines.

        Returns:
            A tuple of ``(assembled_context, breakdown)``. The
            assembled string is empty when no items were included.
        """
        # Stable sort: Python's sort is stable, so we get
        # (priority asc, insertion order asc) by sorting on priority
        # alone.
        ordered = sorted(
            range(len(self._items)),
            key=lambda i: self._items[i].priority,
        )

        included_indices: list[int] = []
        dropped: list[dict] = []
        running_total = 0
        budget_exhausted = False

        for idx in ordered:
            item = self._items[idx]
            if (
                not budget_exhausted
                and running_total + item.tokens <= self.total_budget_tokens
            ):
                included_indices.append(idx)
                running_total += item.tokens
            else:
                # Once we have to drop one item we still try to fit
                # later, smaller items in priority order. The greedy
                # rule of the brief is "drop remaining with reason
                # budget_exhausted" — but we must remain deterministic
                # so we mark every non-fitting item with the same
                # reason regardless of size.
                budget_exhausted = True
                dropped.append(
                    {
                        "kind": item.kind,
                        "tokens": item.tokens,
                        "reason": "budget_exhausted",
                    }
                )

        included_items = [self._items[i] for i in included_indices]

        breakdown = BudgetBreakdown(
            total_budget_tokens=self.total_budget_tokens,
            included=included_items,
            dropped=dropped,
            total_tokens_used=running_total,
        )

        assembled = self._assemble(included_items)
        return assembled, breakdown

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _assemble(included: list[BudgetItem]) -> str:
        """Concatenate included items grouped by kind with section headers.

        Sections appear in the canonical kind order
        (answer_contract → user_prompt → source_policy → evidence_card
        → raw_span → conversation → example), and within a section
        items keep insertion order.

        Args:
            included: The items that fit inside the budget.

        Returns:
            The fully assembled context string. Empty when no items
            were included.
        """
        if not included:
            return ""

        # Bucket included items by kind, preserving insertion order
        # within each bucket.
        buckets: dict[str, list[BudgetItem]] = {
            kind: [] for kind in _DEFAULT_PRIORITY
        }
        for item in included:
            buckets[item.kind].append(item)

        parts: list[str] = []
        for kind in _DEFAULT_PRIORITY:
            bucket = buckets.get(kind, [])
            if not bucket:
                continue
            header = _KIND_HEADERS[kind]
            section_body = "\n\n".join(item.content for item in bucket)
            parts.append(f"# {header}\n{section_body}")

        # Two blank lines between sections for readability and to
        # match the brief's "section-headered" example output.
        return "\n\n".join(parts)
