"""Unit tests for backend.agent.strategy.context_budgeter.ContextBudgeter."""
from __future__ import annotations

import math

import pytest

from backend.agent.strategy.context_budgeter import (
    BudgetBreakdown,
    BudgetItem,
    ContextBudgeter,
    _default_token_estimator,
)


class TestDefaultTokenizer:
    """Verify the default deterministic ``ceil(len/4)`` heuristic."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("", 0),
            ("a", 1),
            ("abcd", 1),
            ("abcde", 2),
            ("a" * 16, 4),
            ("a" * 17, 5),
        ],
    )
    def test_returns_ceil_len_div_four(self, text: str, expected: int) -> None:
        assert _default_token_estimator(text) == expected

    def test_estimate_matches_ceil_len_div_four(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=1000)
        sample = "Some sample English text of arbitrary length."
        assert bud.estimate_tokens(sample) == math.ceil(len(sample) / 4)


class TestAddAndBuildHappyPath:
    """Items added below the budget are all included in priority order."""

    def test_items_included_in_priority_order(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=10_000)
        # Add in reverse priority on purpose to verify sorting.
        bud.add("evidence_card", "card content one")
        bud.add("user_prompt", "What is the answer?")
        bud.add("answer_contract", "format=prose")

        context, breakdown = bud.build()

        assert isinstance(breakdown, BudgetBreakdown)
        kinds = [item.kind for item in breakdown.included]
        # answer_contract (1) < user_prompt (2) < evidence_card (4)
        assert kinds == ["answer_contract", "user_prompt", "evidence_card"]
        assert breakdown.dropped == []
        assert breakdown.total_tokens_used > 0
        assert "Answer contract" in context
        assert "Evidence cards" in context


class TestDropWithReason:
    """Items that don't fit must surface in ``dropped`` with a reason."""

    def test_low_priority_items_dropped_when_budget_exhausted(self) -> None:
        # Force the budget to fit only the high-priority items.
        bud = ContextBudgeter(total_budget_tokens=20)
        bud.add("answer_contract", "x" * 40)        # 10 tokens
        bud.add("user_prompt", "x" * 40)            # 10 tokens
        bud.add("evidence_card", "x" * 200)         # 50 tokens — won't fit
        bud.add("raw_span", "x" * 200)              # 50 tokens — won't fit

        _, breakdown = bud.build()

        included_kinds = [item.kind for item in breakdown.included]
        dropped_kinds = [d["kind"] for d in breakdown.dropped]
        assert "answer_contract" in included_kinds
        assert "user_prompt" in included_kinds
        assert "evidence_card" in dropped_kinds
        assert "raw_span" in dropped_kinds
        for entry in breakdown.dropped:
            assert entry["reason"] == "budget_exhausted"
            assert "tokens" in entry
        assert breakdown.total_tokens_used <= breakdown.total_budget_tokens


class TestCustomTokenizer:
    """A custom tokenizer must be used in place of the default heuristic."""

    def test_word_count_tokenizer_is_used(self) -> None:
        bud = ContextBudgeter(
            total_budget_tokens=100,
            tokenizer=lambda s: len(s.split()),
        )
        bud.add("user_prompt", "one two three four")
        _, breakdown = bud.build()
        assert breakdown.included[0].tokens == 4
        assert breakdown.total_tokens_used == 4

    def test_estimate_tokens_delegates_to_custom_tokenizer(self) -> None:
        bud = ContextBudgeter(
            total_budget_tokens=100,
            tokenizer=lambda s: len(s.split()),
        )
        assert bud.estimate_tokens("a b c d e") == 5


class TestEmptyBudgeter:
    """A budgeter with no items returns an empty context and breakdown."""

    def test_build_returns_empty(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=100)
        context, breakdown = bud.build()
        assert context == ""
        assert breakdown.included == []
        assert breakdown.dropped == []
        assert breakdown.total_tokens_used == 0
        assert breakdown.total_budget_tokens == 100


class TestPriorityTieBreaker:
    """Same-priority items must keep insertion order (stable sort)."""

    def test_insertion_order_preserved_within_kind(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=10_000)
        bud.add("evidence_card", "card-A")
        bud.add("evidence_card", "card-B")
        bud.add("evidence_card", "card-C")

        _, breakdown = bud.build()

        contents = [item.content for item in breakdown.included]
        assert contents == ["card-A", "card-B", "card-C"]

    def test_explicit_priority_overrides_default(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=10_000)
        bud.add("evidence_card", "low-card")               # priority 4
        bud.add("evidence_card", "high-card", priority=0)  # forced first

        _, breakdown = bud.build()

        assert breakdown.included[0].content == "high-card"
        assert breakdown.included[1].content == "low-card"


class TestAssembledHeaders:
    """The assembled context string must group items by kind with headers."""

    def test_section_headers_appear_in_canonical_order(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=10_000)
        bud.add("conversation", "prior chat snippet")
        bud.add("evidence_card", "card content")
        bud.add("answer_contract", "format=prose")
        bud.add("user_prompt", "the question")

        context, _ = bud.build()

        # Canonical order: answer_contract -> user_prompt ->
        # source_policy -> evidence_card -> raw_span -> conversation
        idx_contract = context.find("# Answer contract")
        idx_prompt = context.find("# User prompt")
        idx_evidence = context.find("# Evidence cards")
        idx_conv = context.find("# Conversation")
        assert -1 < idx_contract < idx_prompt < idx_evidence < idx_conv

    def test_unknown_kind_raises(self) -> None:
        bud = ContextBudgeter(total_budget_tokens=100)
        with pytest.raises(ValueError):
            bud.add("not_a_kind", "anything")  # type: ignore[arg-type]


class TestDeterminism:
    """Identical inputs MUST yield identical outputs (no randomness)."""

    def test_repeat_runs_match(self) -> None:
        def make_budgeter() -> ContextBudgeter:
            bud = ContextBudgeter(total_budget_tokens=80)
            bud.add("answer_contract", "x" * 40)
            bud.add("user_prompt", "x" * 40)
            bud.add("evidence_card", "x" * 80)
            bud.add("evidence_card", "x" * 200)
            return bud

        ctx_a, br_a = make_budgeter().build()
        ctx_b, br_b = make_budgeter().build()
        assert ctx_a == ctx_b
        assert br_a.model_dump() == br_b.model_dump()


class TestBudgetItemModel:
    """Quick sanity check on the Pydantic v2 ``BudgetItem`` model."""

    def test_construct_and_dump(self) -> None:
        item = BudgetItem(
            kind="evidence_card",
            content="hello",
            priority=4,
            tokens=2,
            metadata={"src": "x"},
        )
        dumped = item.model_dump()
        assert dumped["kind"] == "evidence_card"
        assert dumped["metadata"] == {"src": "x"}
