"""Progress labels shared by the ingestion pipeline, the API and the ingestion worker.

Every "file X of Y" message carries the same percent string so the live phase message,
the worker's status and the pipeline log line agree with the progress bar.
"""


def progress_percent(current: int, total: int) -> float:
    """Return the completed share in percent, clamped to 0..100 (0.0 when ``total`` is unknown)."""
    if total <= 0:
        return 0.0
    return min(100.0, max(0.0, current / total * 100.0))


def progress_percent_label(current: int, total: int) -> str:
    """Return ``"4.9%"`` for 17 735 of 361 819.

    One decimal, so a large corpus visibly moves between status refreshes.
    """
    return f"{progress_percent(current, total):.1f}%"
