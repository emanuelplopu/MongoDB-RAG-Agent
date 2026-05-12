"""JSON report export."""

from __future__ import annotations

import json
from pathlib import Path

from quellex_profiler.schema import ProfilerReport


def to_json_string(report: ProfilerReport, *, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, ensure_ascii=False)


def write_json(report: ProfilerReport, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_json_string(report), encoding="utf-8")
    return out
