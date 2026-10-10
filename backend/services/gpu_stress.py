"""GPU thermal and mixed-placement stress test for local llama.cpp model servers.

The runner starts one or more llama.cpp servers (Docker ``server-cuda`` or CPU
image, any GGUF from Hugging Face) or targets already-running OpenAI-compatible
endpoints, drives them with concurrent synthetic chat/embedding load for a fixed
duration and samples ``nvidia-smi`` (temperature, power, clocks, throttle
reasons, VRAM) plus host CPU/RAM the whole time. Scenarios are config-driven
(``backend/config/gpu_stress/*.yaml``) so the same suite can be rerun on other
hardware through ``quellexctl bench gpu-stress``.

A scenario may place models on the GPU, in system RAM (CPU image, ``-ngl 0``),
or split across both (``--fit on`` / ``--cpu-moe``), and several models can run
at the same time, which is how "models on the GPU and in RAM simultaneously"
is exercised.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import shutil
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Sequence, Tuple

import httpx
import psutil
import yaml
from pydantic import BaseModel, Field, model_validator

from backend.services.embedding_checks import HardwareInfo, collect_hardware

logger = logging.getLogger(__name__)

Placement = Literal["gpu", "cpu", "split"]
ModelKind = Literal["chat", "embedding"]
Phase = Literal["idle", "loading", "warmup", "load", "cooldown"]

STRESS_LABEL = "recallhub.gpu-stress=1"

# ---------------------------------------------------------------------------
# Specs (what to run)
# ---------------------------------------------------------------------------


class ModelSpec(BaseModel):
    """One model server inside a scenario."""

    name: str
    kind: ModelKind = "chat"
    hf_repo: Optional[str] = Field(default=None, description="Hugging Face GGUF ref, e.g. ggml-org/gemma-4-E2B-it-GGUF:Q8_0")
    endpoint: Optional[str] = Field(default=None, description="Existing OpenAI-compatible server root; no container is started")
    model_id: Optional[str] = Field(default=None, description="Model name sent in requests (endpoint mode, e.g. gemma4:26b)")
    placement: Placement = "gpu"
    n_gpu_layers: Optional[int] = Field(default=None, description="Explicit -ngl; default 999 (gpu), 0 (cpu), --fit (split)")
    cpu_moe: bool = Field(default=False, description="Keep Mixture-of-Experts weights in system RAM (--cpu-moe)")
    n_cpu_moe: Optional[int] = Field(default=None, description="Keep the first N layers' expert weights in RAM (--n-cpu-moe)")
    ctx: int = Field(default=8192, ge=512)
    slots: int = Field(default=4, ge=1)
    threads: Optional[int] = Field(default=None, ge=1)
    batch: int = Field(default=2048, ge=32)
    ubatch: int = Field(default=512, ge=32)
    image: Optional[str] = Field(default=None, description="Docker image override")
    extra_args: List[str] = Field(default_factory=list)
    load_timeout_s: int = Field(default=1800, ge=30, description="Covers first-run GGUF downloads")
    # Load profile
    concurrency: int = Field(default=4, ge=1)
    max_tokens: int = Field(default=128, ge=1, description="Chat: completion tokens per request")
    prompt_words: int = Field(default=300, ge=1, description="Chat: synthetic prompt length in words")
    batch_size: int = Field(default=32, ge=1, description="Embedding: texts per request")
    text_words: int = Field(default=150, ge=1, description="Embedding: words per text")
    request_timeout_s: float = Field(default=600.0, gt=0)

    @model_validator(mode="after")
    def _source_required(self) -> "ModelSpec":
        if not self.hf_repo and not self.endpoint:
            raise ValueError(f"model '{self.name}': either hf_repo or endpoint is required")
        return self

    @property
    def request_model(self) -> str:
        return self.model_id or self.name

    @property
    def placement_label(self) -> str:
        label = self.placement
        if self.cpu_moe:
            label += "+cpu-moe"
        elif self.n_cpu_moe:
            label += f"+cpu-moe:{self.n_cpu_moe}"
        return label


class StressDefaults(BaseModel):
    """Suite-wide settings (Docker, timing, sampling)."""

    volume: str = Field(default="recallhub_embedder_models", description="Named volume mounted at /root/.cache")
    cuda_image: str = "ghcr.io/ggml-org/llama.cpp:server-cuda"
    cpu_image: str = "ghcr.io/ggml-org/llama.cpp:server"
    gpus: str = Field(default="all", description="Value for docker run --gpus")
    base_port: int = Field(default=11091, ge=1024, le=65000)
    gpu_index: int = Field(default=0, ge=0)
    duration_s: int = Field(default=120, ge=5)
    warmup_s: int = Field(default=10, ge=0)
    cooldown_s: int = Field(default=30, ge=0)
    baseline_s: int = Field(default=3, ge=0, description="Idle sampling before models are loaded")
    sample_interval_s: float = Field(default=1.0, gt=0)


class Scenario(BaseModel):
    """One timed load phase with a fixed set of model servers."""

    name: str
    description: str = ""
    models: List[ModelSpec] = Field(min_length=1)
    duration_s: Optional[int] = Field(default=None, ge=5)
    warmup_s: Optional[int] = Field(default=None, ge=0)
    cooldown_s: Optional[int] = Field(default=None, ge=0)


class StressSuite(BaseModel):
    """A named list of scenarios plus defaults; loaded from YAML/JSON."""

    name: str
    description: str = ""
    defaults: StressDefaults = Field(default_factory=StressDefaults)
    scenarios: List[Scenario] = Field(min_length=1)


def load_suite(path: Path) -> StressSuite:
    """Load a suite file.

    The file may define a ``models`` library; scenario entries reference it with
    ``use: <key>`` and may override any field. Inline models (no ``use``) need a
    ``name``.
    """
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: suite must be a mapping")
    library: Dict[str, Dict[str, Any]] = data.get("models") or {}
    scenarios: List[Dict[str, Any]] = []
    for scenario in data.get("scenarios") or []:
        resolved: List[Dict[str, Any]] = []
        for entry in scenario.get("models") or []:
            entry = dict(entry)
            key = entry.pop("use", None)
            if key is not None:
                if key not in library:
                    raise ValueError(f"{path}: scenario '{scenario.get('name')}' uses unknown model '{key}'")
                merged = {"name": key, **library[key], **entry}
            else:
                merged = entry
            resolved.append(merged)
        scenarios.append({**scenario, "models": resolved})
    return StressSuite(
        name=data.get("name") or path.stem,
        description=data.get("description") or "",
        defaults=StressDefaults(**(data.get("defaults") or {})),
        scenarios=scenarios,
    )


# ---------------------------------------------------------------------------
# Results (what we measured)
# ---------------------------------------------------------------------------


class GpuSample(BaseModel):
    """One telemetry sample (GPU via nvidia-smi, host via psutil)."""

    t: float = Field(description="Seconds since scenario start")
    phase: Phase
    temperature_c: Optional[float] = None
    memory_temperature_c: Optional[float] = None
    power_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    utilization_pct: Optional[float] = None
    memory_utilization_pct: Optional[float] = None
    memory_used_mb: Optional[float] = None
    memory_total_mb: Optional[float] = None
    sm_clock_mhz: Optional[float] = None
    mem_clock_mhz: Optional[float] = None
    fan_pct: Optional[float] = None
    pstate: Optional[str] = None
    throttle_mask: Optional[str] = None
    throttle_reasons: List[str] = Field(default_factory=list)
    host_cpu_pct: Optional[float] = None
    host_ram_used_gb: Optional[float] = None


class GpuSummary(BaseModel):
    """Thermal/power summary of one scenario."""

    samples: int
    temp_start_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    temp_mean_load_c: Optional[float] = None
    temp_end_c: Optional[float] = Field(default=None, description="Last sample, after cooldown")
    time_to_max_s: Optional[float] = Field(default=None, description="Seconds from load start to the hottest sample")
    power_mean_load_w: Optional[float] = None
    power_max_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    utilization_mean_load_pct: Optional[float] = None
    sm_clock_mean_load_mhz: Optional[float] = None
    sm_clock_min_load_mhz: Optional[float] = None
    memory_used_max_mb: Optional[float] = None
    fan_max_pct: Optional[float] = None
    throttle_seconds: Dict[str, float] = Field(default_factory=dict, description="Seconds in load phase per active reason")
    thermal_throttled: bool = False
    host_cpu_mean_load_pct: Optional[float] = None
    host_ram_max_gb: Optional[float] = None


class ModelResult(BaseModel):
    """Load/throughput result for one model server in a scenario."""

    name: str
    kind: ModelKind
    placement: str
    source: str = Field(description="hf_repo or endpoint")
    container: Optional[str] = None
    port: Optional[int] = None
    load_time_s: Optional[float] = None
    offloaded_layers: Optional[str] = Field(default=None, description="e.g. 35/35 from the server log")
    gpu_model_buffer_mb: Optional[float] = Field(default=None, description="Model weight buffers allocated on GPU devices (load_tensors)")
    cpu_model_buffer_mb: Optional[float] = Field(
        default=None,
        description="CPU / CPU_Mapped model buffers (load_tensors). With mmap this is the mapped file region and can cover the whole GGUF even when layers were uploaded to the GPU; use offloaded_layers, gpu_model_buffer_mb and vram_delta_mb for placement.",
    )
    vram_delta_mb: Optional[float] = Field(default=None, description="GPU memory growth while loading")
    build: Optional[str] = None
    requests: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    texts: int = 0
    wall_s: float = 0.0
    requests_per_s: float = 0.0
    prompt_tokens_per_s: float = 0.0
    tokens_per_s: float = Field(default=0.0, description="Chat: completion tok/s; embedding: input tok/s")
    texts_per_s: float = 0.0
    latency_mean_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    server_predicted_per_s: Optional[float] = Field(default=None, description="Mean llama.cpp timings.predicted_per_second")
    error_samples: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


class ScenarioResult(BaseModel):
    name: str
    description: str = ""
    started_at: datetime
    duration_s: int
    warmup_s: int
    cooldown_s: int
    models: List[ModelResult] = Field(default_factory=list)
    gpu: Optional[GpuSummary] = None
    samples: List[GpuSample] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None


class StressResult(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: Literal["gpu_stress"] = "gpu_stress"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    suite: str
    description: str = ""
    hardware: Optional[HardwareInfo] = None
    nvidia_smi_available: bool = True
    defaults: StressDefaults
    scenarios: List[ScenarioResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# nvidia-smi telemetry
# ---------------------------------------------------------------------------

NVIDIA_SMI_FIELDS: Tuple[str, ...] = (
    "temperature.gpu",
    "temperature.memory",
    "power.draw",
    "power.limit",
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "clocks.sm",
    "clocks.mem",
    "fan.speed",
    "pstate",
    "clocks_throttle_reasons.active",
    "clocks_throttle_reasons.hw_thermal_slowdown",
    "clocks_throttle_reasons.sw_thermal_slowdown",
    "clocks_throttle_reasons.sw_power_cap",
    "clocks_throttle_reasons.hw_slowdown",
)
THROTTLE_REASON_NAMES: Dict[str, str] = {
    "clocks_throttle_reasons.hw_thermal_slowdown": "hw_thermal",
    "clocks_throttle_reasons.sw_thermal_slowdown": "sw_thermal",
    "clocks_throttle_reasons.sw_power_cap": "power_cap",
    "clocks_throttle_reasons.hw_slowdown": "hw_slowdown",
}
THERMAL_REASONS = {"hw_thermal", "sw_thermal"}


def _num(value: str) -> Optional[float]:
    value = value.strip().strip("[]")
    if not value or value.upper() in {"N/A", "NA", "-"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_nvidia_smi_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse one ``--format=csv,noheader,nounits`` line for :data:`NVIDIA_SMI_FIELDS`.

    Returns a dict of :class:`GpuSample` fields (without ``t``/``phase``), or
    ``None`` when the line does not have the expected column count.
    """
    parts = [p.strip() for p in line.strip().split(",")]
    if len(parts) != len(NVIDIA_SMI_FIELDS):
        return None
    values = dict(zip(NVIDIA_SMI_FIELDS, parts))
    reasons = [
        short
        for field, short in THROTTLE_REASON_NAMES.items()
        if values.get(field, "").strip().lower() == "active"
    ]
    mask = values["clocks_throttle_reasons.active"].strip() or None
    if mask and mask.upper() in {"N/A", "[N/A]"}:
        mask = None
    return {
        "temperature_c": _num(values["temperature.gpu"]),
        "memory_temperature_c": _num(values["temperature.memory"]),
        "power_w": _num(values["power.draw"]),
        "power_limit_w": _num(values["power.limit"]),
        "utilization_pct": _num(values["utilization.gpu"]),
        "memory_utilization_pct": _num(values["utilization.memory"]),
        "memory_used_mb": _num(values["memory.used"]),
        "memory_total_mb": _num(values["memory.total"]),
        "sm_clock_mhz": _num(values["clocks.sm"]),
        "mem_clock_mhz": _num(values["clocks.mem"]),
        "fan_pct": _num(values["fan.speed"]),
        "pstate": values["pstate"].strip() or None,
        "throttle_mask": mask,
        "throttle_reasons": reasons,
    }


NvidiaSmiReader = Callable[[], Awaitable[Optional[str]]]


async def read_nvidia_smi(gpu_index: int = 0) -> Optional[str]:
    """Run ``nvidia-smi --query-gpu`` for one GPU and return the CSV line."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "-i",
            str(gpu_index),
            f"--query-gpu={','.join(NVIDIA_SMI_FIELDS)}",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
        logger.warning("nvidia-smi failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    lines = [ln for ln in out.decode(errors="replace").splitlines() if ln.strip()]
    return lines[0] if lines else None


class TelemetrySampler:
    """Background task that samples GPU + host telemetry at a fixed interval."""

    def __init__(
        self,
        interval_s: float = 1.0,
        gpu_index: int = 0,
        reader: Optional[NvidiaSmiReader] = None,
        host_stats: Optional[Callable[[], Tuple[Optional[float], Optional[float]]]] = None,
    ) -> None:
        self.interval_s = interval_s
        self.samples: List[GpuSample] = []
        self.phase: Phase = "idle"
        self.nvidia_smi_available = reader is not None or shutil.which("nvidia-smi") is not None
        self._reader: NvidiaSmiReader = reader or (lambda: read_nvidia_smi(gpu_index))
        self._host_stats = host_stats or self._default_host_stats
        self._t0 = time.perf_counter()
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    @staticmethod
    def _default_host_stats() -> Tuple[Optional[float], Optional[float]]:
        try:
            return psutil.cpu_percent(interval=None), round(psutil.virtual_memory().used / 2**30, 2)
        except Exception:  # noqa: BLE001
            return None, None

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self._t0

    async def sample_once(self) -> GpuSample:
        line = await self._reader() if self.nvidia_smi_available else None
        parsed = parse_nvidia_smi_line(line) if line else None
        cpu, ram = self._host_stats()
        sample = GpuSample(t=round(self.elapsed, 2), phase=self.phase, host_cpu_pct=cpu, host_ram_used_gb=ram, **(parsed or {}))
        self.samples.append(sample)
        return sample

    def start(self) -> None:
        self._t0 = time.perf_counter()
        self._host_stats()  # prime psutil.cpu_percent
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                await self.sample_once()
            except Exception as exc:  # noqa: BLE001 - keep sampling
                logger.warning("telemetry sample failed: %s", exc)
            delay = max(0.0, self.interval_s - (time.perf_counter() - started))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    def last_memory_used_mb(self) -> Optional[float]:
        for sample in reversed(self.samples):
            if sample.memory_used_mb is not None:
                return sample.memory_used_mb
        return None


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return round(statistics.fmean(clean), 1) if clean else None


def _max(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return max(clean) if clean else None


def _min(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return min(clean) if clean else None


def summarize_samples(samples: Sequence[GpuSample]) -> Optional[GpuSummary]:
    """Reduce a sample series to the thermal/power summary."""
    if not samples:
        return None
    load = [s for s in samples if s.phase == "load"]
    temps = [s.temperature_c for s in samples]
    temp_max = _max(temps)
    time_to_max: Optional[float] = None
    if temp_max is not None and load:
        load_start = load[0].t
        hottest = next((s for s in samples if s.temperature_c == temp_max and s.t >= load_start), None)
        if hottest is not None:
            time_to_max = round(hottest.t - load_start, 1)
    accumulated: Dict[str, float] = {}
    for prev, cur in zip(load, load[1:]):
        dt = max(0.0, cur.t - prev.t)
        for reason in cur.throttle_reasons:
            accumulated[reason] = accumulated.get(reason, 0.0) + dt
    throttle_seconds = {reason: round(seconds, 1) for reason, seconds in accumulated.items()}
    first_temp = next((t for t in temps if t is not None), None)
    last_temp = next((t for t in reversed(temps) if t is not None), None)
    return GpuSummary(
        samples=len(samples),
        temp_start_c=first_temp,
        temp_max_c=temp_max,
        temp_mean_load_c=_mean([s.temperature_c for s in load]),
        temp_end_c=last_temp,
        time_to_max_s=time_to_max,
        power_mean_load_w=_mean([s.power_w for s in load]),
        power_max_w=_max([s.power_w for s in samples]),
        power_limit_w=_max([s.power_limit_w for s in samples]),
        utilization_mean_load_pct=_mean([s.utilization_pct for s in load]),
        sm_clock_mean_load_mhz=_mean([s.sm_clock_mhz for s in load]),
        sm_clock_min_load_mhz=_min([s.sm_clock_mhz for s in load]),
        memory_used_max_mb=_max([s.memory_used_mb for s in samples]),
        fan_max_pct=_max([s.fan_pct for s in samples]),
        throttle_seconds=throttle_seconds,
        thermal_throttled=any(r in THERMAL_REASONS for r in throttle_seconds),
        host_cpu_mean_load_pct=_mean([s.host_cpu_pct for s in load]),
        host_ram_max_gb=_max([s.host_ram_used_gb for s in samples]),
    )


# ---------------------------------------------------------------------------
# Model servers (Docker llama.cpp or existing endpoint)
# ---------------------------------------------------------------------------


class CommandResult(BaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[Sequence[str]], Awaitable[CommandResult]]


async def run_command(args: Sequence[str], timeout_s: float = 120.0) -> CommandResult:
    """Run a local command (docker CLI) and capture its output."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"command timed out after {timeout_s:.0f}s: {' '.join(args)}")
    return CommandResult(returncode=proc.returncode or 0, stdout=out.decode(errors="replace"), stderr=err.decode(errors="replace"))


def container_name(spec: ModelSpec, port: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", spec.name)
    return f"rag-stress-{safe}-{port}"


def build_docker_command(spec: ModelSpec, defaults: StressDefaults, port: int) -> List[str]:
    """Build the ``docker run`` command for a llama.cpp server with the requested placement."""
    if not spec.hf_repo:
        raise ValueError(f"model '{spec.name}' has no hf_repo; endpoint models are not started")
    on_gpu = spec.placement != "cpu"
    image = spec.image or (defaults.cuda_image if on_gpu else defaults.cpu_image)
    cmd: List[str] = [
        "docker", "run", "-d",
        "--name", container_name(spec, port),
        "--label", STRESS_LABEL,
        "-p", f"{port}:8080",
        "-v", f"{defaults.volume}:/root/.cache",
    ]
    if on_gpu:
        cmd += ["--gpus", defaults.gpus]
    cmd += [
        image,
        "-hf", spec.hf_repo,
        "--host", "0.0.0.0",
        "--port", "8080",
        "-lv", str(LOG_VERBOSITY),  # level 4 prints load_tensors (layers/buffers per device)
        "-c", str(spec.ctx),
        "-np", str(spec.slots),
        "-b", str(spec.batch),
        "-ub", str(spec.ubatch),
    ]
    if spec.kind == "embedding":
        cmd.append("--embeddings")
    else:
        cmd.append("--no-mmproj")
    if spec.threads:
        cmd += ["-t", str(spec.threads)]
    if spec.placement == "gpu":
        cmd += ["-ngl", str(spec.n_gpu_layers if spec.n_gpu_layers is not None else 999)]
    elif spec.placement == "cpu":
        cmd += ["-ngl", "0"]
    else:  # split: let llama.cpp fit layers into free VRAM unless pinned
        cmd += ["--fit", "on"]
        if spec.n_gpu_layers is not None:
            cmd += ["-ngl", str(spec.n_gpu_layers)]
    if spec.cpu_moe:
        cmd.append("--cpu-moe")
    elif spec.n_cpu_moe:
        cmd += ["--n-cpu-moe", str(spec.n_cpu_moe)]
    cmd += spec.extra_args
    return cmd


LOG_VERBOSITY = 4
OFFLOAD_RE = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")
BUFFER_RE = re.compile(r"load_tensors:\s+(\S+) model buffer size =\s+([0-9.]+) MiB")


def parse_offloaded_layers(log: str) -> Optional[str]:
    match = OFFLOAD_RE.search(log)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def parse_model_buffers(log: str) -> Tuple[Optional[float], Optional[float]]:
    """Return ``(gpu_mb, cpu_mb)`` model weight buffers from ``load_tensors`` log lines.

    Devices named ``CPU*`` (``CPU``, ``CPU_Mapped``, ``CPU_REPACK``) count as host
    buffers, everything else (``CUDA0``, ``Vulkan0``, ...) as GPU memory. Note that
    ``CPU_Mapped`` is the memory-mapped file region; for large models llama.cpp
    reports the whole GGUF there even when the layers were uploaded to the GPU,
    so the GPU buffer and ``offloaded N/M layers`` are the placement signal.
    """
    gpu = cpu = 0.0
    seen = False
    for device, size in BUFFER_RE.findall(log):
        seen = True
        if device.upper().startswith("CPU"):
            cpu += float(size)
        else:
            gpu += float(size)
    if not seen:
        return None, None
    return round(gpu, 1), round(cpu, 1)


class ModelServer:
    """Lifecycle of one model server (Docker container or external endpoint)."""

    def __init__(
        self,
        spec: ModelSpec,
        defaults: StressDefaults,
        port: int,
        http: httpx.AsyncClient,
        command_runner: CommandRunner = run_command,
    ) -> None:
        self.spec = spec
        self.defaults = defaults
        self.port = port
        self.http = http
        self.run = command_runner
        self.managed = spec.endpoint is None
        self.base_url = (spec.endpoint or f"http://127.0.0.1:{port}").rstrip("/")
        self.container = container_name(spec, port) if self.managed else None
        self.started = False

    async def start(self) -> None:
        if not self.managed:
            return
        await self.run(["docker", "rm", "-f", self.container or ""])  # leftovers from an aborted run
        result = await self.run(build_docker_command(self.spec, self.defaults, self.port))
        if result.returncode != 0:
            raise RuntimeError(f"docker run failed for {self.spec.name}: {result.stderr.strip() or result.stdout.strip()}")
        self.started = True

    async def _container_status(self) -> str:
        result = await self.run(["docker", "inspect", "-f", "{{.State.Status}}", self.container or ""])
        return result.stdout.strip() if result.returncode == 0 else "missing"

    async def is_ready(self) -> bool:
        for path in ("/health", "/v1/models"):
            try:
                response = await self.http.get(f"{self.base_url}{path}", timeout=5.0)
            except httpx.HTTPError:
                continue
            if response.status_code == 200:
                return True
        return False

    async def wait_ready(self, progress: Optional[Callable[[str], None]] = None) -> float:
        """Poll until the server answers; returns the load time in seconds."""
        started = time.perf_counter()
        next_report = 30.0
        while True:
            if await self.is_ready():
                return round(time.perf_counter() - started, 1)
            elapsed = time.perf_counter() - started
            if self.managed:
                status = await self._container_status()
                if status != "running":
                    logs = await self.logs(tail=15)
                    raise RuntimeError(f"{self.spec.name}: container {status} before becoming ready:\n{logs}")
            if elapsed > self.spec.load_timeout_s:
                raise RuntimeError(f"{self.spec.name}: not ready after {self.spec.load_timeout_s}s")
            if progress and elapsed >= next_report:
                progress(f"  {self.spec.name}: still loading ({elapsed:.0f}s)")
                next_report += 30.0
            await asyncio.sleep(2.0)

    async def logs(self, tail: int = 400) -> str:
        if not self.managed:
            return ""
        result = await self.run(["docker", "logs", "--tail", str(tail), self.container or ""])
        return result.stdout + result.stderr

    async def props(self) -> Optional[Dict[str, Any]]:
        try:
            response = await self.http.get(f"{self.base_url}/props", timeout=5.0)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def stop(self) -> None:
        if self.managed and self.started:
            await self.run(["docker", "rm", "-f", self.container or ""])
            self.started = False


ServerFactory = Callable[[ModelSpec, StressDefaults, int, httpx.AsyncClient], ModelServer]


# ---------------------------------------------------------------------------
# Load generation
# ---------------------------------------------------------------------------

_VOCAB = (
    "Mietvertrag Kündigungsfrist Vertragspartei Laufzeit Zahlung Instandhaltung Nebenkosten Eigentümer Wohnung "
    "Geschäftsraum Frist Monat Jahr Schriftform Kaution Verlängerung Mieter Vermieter Klausel Haftung Versicherung "
    "Betriebskosten Abrechnung Protokoll Übergabe Mängel Rüge Gewährleistung Zinsen Mahnung Verzug Rechnung "
    "contract notice period tenant landlord clause liability insurance maintenance invoice payment schedule "
    "renewal deposit inspection damages warranty interest reminder default statement handover records policy "
    "pipeline inverter transformer substation voltage current frequency grid storage battery module cable"
).split()


def synthetic_text(words: int, seed: int) -> str:
    """Deterministic word salad; different seeds give different prompts (defeats prompt caching)."""
    rng = random.Random(seed)
    out: List[str] = []
    for i in range(words):
        word = rng.choice(_VOCAB)
        if i and rng.random() < 0.08:
            out[-1] += "."
            word = word.capitalize()
        out.append(word)
    return " ".join(out) + "."


class LoadStats(BaseModel):
    requests: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    texts: int = 0
    latencies_ms: List[float] = Field(default_factory=list)
    server_predicted_per_s: List[float] = Field(default_factory=list)
    error_samples: List[str] = Field(default_factory=list)
    wall_s: float = 0.0


class LoadGenerator:
    """Concurrent synthetic load against one server for a fixed duration."""

    def __init__(self, spec: ModelSpec, base_url: str, http: httpx.AsyncClient, seed_base: int = 0) -> None:
        self.spec = spec
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.seed_base = seed_base
        self._counter = 0

    def _next_seed(self) -> int:
        self._counter += 1
        return self.seed_base + self._counter * 7919

    def build_request(self) -> Tuple[str, Dict[str, Any], int]:
        """Return ``(path, json_body, texts_in_request)`` for the next request."""
        spec = self.spec
        if spec.kind == "embedding":
            seed = self._next_seed()
            texts = [synthetic_text(spec.text_words, seed + i) for i in range(spec.batch_size)]
            return "/v1/embeddings", {"model": spec.request_model, "input": texts}, len(texts)
        prompt = synthetic_text(spec.prompt_words, self._next_seed())
        body = {
            "model": spec.request_model,
            "messages": [{"role": "user", "content": f"Summarise the following notes in detail:\n\n{prompt}"}],
            "max_tokens": spec.max_tokens,
            "temperature": 0.8,
            "stream": False,
        }
        return "/v1/chat/completions", body, 1

    async def _one(self, stats: LoadStats) -> None:
        path, body, n_texts = self.build_request()
        started = time.perf_counter()
        try:
            response = await self.http.post(f"{self.base_url}{path}", json=body, timeout=self.spec.request_timeout_s)
            elapsed_ms = (time.perf_counter() - started) * 1000
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - counted, not raised
            stats.errors += 1
            if len(stats.error_samples) < 5:
                stats.error_samples.append(str(exc)[:300])
            return
        usage = data.get("usage") or {}
        stats.requests += 1
        stats.texts += n_texts
        stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        stats.completion_tokens += int(usage.get("completion_tokens") or 0)
        stats.latencies_ms.append(elapsed_ms)
        timings = data.get("timings") or {}
        if isinstance(timings, dict) and timings.get("predicted_per_second"):
            stats.server_predicted_per_s.append(float(timings["predicted_per_second"]))

    async def run(self, duration_s: float) -> LoadStats:
        """Keep ``concurrency`` requests in flight until the deadline passes."""
        stats = LoadStats()
        deadline = time.perf_counter() + duration_s
        started = time.perf_counter()

        async def worker() -> None:
            while time.perf_counter() < deadline:
                await self._one(stats)
                await asyncio.sleep(0)  # let other generators/telemetry run even on instant transports

        await asyncio.gather(*(worker() for _ in range(self.spec.concurrency)))
        stats.wall_s = round(time.perf_counter() - started, 2)
        return stats


def finish_model_result(result: ModelResult, stats: LoadStats) -> ModelResult:
    """Fold load statistics into a model result."""
    wall = stats.wall_s or 1e-9
    result.requests = stats.requests
    result.errors = stats.errors
    result.prompt_tokens = stats.prompt_tokens
    result.completion_tokens = stats.completion_tokens
    result.texts = stats.texts
    result.wall_s = stats.wall_s
    result.requests_per_s = round(stats.requests / wall, 2)
    result.prompt_tokens_per_s = round(stats.prompt_tokens / wall, 1)
    result.texts_per_s = round(stats.texts / wall, 2)
    if result.kind == "chat":
        result.tokens_per_s = round(stats.completion_tokens / wall, 1)
    else:
        result.tokens_per_s = round(stats.prompt_tokens / wall, 1)
    if stats.latencies_ms:
        ordered = sorted(stats.latencies_ms)
        idx = max(0, min(len(ordered) - 1, round(0.95 * len(ordered)) - 1))
        result.latency_mean_ms = round(statistics.fmean(ordered), 1)
        result.latency_p95_ms = round(ordered[idx], 1)
    if stats.server_predicted_per_s:
        result.server_predicted_per_s = round(statistics.fmean(stats.server_predicted_per_s), 1)
    result.error_samples = stats.error_samples
    if stats.requests == 0:
        result.success = False
        result.error = stats.error_samples[0] if stats.error_samples else "no successful requests"
    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))


class StressRunner:
    """Executes a :class:`StressSuite` scenario by scenario."""

    def __init__(
        self,
        *,
        http_factory: Callable[[], httpx.AsyncClient] = default_http_client,
        command_runner: CommandRunner = run_command,
        sampler_factory: Optional[Callable[[StressDefaults], TelemetrySampler]] = None,
        server_factory: Optional[ServerFactory] = None,
        hardware_collector: Callable[[], Awaitable[Optional[HardwareInfo]]] = collect_hardware,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._http_factory = http_factory
        self._run = command_runner
        self._sampler_factory = sampler_factory or (
            lambda d: TelemetrySampler(interval_s=d.sample_interval_s, gpu_index=d.gpu_index)
        )
        self._server_factory: ServerFactory = server_factory or (
            lambda spec, defaults, port, http: ModelServer(spec, defaults, port, http, command_runner=self._run)
        )
        self._collect_hardware = hardware_collector
        self._progress = progress or (lambda msg: None)

    async def run(
        self,
        suite: StressSuite,
        only: Optional[Sequence[str]] = None,
        duration_s: Optional[int] = None,
        cooldown_s: Optional[int] = None,
    ) -> StressResult:
        selected = [s for s in suite.scenarios if not only or s.name in set(only)]
        if only and not selected:
            raise ValueError(f"no scenario matches {list(only)}; available: {[s.name for s in suite.scenarios]}")
        result = StressResult(suite=suite.name, description=suite.description, defaults=suite.defaults)
        try:
            result.hardware = await self._collect_hardware()
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning("hardware snapshot failed: %s", exc)
        async with self._http_factory() as http:
            for scenario in selected:
                self._progress(f"scenario {scenario.name}: {len(scenario.models)} model(s)")
                scenario_result = await self._run_scenario(scenario, suite.defaults, http, duration_s, cooldown_s)
                result.nvidia_smi_available = result.nvidia_smi_available and not any(
                    "nvidia-smi" in w for w in scenario_result.warnings
                )
                result.scenarios.append(scenario_result)
                status = "ok" if scenario_result.success else f"FAILED: {scenario_result.error}"
                self._progress(f"scenario {scenario.name}: {status}")
        return result

    async def _run_scenario(
        self,
        scenario: Scenario,
        defaults: StressDefaults,
        http: httpx.AsyncClient,
        duration_override: Optional[int],
        cooldown_override: Optional[int],
    ) -> ScenarioResult:
        duration = duration_override or scenario.duration_s or defaults.duration_s
        warmup = scenario.warmup_s if scenario.warmup_s is not None else defaults.warmup_s
        cooldown = cooldown_override if cooldown_override is not None else (
            scenario.cooldown_s if scenario.cooldown_s is not None else defaults.cooldown_s
        )
        out = ScenarioResult(
            name=scenario.name,
            description=scenario.description,
            started_at=datetime.now(timezone.utc),
            duration_s=duration,
            warmup_s=warmup,
            cooldown_s=cooldown,
        )
        sampler = self._sampler_factory(defaults)
        if not sampler.nvidia_smi_available:
            out.warnings.append("nvidia-smi not found: GPU telemetry unavailable, only host CPU/RAM sampled")
        sampler.start()
        servers: List[ModelServer] = []
        try:
            sampler.phase = "idle"
            await asyncio.sleep(defaults.baseline_s)
            sampler.phase = "loading"
            for index, spec in enumerate(scenario.models):
                port = defaults.base_port + index
                server = self._server_factory(spec, defaults, port, http)
                servers.append(server)
                model_result = ModelResult(
                    name=spec.name,
                    kind=spec.kind,
                    placement=spec.placement_label,
                    source=spec.hf_repo or spec.endpoint or "",
                    container=server.container,
                    port=port if server.managed else None,
                )
                out.models.append(model_result)
                mem_before = sampler.last_memory_used_mb()
                self._progress(f"  starting {spec.name} ({spec.placement_label}) on port {port}")
                await server.start()
                model_result.load_time_s = await server.wait_ready(self._progress)
                await sampler.sample_once()
                mem_after = sampler.last_memory_used_mb()
                if mem_before is not None and mem_after is not None:
                    model_result.vram_delta_mb = round(mem_after - mem_before, 0)
                log = await server.logs()
                model_result.offloaded_layers = parse_offloaded_layers(log)
                model_result.gpu_model_buffer_mb, model_result.cpu_model_buffer_mb = parse_model_buffers(log)
                props = await server.props()
                if props:
                    model_result.build = str(props.get("build_info") or "") or None
                placement_note = ""
                if model_result.gpu_model_buffer_mb is not None:
                    placement_note = f", weights {model_result.gpu_model_buffer_mb:.0f} MB GPU / {model_result.cpu_model_buffer_mb:.0f} MB RAM"
                self._progress(
                    f"  {spec.name} ready in {model_result.load_time_s}s"
                    + (f", layers on GPU {model_result.offloaded_layers}" if model_result.offloaded_layers else "")
                    + placement_note
                    + (f", VRAM +{model_result.vram_delta_mb:.0f} MB" if model_result.vram_delta_mb is not None else "")
                )

            generators = [
                LoadGenerator(spec, server.base_url, http, seed_base=index * 100_000)
                for index, (spec, server) in enumerate(zip(scenario.models, servers))
            ]
            if warmup:
                sampler.phase = "warmup"
                self._progress(f"  warmup {warmup}s")
                await asyncio.gather(*(g.run(warmup) for g in generators))
            sampler.phase = "load"
            self._progress(f"  load {duration}s")
            stats_list = await asyncio.gather(*(g.run(duration) for g in generators))
            for model_result, stats in zip(out.models, stats_list):
                finish_model_result(model_result, stats)
                if model_result.errors:
                    out.warnings.append(f"{model_result.name}: {model_result.errors} failed request(s)")
            out.success = all(m.success for m in out.models)
            if not out.success:
                out.error = "; ".join(f"{m.name}: {m.error}" for m in out.models if not m.success)
        except Exception as exc:  # noqa: BLE001 - scenario boundary
            logger.exception("scenario %s failed", scenario.name)
            out.success = False
            out.error = str(exc)
            for model_result in out.models:
                if model_result.wall_s == 0 and model_result.success:  # never reached the load phase
                    model_result.success = False
                    model_result.error = str(exc).splitlines()[0]
        finally:
            for server in servers:
                try:
                    await server.stop()
                except Exception as exc:  # noqa: BLE001
                    out.warnings.append(f"stop {server.spec.name}: {exc}")
            if cooldown:
                sampler.phase = "cooldown"
                self._progress(f"  cooldown {cooldown}s")
                await asyncio.sleep(cooldown)
            await sampler.stop()
            out.samples = sampler.samples
            out.gpu = summarize_samples(sampler.samples)
            if out.gpu and out.gpu.thermal_throttled:
                out.warnings.append(f"thermal throttling observed: {out.gpu.throttle_seconds}")
        return out


async def cleanup_stress_containers(command_runner: CommandRunner = run_command) -> List[str]:
    """Remove any leftover containers created by this module (by label)."""
    listing = await command_runner(["docker", "ps", "-aq", "--filter", f"label={STRESS_LABEL}"])
    ids = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    if ids:
        await command_runner(["docker", "rm", "-f", *ids])
    return ids
