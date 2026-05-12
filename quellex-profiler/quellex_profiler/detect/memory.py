"""Total system RAM detection."""

from __future__ import annotations


def detect_ram_gb() -> float:
    """Return total physical RAM in GB, rounded to 1 decimal.

    Falls back to 0.0 if psutil is unavailable or access fails.
    """
    try:
        import psutil  # type: ignore

        total = psutil.virtual_memory().total
        return round(total / (1024 ** 3), 1)
    except Exception:
        return 0.0
