"""Run telemetry for benchmarks: Ollama server log, backend log, containers, MongoDB, isolation.

Everything here is read-only towards the stack except :class:`MongoProfiler`, which
switches MongoDB profiling to level 1 for the benchmark database and back to 0.

* Ollama server log (``%LOCALAPPDATA%\\Ollama\\server.log``): per request status and
  duration, prefill and decode tokens and time, context size and use, prompt
  truncation, model loads and evictions, aborts, errors. Read by byte offset so a
  question's slice is exactly what Ollama wrote while it ran.
* Backend container log: WARNING/ERROR/CRITICAL lines, tracebacks, exception types.
* Container state: restarts, OOM kills, start time changes.
* MongoDB: operations slower than a threshold (``system.profile``).
* Isolation: share of an answer's sources from another case (cross-talk under load).
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ─── Ollama ──────────────────────────────────────────────────────────────────


def default_ollama_log() -> Optional[Path]:
    """Path of the Ollama server log on Windows, if it exists."""
    base = os.environ.get("LOCALAPPDATA")
    path = Path(base) / "Ollama" / "server.log" if base else None
    return path if path and path.is_file() else None


class OllamaLogTail:
    """Reads what the Ollama server log gained since a byte offset; detects rotation (server restart)."""

    def __init__(self, path: Optional[Path]):  # noqa: D107
        self.path = path

    def offset(self) -> int:
        """Current end of the log (0 if there is no log)."""
        try:
            return self.path.stat().st_size if self.path else 0
        except OSError:
            return 0

    def read_from(self, offset: int) -> tuple[str, bool]:
        """Text written since ``offset`` and whether the log was rotated in between."""
        if not self.path:
            return "", False
        try:
            size = self.path.stat().st_size
            rotated = size < offset
            with self.path.open("rb") as fh:
                fh.seek(0 if rotated else offset)
                return fh.read().decode("utf-8", errors="replace"), rotated
        except OSError as exc:
            logger.warning("Could not read the Ollama log %s: %s", self.path, exc)
            return "", False


def ollama_get(url: str, path: str, payload: Optional[dict[str, Any]] = None, timeout: float = 5.0) -> Optional[dict[str, Any]]:
    """GET (or POST with ``payload``) an Ollama API path; None if Ollama is unreachable."""
    try:
        with httpx.Client(timeout=timeout) as http:
            r = http.post(url.rstrip("/") + path, json=payload) if payload is not None else http.get(url.rstrip("/") + path)
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def ollama_ps(url: str) -> list[dict[str, Any]]:
    """Loaded models with total and GPU size (GiB) and context length; ``offloaded`` when part runs on the CPU."""
    data = ollama_get(url, "/api/ps") or {}
    out = []
    for m in data.get("models") or []:
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        out.append({"name": m.get("name"), "size_gib": round(size / 2**30, 2), "vram_gib": round(vram / 2**30, 2),
                    "context_length": m.get("context_length"), "offloaded": bool(size and vram < size * 0.99)})
    return out


def ollama_model_blobs(url: str) -> dict[str, str]:
    """Map of blob id prefix (``sha256-`` + 12 hex) to model name, for naming loads in the server log."""
    out: dict[str, str] = {}
    for m in (ollama_get(url, "/api/tags") or {}).get("models") or []:
        show = ollama_get(url, "/api/show", {"model": m.get("name")}, timeout=15) or {}
        hit = re.search(r"sha256-[0-9a-f]{12}", show.get("modelfile") or "")
        if hit:
            out[hit.group(0)] = m.get("name")
    return out


_GIN = re.compile(r"\[GIN\][^|]*\|\s*(\d{3})\s*\|\s*([0-9][0-9.hmsµun]*)\s*\|[^|]*\|\s*(\w+)\s+\"([^\"]+)\"")
_PREFILL = re.compile(r"prompt eval time =\s*([\d.]+) ms /\s*(\d+) tokens")
_DECODE = re.compile(r"(?<!prompt )eval time =\s*([\d.]+) ms /\s*(\d+) tokens")
_STOP = re.compile(r"stop processing: n_tokens = (\d+), truncated = (\d+)")
_NEW_PROMPT = re.compile(r"n_ctx_slot = (\d+).*?task\.n_tokens = (\d+)")
_START = re.compile(r'msg="starting llama-server".*?(sha256-[0-9a-f]{12}).*? -c (\d+)')
_STARTED = re.compile(r'msg="llama-server started in ([\d.]+) seconds"')
_LEVEL = re.compile(r'level=(ERROR|WARN)\S*.*?msg="([^"]*)"')
_ABORT = re.compile(r"client closing|context canceled|cancel(?:led)? task|aborting", re.IGNORECASE)


def go_duration_s(text: str) -> float:
    """Seconds from a Go duration string (``1m2.5s``, ``120.3ms``, ``52µs``)."""
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(h|ms|µs|us|ns|m|s)", text):
        total += float(value) * {"h": 3600, "m": 60, "s": 1, "ms": 1e-3, "µs": 1e-6, "us": 1e-6, "ns": 1e-9}[unit]
    return round(total, 3)


def parse_ollama_log(text: str, blobs: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """Summarise an Ollama server log slice."""
    blobs = blobs or {}
    statuses: collections.Counter[str] = collections.Counter()
    durations: list[float] = []
    prefill_ms = prefill_tok = decode_ms = decode_tok = 0.0
    max_used = max_prompt = n_ctx = truncated = evictions = aborts = 0
    loads: list[dict[str, Any]] = []
    load_s = 0.0
    errors: list[str] = []
    warnings = 0
    for line in text.splitlines():
        if m := _GIN.search(line):
            if m.group(3) == "POST" and m.group(4).startswith(("/api/generate", "/api/chat")):
                statuses[m.group(1)] += 1
                durations.append(go_duration_s(m.group(2)))
            elif not m.group(1).startswith("2"):
                statuses[m.group(1)] += 1
        elif m := _PREFILL.search(line):
            prefill_ms += float(m.group(1))
            prefill_tok += int(m.group(2))
        elif m := _DECODE.search(line):
            decode_ms += float(m.group(1))
            decode_tok += int(m.group(2))
        elif m := _STOP.search(line):
            max_used = max(max_used, int(m.group(1)))
            truncated += int(m.group(2) != "0")
        elif m := _NEW_PROMPT.search(line):
            n_ctx = max(n_ctx, int(m.group(1)))
            max_prompt = max(max_prompt, int(m.group(2)))
        elif m := _START.search(line):
            loads.append({"model": blobs.get(m.group(1), m.group(1)), "ctx": int(m.group(2))})
        elif m := _STARTED.search(line):
            load_s += float(m.group(1))
        elif "evicting" in line:
            evictions += 1
        elif m := _LEVEL.search(line):
            if m.group(1) == "ERROR":
                errors.append(m.group(2)[:200])
            else:
                warnings += 1
        if _ABORT.search(line):
            aborts += 1
    return {
        "requests": sum(statuses.values()), "by_status": dict(statuses), "server_s": round(sum(durations), 2),
        "max_request_s": round(max(durations), 2) if durations else 0.0,
        "prefill_tokens": int(prefill_tok), "prefill_s": round(prefill_ms / 1000, 2),
        "decode_tokens": int(decode_tok), "decode_s": round(decode_ms / 1000, 2),
        "max_prompt_tokens": max_prompt, "n_ctx": n_ctx, "max_context_used": max_used, "truncated_prompts": truncated,
        "loads": loads, "load_s": round(load_s, 2), "evictions": evictions, "aborts": aborts,
        "errors": errors[:10], "error_count": len(errors), "warnings": warnings,
    }


# ─── backend log ─────────────────────────────────────────────────────────────

_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} [\d:,]+ - (\S+) - (WARNING|ERROR|CRITICAL) - (.*)$")
_EXCEPTION = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Timeout|Interrupt)):")
_REQ = re.compile(r"\[req=\w+\]\s*")


def docker_logs(container: str, since: str, until: Optional[str] = None) -> str:
    """stdout+stderr of a container between two RFC 3339 times ('' if docker is unavailable)."""
    cmd = ["docker", "logs", "--since", since] + (["--until", until] if until else []) + [container]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return out.stdout + "\n" + out.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("docker logs %s failed: %s", container, exc)
        return ""


def parse_backend_log(text: str) -> dict[str, Any]:
    """Count warnings, errors, tracebacks and exception types; keep distinct messages."""
    levels: collections.Counter[str] = collections.Counter()
    loggers: collections.Counter[str] = collections.Counter()
    messages: dict[str, int] = {}
    exceptions: collections.Counter[str] = collections.Counter()
    for line in text.splitlines():
        if m := _LOG_LINE.match(line):
            levels[m.group(2)] += 1
            loggers[m.group(1)] += 1
            key = f"{m.group(2)} {m.group(1)}: {_REQ.sub('', m.group(3))[:240]}"
            messages[key] = messages.get(key, 0) + 1
        elif m := _EXCEPTION.match(line.strip()):
            exceptions[m.group(1)] += 1
    return {
        "warnings": levels.get("WARNING", 0), "errors": levels.get("ERROR", 0) + levels.get("CRITICAL", 0),
        "tracebacks": text.count("Traceback (most recent call last)"), "exceptions": dict(exceptions),
        "by_logger": dict(loggers.most_common(10)),
        "messages": [{"message": k, "count": v} for k, v in sorted(messages.items(), key=lambda kv: -kv[1])[:15]],
    }


# ─── containers ──────────────────────────────────────────────────────────────


def container_states(names: list[str]) -> dict[str, dict[str, Any]]:
    """Restart count, start time, OOM kill flag and status per container."""
    fmt = "{{.Name}}|{{.RestartCount}}|{{.State.StartedAt}}|{{.State.OOMKilled}}|{{.State.Status}}"
    try:
        out = subprocess.run(["docker", "inspect", "-f", fmt, *names], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("docker inspect failed: %s", exc)
        return {}
    states = {}
    for line in out.strip().splitlines():
        name, restarts, started, oom, status = line.split("|")
        states[name.lstrip("/")] = {"restarts": int(restarts), "started_at": started, "oom_killed": oom == "true", "status": status}
    return states


def container_changes(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    """Human-readable restarts, OOM kills and status changes between two snapshots."""
    out = []
    for name, a in after.items():
        b = before.get(name)
        if not b:
            continue
        if a["started_at"] != b["started_at"] or a["restarts"] != b["restarts"]:
            out.append(f"{name} restarted (restarts {b['restarts']} -> {a['restarts']})")
        if a["oom_killed"] and not b["oom_killed"]:
            out.append(f"{name} was OOM-killed")
        if a["status"] != "running":
            out.append(f"{name} is {a['status']}")
    return out


# ─── MongoDB ─────────────────────────────────────────────────────────────────


class MongoProfiler:
    """Profile operations slower than ``slow_ms`` on one database while the benchmark runs."""

    def __init__(self, db: Any, slow_ms: int = 100):  # noqa: D107
        self.db, self.slow_ms = db, slow_ms
        self.error: Optional[str] = None
        self.result: dict[str, Any] = {}

    def __enter__(self) -> "MongoProfiler":
        from pymongo.errors import PyMongoError

        self.t0 = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            self.db.command("profile", 1, slowms=self.slow_ms)
        except PyMongoError as exc:
            self.error = str(exc)[:300]
        return self

    def __exit__(self, *exc: object) -> None:
        from pymongo.errors import PyMongoError

        if self.error:
            self.result = {"error": self.error}
            return
        try:
            ops = list(self.db.system.profile.find({"ts": {"$gte": self.t0}},
                                                   {"op": 1, "ns": 1, "millis": 1, "planSummary": 1, "command": 1, "ts": 1})
                       .sort("millis", -1).limit(500))
            by_kind: collections.Counter[str] = collections.Counter()
            top = []
            for o in ops:
                cmd = o.get("command") or {}
                stage = ""
                if isinstance(cmd.get("pipeline"), list) and cmd["pipeline"]:
                    stage = next(iter(cmd["pipeline"][0]), "")
                kind = f"{o.get('op')} {str(o.get('ns', '')).split('.', 1)[-1]} {stage}".strip()
                by_kind[kind] += 1
                if len(top) < 10:
                    top.append({"kind": kind, "millis": o.get("millis"), "plan": o.get("planSummary"), "ts": str(o.get("ts"))})
            self.result = {"slow_ms": self.slow_ms, "slow_ops": len(ops), "by_kind": dict(by_kind.most_common(10)), "slowest": top}
        except PyMongoError as exc:
            self.result = {"error": str(exc)[:300]}
        finally:
            try:
                self.db.command("profile", 0)
            except PyMongoError as exc:
                logger.warning("Could not switch MongoDB profiling off: %s", exc)


# ─── isolation ───────────────────────────────────────────────────────────────


def case_folder(path: str) -> Optional[str]:
    """``<firm>/Akten/<case folder>/`` prefix of a corpus path, if any."""
    parts = path.split("/")
    if "Akten" in parts:
        i = parts.index("Akten")
        if i + 1 < len(parts):
            return "/".join(parts[: i + 2]) + "/"
    return None


def foreign_source_share(gold_documents: list[str], sources: list[str]) -> Optional[float]:
    """Share of an answer's sources from another case folder (None if the question has no single case)."""
    folders = {f for f in (case_folder(d) for d in gold_documents) if f}
    if len(folders) != 1 or not sources:
        return None
    folder = next(iter(folders))
    return round(sum(1 for s in sources if not s.startswith(folder)) / len(sources), 3)


def jsonl_append(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON line (UTF-8)."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
