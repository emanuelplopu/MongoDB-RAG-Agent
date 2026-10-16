"""Resource telemetry for benchmark runs: GPU, host, containers and Ollama, sampled on a fixed grid.

:class:`ResourceSampler` samples everything in a background thread, appends each sample to a JSONL
time series and writes the static facts about the machine (GPU model and limits, CPU, RAM, Docker VM
size, Ollama version) next to it as ``<series>.meta.json``. :func:`summarize_samples` reduces a series
to the numbers the Markdown report and the anomaly list use; :mod:`telemetry_html` draws the series.

Sample layout (one JSON object per line, ``v`` = 2; files without ``v`` hold the first layout, which
:func:`normalize_sample` converts):

* ``t`` seconds since the sampler started, ``ts`` UTC time, ``sample_ms`` how long the probes took;
* ``containers``: per container ``cpu_pct`` (of one core, as ``docker stats`` reports it), ``mem_mb``,
  ``mem_limit_mb`` and cumulative ``blk_read_mb`` / ``blk_write_mb`` / ``net_rx_mb`` / ``net_tx_mb``;
* ``gpu``: utilisation of the SMs and of the memory controller, VRAM used and total, power draw and
  limit, temperatures and the distance to the thermal slowdown limit (``tlimit_c``), SM and memory
  clock, fan, P-state, the active clock-event reasons (``reasons``) with the raw mask, the driver's
  cumulative throttle counters in microseconds (``throttle_us``) and the PCIe link;
* ``host``: CPU total and per core, CPU frequency, RAM, swap, cumulative disk and network traffic, the
  ACPI thermal zones where Windows exposes them (``cpu_temp_c`` is their maximum; it is a board sensor,
  not the CPU package), and RSS / CPU of the Ollama processes and of the Docker VM (``procs``);
* ``ollama``: loaded models with size, VRAM share and context length;
* ``health``: round trip of the backend health endpoint, which shows a blocked event loop.

Every probe is best-effort: a missing tool leaves its key out of the sample and never stops the run.
"""

from __future__ import annotations

import collections
import csv
import json
import logging
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import httpx
import psutil

from . import telemetry as tm

logger = logging.getLogger(__name__)

SAMPLE_VERSION = 2
_NA = {"", "N/A", "[N/A]", "[NOT SUPPORTED]", "[INSUFFICIENT PERMISSIONS]", "NOT SUPPORTED", "-"}


# ─── statistics helpers ──────────────────────────────────────────────────────


def pct(values: Sequence[float], p: float) -> Optional[float]:
    """Nearest-rank percentile (``p`` in 0..100) rounded to one decimal; None for no values."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return round(s[k], 1)


def summary_stats(values: Sequence[float]) -> dict[str, Any]:
    """``n``, ``mean``, ``p50``, ``p95``, ``max``, ``min`` and ``sum`` of a series (``{"n": 0}`` when empty)."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {"n": 0}
    return {"n": len(clean), "mean": round(statistics.fmean(clean), 1), "p50": pct(clean, 50), "p95": pct(clean, 95),
            "max": round(max(clean), 1), "min": round(min(clean), 1), "sum": round(sum(clean), 1)}


def _num(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    v = value.strip().strip("[]").strip()
    if not v or v.upper() in _NA:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── GPU (nvidia-smi) ────────────────────────────────────────────────────────

#: Every field the report uses. Drivers before 2023 do not know ``clocks_event_reasons`` or ``tlimit``;
#: :class:`GpuProbe` then falls back to :data:`GPU_FIELDS_BASIC`.
GPU_FIELDS_FULL: tuple[str, ...] = (
    "utilization.gpu", "utilization.memory", "memory.used", "memory.total", "power.draw", "power.limit",
    "temperature.gpu", "temperature.memory", "temperature.gpu.tlimit", "clocks.sm", "clocks.mem", "fan.speed", "pstate",
    "clocks_event_reasons.active", "clocks_event_reasons.sw_power_cap", "clocks_event_reasons.hw_slowdown",
    "clocks_event_reasons.hw_thermal_slowdown", "clocks_event_reasons.sw_thermal_slowdown",
    "clocks_event_reasons.hw_power_brake_slowdown",
    "clocks_event_reasons_counters.sw_power_cap", "clocks_event_reasons_counters.sw_thermal_slowdown",
    "clocks_event_reasons_counters.hw_thermal_slowdown", "clocks_event_reasons_counters.hw_power_brake_slowdown",
    "pcie.link.gen.gpucurrent", "pcie.link.width.current",
)
GPU_FIELDS_BASIC: tuple[str, ...] = (
    "utilization.gpu", "utilization.memory", "memory.used", "memory.total", "power.draw", "power.limit",
    "temperature.gpu", "clocks.sm", "clocks.mem", "fan.speed", "pstate", "clocks_throttle_reasons.active",
)
GPU_STATIC_FULL: tuple[str, ...] = (
    "name", "driver_version", "vbios_version", "memory.total", "power.limit", "power.max_limit", "clocks.max.sm",
    "clocks.max.memory", "pcie.link.gen.max", "pcie.link.width.max",
)
GPU_STATIC_BASIC: tuple[str, ...] = ("name", "driver_version", "memory.total", "power.limit")

_GPU_NUMBERS: dict[str, str] = {
    "utilization.gpu": "util_pct", "utilization.memory": "mem_util_pct", "memory.used": "mem_mb", "memory.total": "mem_total_mb",
    "power.draw": "power_w", "power.limit": "power_limit_w", "temperature.gpu": "temp_c", "temperature.memory": "mem_temp_c",
    "temperature.gpu.tlimit": "tlimit_c", "clocks.sm": "sm_mhz", "clocks.mem": "mem_mhz", "fan.speed": "fan_pct",
    "pcie.link.gen.gpucurrent": "pcie_gen", "pcie.link.width.current": "pcie_width",
}
_GPU_STATIC_KEYS: dict[str, str] = {
    "name": "name", "driver_version": "driver", "vbios_version": "vbios", "memory.total": "mem_total_mb", "power.limit": "power_limit_w",
    "power.max_limit": "power_max_limit_w", "clocks.max.sm": "sm_max_mhz", "clocks.max.memory": "mem_max_mhz",
    "pcie.link.gen.max": "pcie_gen_max", "pcie.link.width.max": "pcie_width_max",
}
#: Short names of the clock-event reasons (NVML ``nvmlClocksEventReasons``), by flag field suffix and by mask bit.
REASON_BY_SUFFIX: dict[str, str] = {
    "sw_power_cap": "power_cap", "hw_slowdown": "hw_slowdown", "hw_thermal_slowdown": "hw_thermal",
    "sw_thermal_slowdown": "sw_thermal", "hw_power_brake_slowdown": "hw_power_brake",
}
REASON_BITS: dict[int, str] = {
    0x2: "app_clocks", 0x4: "power_cap", 0x8: "hw_slowdown", 0x10: "sync_boost", 0x20: "sw_thermal", 0x40: "hw_thermal",
    0x80: "hw_power_brake", 0x100: "display_clocks",
}
THERMAL_REASONS = frozenset({"sw_thermal", "hw_thermal", "hw_slowdown"})
REASON_LABELS: dict[str, str] = {
    "power_cap": "power cap", "sw_thermal": "thermal (software)", "hw_thermal": "thermal (hardware)", "hw_slowdown": "hardware slowdown",
    "hw_power_brake": "power brake", "app_clocks": "application clocks", "sync_boost": "sync boost", "display_clocks": "display clocks",
}


def reasons_from_mask(mask: Optional[str]) -> list[str]:
    """Active clock-event reasons from the hex mask ``nvidia-smi`` prints (bit 0x1, GPU idle, is not a reason)."""
    if not mask or mask.strip().upper() in _NA:
        return []
    try:
        bits = int(mask, 16)
    except ValueError:
        return []
    return [name for bit, name in REASON_BITS.items() if bits & bit]


def parse_gpu_line(fields: Sequence[str], line: str) -> Optional[dict[str, Any]]:
    """Parse one ``--format=csv,noheader,nounits`` line queried with ``fields`` into a sample's ``gpu`` dict."""
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != len(fields):
        return None
    raw = dict(zip(fields, parts))
    g: dict[str, Any] = {}
    for field, key in _GPU_NUMBERS.items():
        if field in raw:
            v = _num(raw[field])
            if v is not None:
                g[key] = v
    pstate = raw.get("pstate", "")
    if pstate and pstate.upper() not in _NA:
        g["pstate"] = pstate
    mask = raw.get("clocks_event_reasons.active") or raw.get("clocks_throttle_reasons.active")
    reasons = set(reasons_from_mask(mask))
    if mask and mask.strip().upper() not in _NA:
        g["clock_reasons"] = mask.strip()
    counters: dict[str, int] = {}
    for field, value in raw.items():
        if field.startswith("clocks_event_reasons_counters."):
            v = _num(value)
            if v is not None:
                counters[REASON_BY_SUFFIX.get(field.rsplit(".", 1)[1], field)] = int(v)
        elif field.startswith(("clocks_event_reasons.", "clocks_throttle_reasons.")) and not field.endswith(".active"):
            if value.strip().lower() == "active":
                reasons.add(REASON_BY_SUFFIX.get(field.rsplit(".", 1)[1], field))
    g["reasons"] = sorted(reasons)
    if counters:
        g["throttle_us"] = counters
    return g


def parse_gpu_static(fields: Sequence[str], line: str) -> Optional[dict[str, Any]]:
    """Parse the static ``nvidia-smi`` query (name, driver, limits) into a dict."""
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != len(fields):
        return None
    out: dict[str, Any] = {}
    for field, value in zip(fields, parts):
        key = _GPU_STATIC_KEYS.get(field, field)
        if field in ("name", "driver_version", "vbios_version"):
            if value and value.upper() not in _NA:
                out[key] = value
        else:
            v = _num(value)
            if v is not None:
                out[key] = v
    return out


def run_nvidia_smi(args: Sequence[str], timeout: float = 15.0) -> Optional[str]:
    """First non-empty stdout line of an ``nvidia-smi`` call, or None when it fails or is not installed."""
    try:
        out = subprocess.run(["nvidia-smi", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("nvidia-smi failed: %s", exc)
        return None
    if out.returncode != 0:
        return None
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else None


class GpuProbe:
    """Samples one GPU through ``nvidia-smi``; picks the richest field set the driver understands."""

    def __init__(self, gpu_index: int = 0, runner: Callable[[Sequence[str]], Optional[str]] = run_nvidia_smi):  # noqa: D107
        self.gpu_index = gpu_index
        self._run = runner
        self.available = runner is not run_nvidia_smi or shutil.which("nvidia-smi") is not None
        self.fields: tuple[str, ...] = ()
        self.static: dict[str, Any] = {}
        if self.available:
            self._detect()

    def _query(self, fields: Sequence[str]) -> Optional[str]:
        return self._run(["-i", str(self.gpu_index), f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"])

    def _detect(self) -> None:
        for fields in (GPU_FIELDS_FULL, GPU_FIELDS_BASIC):
            line = self._query(fields)
            if line and parse_gpu_line(fields, line) is not None:
                self.fields = fields
                break
        else:
            self.available = False
            return
        for fields in (GPU_STATIC_FULL, GPU_STATIC_BASIC):
            line = self._query(fields)
            parsed = parse_gpu_static(fields, line) if line else None
            if parsed:
                self.static = parsed
                break
        self.static["fields"] = "full" if self.fields == GPU_FIELDS_FULL else "basic"

    def read(self) -> Optional[dict[str, Any]]:
        """One sample, or None when the GPU cannot be read."""
        if not self.available:
            return None
        line = self._query(self.fields)
        return parse_gpu_line(self.fields, line) if line else None


# ─── containers (docker stats) ───────────────────────────────────────────────

_UNIT_MB: dict[str, float] = {
    "b": 1e-6, "kb": 1e-3, "kib": 1024 / 1e6, "mb": 1.0, "mib": 1.048576, "gb": 1000.0, "gib": 1073.741824, "tb": 1e6, "tib": 1048576.0,
}
_SIZE = re.compile(r"([\d.]+)\s*([A-Za-z]+)")


def size_mb(text: str) -> Optional[float]:
    """``"2.978GiB"`` -> 3123.2 (MB, decimal); None when unparsable."""
    m = _SIZE.match(text.strip())
    if not m:
        return None
    factor = _UNIT_MB.get(m.group(2).lower())
    return round(float(m.group(1)) * factor, 2) if factor is not None else None


def parse_docker_stats(text: str) -> dict[str, dict[str, Any]]:
    """Parse ``docker stats --no-stream --format "{{.Name}};{{.CPUPerc}};{{.MemUsage}};{{.BlockIO}};{{.NetIO}}"``."""
    out: dict[str, dict[str, Any]] = {}
    for line in text.strip().splitlines():
        parts = line.split(";")
        if len(parts) < 3:
            continue
        name, cpu, mem = parts[0].strip(), parts[1].strip(), parts[2]
        used, _, limit = mem.partition("/")
        row: dict[str, Any] = {"cpu_pct": _num(cpu.rstrip("%")) or 0.0, "mem_mb": size_mb(used) or 0.0}
        lim = size_mb(limit) if limit else None
        if lim is not None:
            row["mem_limit_mb"] = lim
        if len(parts) > 3:
            rd, _, wr = parts[3].partition("/")
            row["blk_read_mb"], row["blk_write_mb"] = size_mb(rd), size_mb(wr)
        if len(parts) > 4:
            rx, _, tx = parts[4].partition("/")
            row["net_rx_mb"], row["net_tx_mb"] = size_mb(rx), size_mb(tx)
        out[name] = row
    return out


def read_docker_stats(timeout: float = 30.0) -> dict[str, dict[str, Any]]:
    """CPU, memory, block and network I/O of every running container (empty when docker is unavailable)."""
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}};{{.BlockIO}};{{.NetIO}}"],
                             capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("docker stats failed: %s", exc)
        return {}
    return parse_docker_stats(out.stdout) if out.returncode == 0 else {}


def docker_info() -> dict[str, Any]:
    """CPUs and memory of the Docker engine (the Docker Desktop VM on Windows) and its version."""
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.NCPU}};{{.MemTotal}};{{.ServerVersion}};{{.OperatingSystem}}"],
                             capture_output=True, text=True, timeout=30)
        cpus, mem, version, os_name = out.stdout.strip().split(";", 3)
        return {"cpus": int(cpus), "mem_gb": round(int(mem) / 2**30, 2), "version": version, "os": os_name}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


# ─── host (psutil, thermal zones) ────────────────────────────────────────────

#: Process name prefixes (lower case) that are attributed to a group in ``host.procs``.
PROCESS_GROUPS: dict[str, tuple[str, ...]] = {
    "ollama": ("ollama",),
    "docker_vm": ("vmmem",),
    "docker_desktop": ("docker", "com.docker"),
}


class HostProbe:
    """CPU, memory, swap, disk and network of the host plus the RSS / CPU of named process groups."""

    def __init__(self, process_groups: Optional[Mapping[str, tuple[str, ...]]] = None):  # noqa: D107
        self.groups = dict(process_groups if process_groups is not None else PROCESS_GROUPS)
        self.cpu_count = psutil.cpu_count() or 1
        self._procs: dict[int, tuple[str, psutil.Process]] = {}
        try:
            psutil.cpu_percent(percpu=True)  # prime: the first call always returns 0
        except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
            logger.debug("psutil cpu_percent failed: %s", exc)

    def static(self) -> dict[str, Any]:
        """Hostname, OS, CPU model and core counts, RAM, Python."""
        info: dict[str, Any] = {"hostname": socket.gethostname(), "os": platform.platform(), "cpu": platform.processor() or platform.machine(),
                                "cores_logical": self.cpu_count, "python": sys.version.split()[0]}
        try:
            info["cores_physical"] = psutil.cpu_count(logical=False)
            info["ram_total_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
            freq = psutil.cpu_freq()
            if freq and freq.max:
                info["cpu_max_mhz"] = freq.max
        except Exception as exc:  # noqa: BLE001
            logger.debug("psutil static info failed: %s", exc)
        return info

    def _processes(self) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {g: {"rss_gb": 0.0, "cpu_pct": 0.0, "count": 0} for g in self.groups}
        seen: set[int] = set()
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower()
            group = next((g for g, prefixes in self.groups.items() if name.startswith(prefixes)), None)
            if group is None:
                continue
            entry = self._procs.get(p.pid)
            if entry is None:
                try:
                    proc = psutil.Process(p.pid)
                    proc.cpu_percent(None)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                entry = self._procs[p.pid] = (group, proc)
            seen.add(p.pid)
            try:
                rss = entry[1].memory_info().rss
                cpu = entry[1].cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            g = groups[entry[0]]
            g["rss_gb"] += rss / 2**30
            g["cpu_pct"] += cpu / self.cpu_count
            g["count"] += 1
        for pid in [pid for pid in self._procs if pid not in seen]:
            del self._procs[pid]
        return {g: {"rss_gb": round(v["rss_gb"], 3), "cpu_pct": round(v["cpu_pct"], 2), "count": v["count"]}
                for g, v in groups.items() if v["count"]}

    def sample(self) -> dict[str, Any]:
        """One host sample (never raises; keys are left out when a reading fails)."""
        host: dict[str, Any] = {}
        try:
            per_core = psutil.cpu_percent(percpu=True)
            host["cpu_pct"] = round(statistics.fmean(per_core), 1) if per_core else 0.0
            host["cpu_per_core"] = [round(c, 1) for c in per_core]
        except Exception as exc:  # noqa: BLE001
            logger.debug("cpu_percent failed: %s", exc)
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                host["cpu_mhz"] = round(freq.current)
        except Exception:  # noqa: BLE001 - not available on every platform
            pass
        try:
            vm = psutil.virtual_memory()
            host.update({"ram_used_gb": round(vm.used / 2**30, 2), "ram_avail_gb": round(vm.available / 2**30, 2),
                         "ram_total_gb": round(vm.total / 2**30, 2), "ram_pct": vm.percent})
            sw = psutil.swap_memory()
            host["swap_used_gb"] = round(sw.used / 2**30, 2)
        except Exception as exc:  # noqa: BLE001
            logger.debug("memory readings failed: %s", exc)
        try:
            disk = psutil.disk_io_counters()
            if disk:
                host["disk_read_mb"], host["disk_write_mb"] = round(disk.read_bytes / 1e6, 1), round(disk.write_bytes / 1e6, 1)
            net = psutil.net_io_counters()
            if net:
                host["net_sent_mb"], host["net_recv_mb"] = round(net.bytes_sent / 1e6, 1), round(net.bytes_recv / 1e6, 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("I/O counters failed: %s", exc)
        try:
            host["procs"] = self._processes()
        except Exception as exc:  # noqa: BLE001
            logger.debug("process scan failed: %s", exc)
        return host


_TZ_NAME = re.compile(r"\(([^)]*)\)\\Temperature", re.IGNORECASE)


def parse_typeperf_thermal(text: str) -> dict[str, float]:
    """Zone -> degrees C from ``typeperf "\\Thermal Zone Information(*)\\Temperature" -sc 1`` (values are Kelvin)."""
    rows = [r for r in csv.reader(line for line in text.splitlines() if line.strip()) if len(r) >= 2]
    if len(rows) < 2:
        return {}
    header = rows[0]
    # typeperf appends status lines ("Exiting, please wait...") that also split on commas: take the last
    # row whose values are all numbers.
    for data in reversed(rows[1:]):
        if len(data) != len(header):
            continue
        try:
            values = [float(v) for v in data[1:]]
        except ValueError:
            continue
        out: dict[str, float] = {}
        for name, kelvin in zip(header[1:], values):
            m = _TZ_NAME.search(name)
            zone = (m.group(1) if m else name).split(".")[-1]
            if kelvin > 200:
                out[zone] = round(kelvin - 273.15, 1)
        return out
    return {}


class ThermalProbe:
    """Board / CPU temperatures: ACPI thermal zones through ``typeperf`` on Windows, ``psutil`` sensors elsewhere."""

    def __init__(self, runner: Optional[Callable[[], Optional[str]]] = None):  # noqa: D107
        self._run = runner or self._typeperf
        self.source = "typeperf" if sys.platform == "win32" else "psutil"
        if runner is None and sys.platform != "win32":
            self._run = None
        self.available = bool(self.sample())

    @staticmethod
    def _typeperf() -> Optional[str]:
        try:
            out = subprocess.run(["typeperf", r"\Thermal Zone Information(*)\Temperature", "-sc", "1"],
                                 capture_output=True, text=True, timeout=20)
            return out.stdout if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    def sample(self) -> dict[str, float]:
        """Zone -> degrees C (empty when nothing is readable)."""
        if self._run is not None:
            text = self._run()
            return parse_typeperf_thermal(text) if text else {}
        try:
            temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            return {}
        out: dict[str, float] = {}
        for chip, entries in (temps or {}).items():
            for i, e in enumerate(entries):
                if e.current:
                    out[f"{chip}:{e.label or i}"] = round(e.current, 1)
        return out


# ─── the sampler ─────────────────────────────────────────────────────────────


class ResourceSampler:
    """Samples GPU, host, containers, Ollama and the backend health endpoint on a fixed interval in a thread.

    With ``out_path`` every sample is appended to that JSONL file as it is taken and the static facts go to
    ``<out_path stem>.meta.json``, so a page can be rendered while the run is still going.
    """

    def __init__(self, containers: Iterable[str], interval_s: float = 5.0, out_path: Optional[Path] = None,
                 ollama_url: Optional[str] = None, health_url: Optional[str] = None, *, gpu_index: int = 0,
                 gpu_probe: Optional[GpuProbe] = None, docker_probe: Optional[Callable[[], dict[str, dict[str, Any]]]] = None,
                 host_probe: Optional[HostProbe] = None, thermal_probe: Optional[ThermalProbe] = None,
                 docker_info_probe: Optional[Callable[[], dict[str, Any]]] = None):  # noqa: D107
        self.containers = list(containers)
        self.interval_s = interval_s
        self.out_path = out_path
        self.ollama_url = ollama_url
        self.health_url = health_url
        self.samples: list[dict[str, Any]] = []
        self.gpu = gpu_probe if gpu_probe is not None else GpuProbe(gpu_index)
        self.docker = docker_probe if docker_probe is not None else read_docker_stats
        self.host = host_probe if host_probe is not None else HostProbe()
        self.thermal = thermal_probe if thermal_probe is not None else ThermalProbe()
        self._docker_info = docker_info_probe if docker_info_probe is not None else docker_info
        self.meta_info: dict[str, Any] = {}
        self._http: Optional[httpx.Client] = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="resource-sampler", daemon=True)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="resource-probe")
        self._t0 = time.monotonic()

    @property
    def meta_path(self) -> Optional[Path]:
        """Where the static facts are written (``resources_<run>.meta.json``)."""
        return self.out_path.with_name(self.out_path.stem + ".meta.json") if self.out_path else None

    def meta(self) -> dict[str, Any]:
        """Static facts about the machine and this sampling run."""
        info: dict[str, Any] = {"version": SAMPLE_VERSION, "started_at": _now(), "interval_s": self.interval_s,
                                "containers": self.containers, "host": self.host.static(), "gpu": self.gpu.static or None,
                                "docker": self._docker_info(), "thermal_source": self.thermal.source if self.thermal.available else None}
        if self.ollama_url:
            info["ollama"] = {"url": self.ollama_url, "version": (tm.ollama_get(self.ollama_url, "/api/version") or {}).get("version"),
                              "loaded_at_start": tm.ollama_ps(self.ollama_url)}
        return info

    def _health(self) -> dict[str, Any]:
        # One keep-alive connection: a fresh connection to ``localhost`` can cost seconds on Windows when the
        # service only binds IPv4, which would hide the event-loop latency the probe is after.
        if self._http is None:
            self._http = httpx.Client(timeout=10)
        t = time.monotonic()
        try:
            ok = self._http.get(self.health_url or "").status_code == 200
        except httpx.HTTPError:
            ok = False
        return {"ms": round((time.monotonic() - t) * 1000, 1), "ok": ok}

    def _sample(self) -> dict[str, Any]:
        started = time.monotonic()
        row: dict[str, Any] = {"v": SAMPLE_VERSION, "t": round(started - self._t0, 1), "ts": _now()}
        futures: dict[str, Future[Any]] = {"containers": self._pool.submit(self.docker)}
        if self.gpu.available:
            futures["gpu"] = self._pool.submit(self.gpu.read)
        if self.thermal.available:
            futures["thermal"] = self._pool.submit(self.thermal.sample)
        if self.ollama_url:
            futures["ollama"] = self._pool.submit(tm.ollama_ps, self.ollama_url)
        if self.health_url:
            futures["health"] = self._pool.submit(self._health)
        row["host"] = self.host.sample()
        for key, fut in futures.items():
            try:
                value = fut.result(timeout=max(30.0, self.interval_s * 3))
            except Exception as exc:  # noqa: BLE001 - one failing probe must not lose the sample
                row.setdefault("errors", {})[key] = f"{type(exc).__name__}: {exc}"[:200]
                continue
            if key == "thermal":
                if value:
                    row["host"]["thermal_zones"] = value
                    row["host"]["cpu_temp_c"] = max(value.values())
            elif value is not None:
                row[key] = value
        row["sample_ms"] = round((time.monotonic() - started) * 1000)
        return row

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                sample = self._sample()
            except Exception as exc:  # noqa: BLE001 - keep sampling
                logger.warning("resource sample failed: %s", exc)
                sample = {"v": SAMPLE_VERSION, "t": round(started - self._t0, 1), "ts": _now(), "errors": {"sample": str(exc)[:200]}}
            self.samples.append(sample)
            if self.out_path:
                try:
                    tm.jsonl_append(self.out_path, sample)
                except OSError as exc:
                    logger.warning("could not append to %s: %s", self.out_path, exc)
            self._stop.wait(max(0.0, self.interval_s - (time.monotonic() - started)))

    def __enter__(self) -> "ResourceSampler":
        self._t0 = time.monotonic()
        try:
            self.meta_info = self.meta()
            if self.meta_path:
                self.meta_path.write_text(json.dumps(self.meta_info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - static facts are optional
            logger.warning("could not collect the machine facts: %s", exc)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=max(60.0, self.interval_s * 4))
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._http is not None:
            self._http.close()
            self._http = None

    def summary(self) -> dict[str, Any]:
        """Aggregates over the samples taken so far (see :func:`summarize_samples`) plus the static facts."""
        out = summarize_samples(self.samples, self.containers)
        if self.meta_info:
            out["static"] = {k: self.meta_info.get(k) for k in ("host", "gpu", "docker", "ollama", "thermal_source")}
        return out


# ─── reading and summarising series ──────────────────────────────────────────


def normalize_sample(row: Mapping[str, Any]) -> dict[str, Any]:
    """Bring a sample of any layout version to the current one (containers under ``containers``, GPU reasons listed)."""
    if int(row.get("v") or 1) >= 2:
        out = dict(row)
    else:
        out = {"v": SAMPLE_VERSION, "t": row.get("t"), "ts": row.get("ts")}
        out["containers"] = {k: dict(v) for k, v in row.items()
                             if isinstance(v, dict) and "cpu_pct" in v and "mem_mb" in v and k not in ("gpu", "host")}
        for key in ("gpu", "ollama", "health", "docker_error"):
            if key in row:
                out[key] = row[key]
    gpu = out.get("gpu")
    if isinstance(gpu, dict) and "reasons" not in gpu:
        gpu = dict(gpu)
        gpu["reasons"] = reasons_from_mask(gpu.get("clock_reasons"))
        out["gpu"] = gpu
    return out


def load_series(path: Path) -> list[dict[str, Any]]:
    """Read a resources JSONL file into normalised samples (malformed lines are skipped)."""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(normalize_sample(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return rows


def _drift(values: Sequence[float]) -> Optional[float]:
    if len(values) < 6:
        return None
    third = len(values) // 3
    return round(statistics.fmean(values[-third:]) - statistics.fmean(values[:third]), 1)


def _total(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(clean[-1] - clean[0], 1) if len(clean) >= 2 else None


def _steps(samples: Sequence[Mapping[str, Any]], cap: float) -> list[tuple[Mapping[str, Any], float]]:
    """(sample, seconds until the next sample) pairs; gaps longer than ``cap`` (resumed runs) count as ``cap``."""
    out = []
    for prev, cur in zip(samples, samples[1:]):
        dt = float(cur.get("t") or 0) - float(prev.get("t") or 0)
        out.append((cur, min(max(dt, 0.0), cap) if dt > 0 else cap))
    return out


def summarize_samples(samples: Sequence[Mapping[str, Any]], containers: Sequence[str] = ()) -> dict[str, Any]:
    """Aggregates, drift (last third vs first third), GPU throttle time, host peaks and Ollama residency.

    The keys mirror the first sampler version (container names at the top level, ``gpu``, ``ollama``,
    ``health``) so older summaries and reports keep working, plus ``host`` and the extended ``gpu`` keys.
    """
    rows = [normalize_sample(s) for s in samples]
    out: dict[str, Any] = {"samples": len(rows)}
    if not rows:
        return out
    ts = [float(r.get("t") or 0) for r in rows]
    dts = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    interval = statistics.median(dts) if dts else 0.0
    out["interval_s"] = round(interval, 1)
    out["duration_s"] = round(sum(min(dt, interval * 3) for dt in dts), 1) if dts else 0.0
    cap = interval * 3 if interval else 60.0
    seen = list(containers) + sorted({n for r in rows for n in (r.get("containers") or {})} - set(containers))
    for name in seen:
        cs = [r["containers"][name] for r in rows if name in (r.get("containers") or {})]
        if not cs:
            continue
        mem = [c["mem_mb"] for c in cs]
        out[name] = {"cpu_pct": summary_stats([c["cpu_pct"] for c in cs]), "mem_mb": summary_stats(mem), "mem_drift_mb": _drift(mem)}
        for key in ("blk_read_mb", "blk_write_mb", "net_rx_mb", "net_tx_mb"):
            total = _total([c.get(key) for c in cs])
            if total is not None:
                out[name][key] = total
    gpu = [r["gpu"] for r in rows if isinstance(r.get("gpu"), dict)]
    if gpu:
        throttled = [g for g in gpu if g.get("reasons")]
        g_out: dict[str, Any] = {key: summary_stats([g.get(key) for g in gpu])
                                 for key in ("util_pct", "mem_util_pct", "mem_mb", "power_w", "temp_c", "mem_temp_c", "sm_mhz", "mem_mhz", "fan_pct", "tlimit_c")}
        g_out = {k: v for k, v in g_out.items() if v.get("n")}
        g_out["throttled_samples"] = len(throttled)
        g_out["clock_reasons"] = dict(collections.Counter(g.get("clock_reasons") or ",".join(g["reasons"]) for g in throttled))
        reasons_s: dict[str, float] = collections.defaultdict(float)
        for cur, dt in _steps([r for r in rows if isinstance(r.get("gpu"), dict)], cap):
            for reason in cur["gpu"].get("reasons") or []:
                reasons_s[reason] += dt
        g_out["reasons_s"] = {k: round(v, 1) for k, v in reasons_s.items()}
        g_out["thermal_s"] = round(sum(v for k, v in reasons_s.items() if k in THERMAL_REASONS), 1)
        counters = [g["throttle_us"] for g in gpu if isinstance(g.get("throttle_us"), dict)]
        if len(counters) >= 2:
            g_out["throttle_counters_s"] = {k: round((counters[-1].get(k, 0) - counters[0].get(k, 0)) / 1e6, 1)
                                            for k in counters[0] if k in counters[-1]}
        for key, fn in (("mem_total_mb", max), ("power_limit_w", max)):
            vals = [g[key] for g in gpu if g.get(key) is not None]
            if vals:
                g_out[key] = fn(vals)
        if "tlimit_c" in g_out:
            g_out["tlimit_min_c"] = g_out["tlimit_c"]["min"]
        pstates = collections.Counter(g["pstate"] for g in gpu if g.get("pstate"))
        if pstates:
            g_out["pstates"] = dict(pstates)
        out["gpu"] = g_out
    host = [r["host"] for r in rows if isinstance(r.get("host"), dict)]
    if host:
        h_out: dict[str, Any] = {key: summary_stats([h.get(key) for h in host])
                                 for key in ("cpu_pct", "cpu_mhz", "ram_used_gb", "ram_pct", "swap_used_gb", "cpu_temp_c")}
        h_out = {k: v for k, v in h_out.items() if v.get("n")}
        totals = [h["ram_total_gb"] for h in host if h.get("ram_total_gb")]
        if totals:
            h_out["ram_total_gb"] = max(totals)
        for key in ("disk_read_mb", "disk_write_mb", "net_sent_mb", "net_recv_mb"):
            total = _total([h.get(key) for h in host])
            if total is not None:
                h_out[key] = total
        groups = sorted({g for h in host for g in (h.get("procs") or {})})
        if groups:
            h_out["procs"] = {g: {"rss_gb": summary_stats([h["procs"][g]["rss_gb"] for h in host if g in (h.get("procs") or {})]),
                                  "cpu_pct": summary_stats([h["procs"][g]["cpu_pct"] for h in host if g in (h.get("procs") or {})])}
                              for g in groups}
        out["host"] = h_out
    ollama = [r["ollama"] for r in rows if isinstance(r.get("ollama"), list)]
    if ollama:
        out["ollama"] = {
            "samples_with_offload": sum(1 for models in ollama if any(m.get("offloaded") for m in models)),
            "models_seen": sorted({m["name"] for models in ollama for m in models if m.get("name")}),
            "max_models_loaded": max(len(models) for models in ollama),
            "context_lengths": sorted({m["context_length"] for models in ollama for m in models if m.get("context_length")}),
            "vram_gib_max": round(max((sum(m.get("vram_gib") or 0 for m in models) for models in ollama), default=0.0), 2),
        }
    health = [r["health"] for r in rows if isinstance(r.get("health"), dict)]
    if health:
        out["health"] = {"ms": summary_stats([h["ms"] for h in health]), "failures": sum(1 for h in health if not h.get("ok"))}
    sample_ms = [r["sample_ms"] for r in rows if r.get("sample_ms") is not None]
    if sample_ms:
        out["sample_ms"] = summary_stats(sample_ms)
    errors = collections.Counter(k for r in rows for k in (r.get("errors") or {}))
    if errors:
        out["probe_errors"] = dict(errors)
    return out
