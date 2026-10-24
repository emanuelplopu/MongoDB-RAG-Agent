"""Tests for the percent label shared by the ingestion progress messages."""

from src.ingestion.progress import progress_percent, progress_percent_label


def test_label_has_one_decimal() -> None:
    assert progress_percent_label(17735, 361819) == "4.9%"
    assert progress_percent_label(0, 361819) == "0.0%"
    assert progress_percent_label(361819, 361819) == "100.0%"


def test_label_survives_unknown_or_overrun_totals() -> None:
    assert progress_percent_label(5, 0) == "0.0%"
    assert progress_percent_label(12, 10) == "100.0%"
    assert progress_percent(-1, 10) == 0.0
