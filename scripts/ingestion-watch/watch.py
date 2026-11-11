"""Ingestion watch: one pass over everything that keeps the live ingestion at full speed.

Checks (and prints one ``ANOMALY: ...`` line per problem, ``OK`` lines otherwise):

* Docker is up and every stack container runs and is healthy.
* The llama.cpp ingestion embedder is not in its degraded state. The server degrades after a
  day or two of uptime (one CPU core pinned, 8-10 GB RSS, seconds per embedding instead of
  milliseconds); then every file's embedding step takes an hour, the conversion slots sit idle
  and the machine runs at half load. A healthy but busy embedder also answers late (its four
  slots queue the pipeline's batches), so the rule is: three one-sentence probes 30 s apart all
  slower than ``SLOW_SECONDS`` **and** the container pinned at about one CPU core
  (``DEGRADED_CPU_CORES``) **and** the GPU below ``BUSY_GPU_UTIL_PCT`` in the host agent's last
  samples (a busy embedder keeps the GPU working; the degraded one spins one CPU core and leaves
  the GPU idle), the container up for more than five minutes, no restart in the last
  ``RESTART_COOLDOWN_MIN`` → ``docker restart`` of the container. Seen 2026-11-01 three times:
  degraded = 1.0 core, GPU 6-13 %, memory 6-10 GB (not a reliable sign), 60-115 min after a start.
* The ingestion job is running and finished files in the last hour.
* A hung API: the backend container unhealthy for ``HUNG_FAILING_STREAK`` health checks, no file
  started or ended for ``HUNG_IDLE_MIN`` minutes while the job says "processing", the API
  container pinned near one core (its event loop spinning) → the file ``activity_current`` names
  is recorded in ``failed_documents`` with error type ``hang`` (so the resumed job skips it) and
  the backend container is restarted (at most once per ``BACKEND_RESTART_COOLDOWN_MIN``). Seen
  2026-11-02 21:19 UTC and 2026-11-03 02:15 UTC: the main thread spun for hours after converting
  one .pptx / one file; nothing in flight progresses, so the restart loses nothing.
* The host agent service runs, its samples are fresh, and the Windows power plan matches
  what the backend asks for (``host_control``).
* The free space of the system drive.

State (last restart) and a log line per run go to ``C:\\RecallHub-Verify\\logs``. No API key is
needed: the job and the control documents are read from MongoDB, the embedder through its
host port, containers through the Docker CLI.

    uv run python scripts/ingestion-watch/watch.py [--no-restart] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

MONGO = "mongodb://127.0.0.1:11017/?directConnection=true"
TENANT_DB = "rag_diejuristin"
TELEMETRY_DB = "recallhub_telemetry"
EMBEDDER_URL = "http://127.0.0.1:11090/v1/embeddings"
EMBEDDER_CONTAINER = "rag-embedder-gpu"
CONTAINERS = ["rag-mongodb", "rag-backend-quellex", "rag-ingestion-worker", "rag-frontend-quellex",
              "rag-gateway-quellex", "rag-embedder-gpu", "rag-embedder-query"]
SLOW_SECONDS = 5.0
PROBES = 3
PROBE_GAP_S = 30
DEGRADED_CPU_CORES = 0.9
BUSY_GPU_UTIL_PCT = 40.0
WARMUP_MIN = 5
RESTART_COOLDOWN_MIN = 30
BACKEND_CONTAINER = "rag-backend-quellex"
HUNG_FAILING_STREAK = 6
HUNG_IDLE_MIN = 10   # the dead health endpoint is the primary signal; a busy API still answers /health
HUNG_MAX_CORES = 3.0   # a spinning main thread plus helper threads showed 1.0-2.0 cores; healthy processing uses 10-16
BACKEND_RESTART_COOLDOWN_MIN = 30
LOG_DIR = Path(r"C:\RecallHub-Verify\logs")
STATE_FILE = LOG_DIR / "ingestion-watch.json"
LOG_FILE = LOG_DIR / "ingestion-watch.log"

anomalies: list[str] = []
oks: list[str] = []


def ok(msg: str) -> None:
    oks.append(msg)


def bad(msg: str) -> None:
    anomalies.append(msg)


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001 - a failing probe is itself the finding
        return 1, str(exc)


def embed_probe() -> float | None:
    body = json.dumps({"model": "embeddinggemma-2", "input": ["Testsatz fuer das Embedding"]}).encode("utf-8")
    req = urllib.request.Request(EMBEDDER_URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
        return time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001
        bad(f"embedder probe failed: {exc}")
        return None


def container_load(name: str) -> tuple[float | None, float | None]:
    """(CPU cores, memory GiB) of a container right now, from one ``docker stats`` sample."""
    code, out = run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}	{{.MemUsage}}", name], timeout=30)
    if code != 0 or "	" not in out:
        return None, None
    cpu_text, mem_text = out.split("	", 1)
    cpu = re.match(r"\s*([\d.]+)%", cpu_text)
    m = re.match(r"\s*([\d.]+)\s*([KMG]i?B)", mem_text)
    cores = float(cpu.group(1)) / 100 if cpu else None
    if not m:
        return cores, None
    value, unit = float(m.group(1)), m.group(2)
    return cores, (value / 1024 if unit.startswith("M") else value * 1024 if unit.startswith("K") else value)


def container_up_minutes(name: str) -> float | None:
    code, out = run(["docker", "inspect", name, "--format", "{{.State.StartedAt}}"], timeout=30)
    if code != 0:
        return None
    try:
        started = datetime.fromisoformat(out.strip().replace("Z", "+00:00")[:26] + "+00:00") if "." in out else datetime.fromisoformat(out.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - started).total_seconds() / 60


def sustained_load(minutes: int = 10) -> tuple[float | None, float | None]:
    """(embedder CPU cores, GPU utilisation %) averaged over the host agent's last ``minutes`` of samples.

    The degraded embedder spins one CPU core for an hour while the GPU idles; a momentary probe
    can still land between two batches and look fine, the 10-minute average cannot.
    """
    try:
        from pymongo import MongoClient
        hs = MongoClient(MONGO, serverSelectionTimeoutMS=8000, tz_aware=True)[TELEMETRY_DB]["host_samples"]
        rows = list(hs.aggregate([
            {"$match": {"ts": {"$gte": datetime.now(timezone.utc) - timedelta(minutes=minutes)}}},
            {"$group": {"_id": None, "gpu": {"$avg": "$gpu.util_pct"}, "emb": {"$avg": {"$sum": {"$map": {
                "input": {"$filter": {"input": "$containers", "as": "x", "cond": {"$eq": ["$$x.name", EMBEDDER_CONTAINER]}}},
                "as": "x", "in": "$$x.cpu_cores"}}}}}},
        ]))
        if not rows:
            return None, None
        r = rows[0]
        return (None if r.get("emb") is None else float(r["emb"])), (None if r.get("gpu") is None else float(r["gpu"]))
    except Exception:  # noqa: BLE001
        return None, None


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")


def check_docker(no_restart: bool, state: dict) -> None:
    code, out = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], timeout=30)
    if code != 0:
        bad(f"Docker not reachable: {out[:200]}")
        return
    status = dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)
    for name in CONTAINERS:
        s = status.get(name)
        if s is None:
            bad(f"container {name} is not running")
        elif "unhealthy" in s:
            bad(f"container {name} is unhealthy ({s})")
        else:
            ok(f"{name}: {s}")

    # Embedder: degraded, or just busy? The sustained 10-minute picture decides; probes confirm.
    sustained_cores, gpu = sustained_load()
    up = container_up_minutes(EMBEDDER_CONTAINER)
    spinning = sustained_cores is not None and sustained_cores >= DEGRADED_CPU_CORES and (gpu is None or gpu < BUSY_GPU_UTIL_PCT)
    latencies: list[float] = []
    for i in range(PROBES):
        lat = embed_probe()
        if lat is None:
            return
        latencies.append(lat)
        if lat <= SLOW_SECONDS and not spinning:
            break
        if i < PROBES - 1:
            time.sleep(PROBE_GAP_S)
    shown = ", ".join(f"{x:.1f} s" for x in latencies)
    cores, rss = container_load(EMBEDDER_CONTAINER)
    detail = (f"probes {shown}, CPU {sustained_cores if sustained_cores is None else round(sustained_cores, 2)} cores over 10 min "
              f"({cores if cores is None else round(cores, 2)} now), RSS {rss if rss is None else round(rss, 1)} GiB, "
              f"GPU {gpu if gpu is None else round(gpu)} %, up {up if up is None else round(up)} min")
    if not spinning and latencies[-1] <= SLOW_SECONDS:
        ok(f"embedder answers in {shown}" + (" (busy, then fine)" if len(latencies) > 1 else ""))
        return
    if not spinning:
        bad(f"embedder slow but not spinning a CPU core, not restarting ({detail})")
        return
    if up is not None and up < 20 and max(latencies) <= SLOW_SECONDS:
        ok(f"embedder warming up, probes fine ({detail})")
        return
    last = state.get("last_embedder_restart")
    last_dt = datetime.fromisoformat(last) if last else None
    cooled = last_dt is None or datetime.now(timezone.utc) - last_dt > timedelta(minutes=RESTART_COOLDOWN_MIN)
    if up is not None and up < WARMUP_MIN:
        bad(f"embedder slow right after a start, waiting ({detail})")
    elif no_restart:
        bad(f"embedder degraded, restart suppressed by --no-restart ({detail})")
    elif not cooled:
        bad(f"embedder degraded but restarted at {last} already; not again yet ({detail})")
    else:
        code, out = run(["docker", "restart", EMBEDDER_CONTAINER], timeout=180)
        state["last_embedder_restart"] = datetime.now(timezone.utc).isoformat()
        state["restarts"] = int(state.get("restarts", 0)) + 1
        bad(f"embedder degraded: RESTARTED {EMBEDDER_CONTAINER} (exit {code}; {detail})")


def check_mongo() -> None:
    try:
        from pymongo import MongoClient
        c = MongoClient(MONGO, serverSelectionTimeoutMS=8000, tz_aware=True)
        now = datetime.now(timezone.utc)
        job = c[TENANT_DB]["ingestion_jobs"].find_one(
            {"status": {"$in": ["running", "paused", "interrupted", "pending"]}},
            projection={"status": 1, "phase": 1, "processed_files": 1, "failed_files": 1, "total_files": 1,
                        "load_period": 1, "concurrency_slots": 1, "resumed_at": 1})
        if not job:
            done = c[TENANT_DB]["ingestion_jobs"].find_one({"status": "completed"}, sort=[("completed_at", -1)],
                                                           projection={"completed_at": 1, "processed_files": 1})
            bad(f"no ingestion job running (last completed: {done})")
        else:
            ev = c[TELEMETRY_DB]["activity_events"]
            ended = ev.count_documents({"meta.kind": "file.end", "ts": {"$gte": now - timedelta(hours=1)}})
            started = ev.count_documents({"meta.kind": "file.start", "ts": {"$gte": now - timedelta(hours=1)}})
            line = (f"job {job.get('status')}/{job.get('phase')}: {job.get('processed_files')} done, {job.get('failed_files')} failed of "
                    f"{job.get('total_files')}; slots {job.get('concurrency_slots')} ({job.get('load_period')}); last hour {started} started, {ended} ended")
            if job.get("status") == "running" and job.get("phase") == "processing" and ended == 0 and started == 0:
                bad("job running but no file started or ended in the last hour: " + line)
            elif job.get("status") in ("paused", "interrupted"):
                bad("job is " + str(job.get("status")) + ": " + line)
            else:
                ok(line)
        cfg = c[TENANT_DB]["ingestion_config"].find_one({"_id": "performance_config"}) or {}
        oh = cfg.get("office_hours") or {}
        ok(f"load_profile {cfg.get('load_profile')}, max {cfg.get('max_concurrent_files')}, office {oh.get('max_concurrent_files')}/lite "
           f"{oh.get('lite_max_concurrent_files')}, cap_cpu_boost {oh.get('cap_cpu_boost')}")
        hs = c[TELEMETRY_DB]["host_samples"].find_one(sort=[("ts", -1)], projection={"ts": 1, "cpu.pct": 1, "power.cpu_pkg_w": 1, "temp.cpu_pkg_c": 1, "temp.gpu_c": 1, "meta.agent_version": 1})
        if not hs or now - hs["ts"] > timedelta(minutes=2):
            bad(f"host agent samples stale (last {hs['ts'] if hs else None})")
        else:
            ok(f"host sample {int((now - hs['ts']).total_seconds())} s old: CPU {hs['cpu']['pct']:.0f} %, {hs['power']['cpu_pkg_w']:.0f} W, "
               f"pkg {hs['temp']['cpu_pkg_c']:.0f} C, GPU {hs['temp'].get('gpu_c')} C, agent {hs['meta'].get('agent_version')}")
        hc = {d["_id"]: d for d in c[TELEMETRY_DB]["host_control"].find({})}
        desired = [d for k, d in hc.items() if k.startswith("power_plan:")]
        status = hc.get("power_plan")
        if status is None or now - status["updated_at"] > timedelta(minutes=3):
            bad(f"power plan agent report stale or missing ({status['updated_at'] if status else None})")
        else:
            want = "capped" if any(d.get("desired") == "capped" and now - d["updated_at"] < timedelta(minutes=10) for d in desired) else "normal"
            summary = ", ".join(f"{d.get('tenant', '?')}:{d.get('desired', '?')}" for d in desired) or "no desires"
            if status.get("state") != want:
                bad(f"power plan is {status.get('state')} but desired {want} ({summary})")
            else:
                ok(f"power plan {status.get('state')} as desired ({summary})")
    except Exception as exc:  # noqa: BLE001
        bad(f"MongoDB checks failed: {exc}")


def check_hung_api(no_restart: bool, state: dict) -> None:
    """Restart a hung API (event loop spinning, health gone, no file progress) after recording the file it hung on."""
    code, out = run(["docker", "inspect", BACKEND_CONTAINER, "--format", "{{.State.Health.Status}} {{.State.Health.FailingStreak}}"], timeout=30)
    if code != 0:
        return
    health, _, streak = out.strip().partition(" ")
    try:
        streak_n = int(streak)
    except ValueError:
        streak_n = 0
    if health != "unhealthy" or streak_n < HUNG_FAILING_STREAK:
        return
    try:
        from pymongo import MongoClient
        c = MongoClient(MONGO, serverSelectionTimeoutMS=8000, tz_aware=True)
        now = datetime.now(timezone.utc)
        ev = c[TELEMETRY_DB]["activity_events"]
        recent = ev.count_documents({"meta.kind": {"$in": ["file.start", "file.end"]}, "ts": {"$gte": now - timedelta(minutes=HUNG_IDLE_MIN)}})
        job = c[TENANT_DB]["ingestion_jobs"].find_one({"status": "running"}, projection={"phase": 1})
        cur = c[TELEMETRY_DB]["activity_current"].find_one({"_id": {"$regex": "quellex"}}) or {}
    except Exception as exc:  # noqa: BLE001
        bad(f"hung-API check could not read MongoDB: {exc}")
        return
    cores, _ = container_load(BACKEND_CONTAINER)
    detail = (f"unhealthy x{streak_n}, {recent} file events in {HUNG_IDLE_MIN} min, API {cores if cores is None else round(cores, 2)} cores, "
              f"phase {(job or {}).get('phase')}, current file {cur.get('current_file')}")
    if recent > 0 or not job or job.get("phase") != "processing":
        bad(f"backend unhealthy but not the hang signature, not restarting ({detail})")
        return
    # The CPU figure is informational only: conversion workers started before the hang keep
    # converting their files (14-16 cores seen on 2026-11-03) while the main thread is stuck.
    last = state.get("last_backend_restart")
    last_dt = datetime.fromisoformat(last) if last else None
    if last_dt is not None and datetime.now(timezone.utc) - last_dt < timedelta(minutes=BACKEND_RESTART_COOLDOWN_MIN):
        bad(f"API hung but restarted at {last} already; not again yet ({detail})")
        return
    if no_restart:
        bad(f"API hung, restart suppressed by --no-restart ({detail})")
        return
    path = cur.get("current_file")
    if path:
        try:
            c[TENANT_DB]["failed_documents"].insert_one({
                "file_path": path, "file_name": path.rsplit("/", 1)[-1], "file_size_bytes": 0,
                "error_type": "hang",
                "error_message": f"API main process hung after converting this file ({detail}); recorded by ingestion-watch and skipped",
                "timeout_seconds": 0.0, "processing_time_ms": 0.0, "failed_at": datetime.now().isoformat(), "profile_key": "default",
                "resolved": False, "retry_count": 0, "retry_pending": False,
                "job_id": (c[TENANT_DB]["ingestion_jobs"].find_one({"status": "running"}, projection={"_id": 1}) or {}).get("_id"),
                "recorded_by": "ingestion-watch",
            })
        except Exception as exc:  # noqa: BLE001
            bad(f"could not record the hang file {path}: {exc}")
    # Evidence first: a py-spy stack of the stuck API process (py-spy is pip-installed in the
    # container; the install does not survive a container recreate, only a restart)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    code, dump = run(["docker", "exec", "--privileged", BACKEND_CONTAINER, "sh", "-c",
                      "PYSPY=$(command -v py-spy || echo /app/.venv/bin/py-spy); timeout 90 $PYSPY dump --pid 1 --nonblocking 2>&1"], timeout=120)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / f"hang-stack-{stamp}.txt").write_text(f"{detail}\n\n{dump}\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        bad(f"could not save the hang stack: {exc}")
    code, out = run(["docker", "restart", BACKEND_CONTAINER], timeout=300)
    state["last_backend_restart"] = datetime.now(timezone.utc).isoformat()
    state["backend_restarts"] = int(state.get("backend_restarts", 0)) + 1
    bad(f"API hung: recorded {path} as hang, stack in hang-stack-{stamp}.txt, RESTARTED {BACKEND_CONTAINER} (exit {code}; {detail})")


def check_host() -> None:
    code, out = run(["sc", "query", "RecallHubHostAgent"], timeout=20)
    if code != 0 or "RUNNING" not in out:
        bad(f"host agent service not running: {out[:160]}")
    else:
        ok("host agent service running")
    try:
        free_gb = shutil.disk_usage("C:\\").free / 2**30
        (ok if free_gb > 100 else bad)(f"C: free {free_gb:.0f} GB")
    except Exception as exc:  # noqa: BLE001
        bad(f"disk check failed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-restart", action="store_true", help="report a degraded embedder but do not restart it")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    state = load_state()
    check_docker(args.no_restart, state)
    check_mongo()
    check_hung_api(args.no_restart, state)
    check_host()
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["last_anomalies"] = anomalies
    try:
        save_state(state)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {'ANOMALY ' + ' | '.join(anomalies) if anomalies else 'OK ' + '; '.join(oks)}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"(could not write state/log: {exc})", file=sys.stderr)
    if args.json:
        print(json.dumps({"anomalies": anomalies, "ok": oks}, indent=1, ensure_ascii=False))
    else:
        for a in anomalies:
            print("ANOMALY:", a)
        for o in oks:
            print("OK:", o)
        print("RESULT:", "ANOMALIES" if anomalies else "ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
