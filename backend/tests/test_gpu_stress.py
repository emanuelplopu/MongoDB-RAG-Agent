"""Tests for the GPU stress test (service + CLI).

Docker, nvidia-smi and the model servers are replaced by fakes: a recording
command runner, an ``httpx.MockTransport`` that answers like a llama.cpp server
and a telemetry reader that emits synthetic nvidia-smi CSV lines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx
import pytest

from backend.services import gpu_stress as gs
from backend.services.gpu_stress import (
    CommandResult,
    GpuSample,
    LoadGenerator,
    ModelSpec,
    Scenario,
    StressDefaults,
    StressRunner,
    StressSuite,
    TelemetrySampler,
    build_docker_command,
    finish_model_result,
    load_suite,
    parse_nvidia_smi_line,
    parse_model_buffers,
    parse_offloaded_layers,
    summarize_samples,
    synthetic_text,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def smi_line(temp: float, mem_used: float = 4400, power: float = 69.5, power_cap: bool = False, thermal: bool = False) -> str:
    """One nvidia-smi CSV line in NVIDIA_SMI_FIELDS order."""
    return ", ".join(
        [
            f"{temp:.0f}", "[N/A]", f"{power:.2f}", "70.00", "93", "41", f"{mem_used:.0f}", "16311", "2070", "7001",
            "38", "P0", "0x0000000000000004",
            "Not Active", "Active" if thermal else "Not Active", "Active" if power_cap else "Not Active", "Not Active",
        ]
    )


class FakeSmi:
    """Emits a rising temperature; VRAM jumps once the first server has 'started'."""

    def __init__(self) -> None:
        self.calls = 0
        self.loaded = False

    async def __call__(self) -> Optional[str]:
        self.calls += 1
        temp = min(60 + self.calls, 88)
        return smi_line(temp, mem_used=4400 if self.loaded else 1200, power_cap=self.calls > 3)


class FakeDocker:
    """Records docker CLI calls and answers like a healthy (or dying) container."""

    def __init__(self, status: str = "running", smi: Optional[FakeSmi] = None) -> None:
        self.calls: List[List[str]] = []
        self.status = status
        self.smi = smi

    async def __call__(self, args: Sequence[str]) -> CommandResult:
        args = list(args)
        self.calls.append(args)
        verb = args[1] if len(args) > 1 else ""
        if verb == "run":
            if self.smi:
                self.smi.loaded = True
            return CommandResult(returncode=0, stdout="0123456789ab\n")
        if verb == "inspect":
            return CommandResult(returncode=0, stdout=f"{self.status}\n")
        if verb == "logs":
            return CommandResult(
                returncode=0,
                stdout=(
                    "load_tensors: offloaded 35/35 layers to GPU\n"
                    "load_tensors:   CPU_Mapped model buffer size =   136.00 MiB\n"
                    "load_tensors:        CUDA0 model buffer size =   144.44 MiB\n"
                    "srv  llama_server: model loaded\n"
                ),
            )
        if verb == "ps":
            return CommandResult(returncode=0, stdout="aaa\nbbb\n")
        return CommandResult(returncode=0)


class FakeLlamaServer:
    """httpx handler for /health, /props, /v1/chat/completions and /v1/embeddings."""

    def __init__(self, *, healthy: bool = True, fail_requests: bool = False) -> None:
        self.healthy = healthy
        self.fail_requests = fail_requests
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200 if self.healthy else 503, json={"status": "ok" if self.healthy else "loading"})
        if path == "/v1/models":
            return httpx.Response(200 if self.healthy else 503, json={"data": []})
        if path == "/props":
            return httpx.Response(200, json={"build_info": "b11541-test", "total_slots": 4})
        if self.fail_requests:
            return httpx.Response(500, text="boom")
        body = json.loads(request.content)
        if path == "/v1/chat/completions":
            words = len(body["messages"][0]["content"].split())
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "summary"}}],
                    "usage": {"prompt_tokens": words, "completion_tokens": body["max_tokens"]},
                    "timings": {"predicted_per_second": 42.0, "prompt_per_second": 900.0},
                },
            )
        if path == "/v1/embeddings":
            n = len(body["input"])
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.0] * 8, "index": i} for i in range(n)], "usage": {"prompt_tokens": n * 150}},
            )
        return httpx.Response(404, text="unknown")


def mock_http(handler: FakeLlamaServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def chat_spec(**overrides: Any) -> ModelSpec:
    base: Dict[str, Any] = {"name": "gemma4-e2b", "kind": "chat", "hf_repo": "ggml-org/gemma-4-E2B-it-GGUF:Q8_0", "concurrency": 2}
    base.update(overrides)
    return ModelSpec(**base)


def embed_spec(**overrides: Any) -> ModelSpec:
    base: Dict[str, Any] = {"name": "embeddinggemma-2", "kind": "embedding", "hf_repo": "ggml-org/embeddinggemma-2-GGUF:Q8_0", "batch_size": 4, "concurrency": 2}
    base.update(overrides)
    return ModelSpec(**base)


def fast_defaults(**overrides: Any) -> StressDefaults:
    base: Dict[str, Any] = {"duration_s": 5, "warmup_s": 0, "cooldown_s": 0, "baseline_s": 0, "sample_interval_s": 0.01}
    base.update(overrides)
    return StressDefaults(**base)


# ---------------------------------------------------------------------------
# nvidia-smi parsing and summary
# ---------------------------------------------------------------------------


def test_parse_nvidia_smi_line_handles_na_and_throttle_flags():
    parsed = parse_nvidia_smi_line(smi_line(87, power_cap=True, thermal=True))
    assert parsed is not None
    assert parsed["temperature_c"] == 87.0
    assert parsed["memory_temperature_c"] is None  # "[N/A]"
    assert parsed["power_w"] == 69.5 and parsed["power_limit_w"] == 70.0
    assert parsed["memory_used_mb"] == 4400.0 and parsed["sm_clock_mhz"] == 2070.0
    assert parsed["pstate"] == "P0" and parsed["throttle_mask"] == "0x0000000000000004"
    assert sorted(parsed["throttle_reasons"]) == ["power_cap", "sw_thermal"]


def test_parse_nvidia_smi_line_rejects_wrong_column_count():
    assert parse_nvidia_smi_line("66, 19.0, 70") is None
    assert parse_nvidia_smi_line("") is None


def test_summarize_samples_thermal_and_throttle_seconds():
    samples = [
        GpuSample(t=0.0, phase="idle", temperature_c=60, power_w=18, power_limit_w=70, memory_used_mb=1200, sm_clock_mhz=1200, host_cpu_pct=5, host_ram_used_gb=10),
        GpuSample(t=1.0, phase="load", temperature_c=75, power_w=70, power_limit_w=70, memory_used_mb=4400, sm_clock_mhz=2100, utilization_pct=90, host_cpu_pct=30, host_ram_used_gb=12, throttle_reasons=["power_cap"]),
        GpuSample(t=2.0, phase="load", temperature_c=88, power_w=70, power_limit_w=70, memory_used_mb=4400, sm_clock_mhz=2000, utilization_pct=94, host_cpu_pct=40, host_ram_used_gb=12, throttle_reasons=["power_cap", "sw_thermal"]),
        GpuSample(t=3.0, phase="load", temperature_c=88, power_w=69, power_limit_w=70, memory_used_mb=4400, sm_clock_mhz=1900, utilization_pct=92, host_cpu_pct=50, host_ram_used_gb=13, throttle_reasons=["power_cap"]),
        GpuSample(t=4.0, phase="cooldown", temperature_c=70, power_w=20, power_limit_w=70, memory_used_mb=1200, sm_clock_mhz=1200, host_cpu_pct=5, host_ram_used_gb=11),
    ]
    summary = summarize_samples(samples)
    assert summary is not None
    assert summary.samples == 5
    assert summary.temp_start_c == 60 and summary.temp_max_c == 88 and summary.temp_end_c == 70
    assert summary.time_to_max_s == 1.0  # hottest first reached at t=2, load started at t=1
    assert summary.temp_mean_load_c == pytest.approx(83.7, abs=0.1)
    assert summary.power_mean_load_w == pytest.approx(69.7, abs=0.1) and summary.power_max_w == 70 and summary.power_limit_w == 70
    assert summary.utilization_mean_load_pct == 92.0
    assert summary.sm_clock_mean_load_mhz == 2000.0 and summary.sm_clock_min_load_mhz == 1900
    assert summary.memory_used_max_mb == 4400
    assert summary.throttle_seconds == {"power_cap": 2.0, "sw_thermal": 1.0}
    assert summary.thermal_throttled is True
    assert summary.host_cpu_mean_load_pct == 40.0 and summary.host_ram_max_gb == 13


def test_summarize_samples_empty():
    assert summarize_samples([]) is None


# ---------------------------------------------------------------------------
# Docker command construction and suite loading
# ---------------------------------------------------------------------------


def test_build_docker_command_gpu_chat():
    cmd = build_docker_command(chat_spec(), StressDefaults(), 11091)
    assert cmd[:3] == ["docker", "run", "-d"]
    assert "--gpus" in cmd and cmd[cmd.index("--gpus") + 1] == "all"
    assert "ghcr.io/ggml-org/llama.cpp:server-cuda" in cmd
    assert cmd[cmd.index("-hf") + 1] == "ggml-org/gemma-4-E2B-it-GGUF:Q8_0"
    assert cmd[cmd.index("-ngl") + 1] == "999"
    assert cmd[cmd.index("-lv") + 1] == "4"
    assert "--no-mmproj" in cmd and "--embeddings" not in cmd and "--fit" not in cmd
    assert cmd[cmd.index("-p") + 1] == "11091:8080"
    assert cmd[cmd.index("-v") + 1] == "recallhub_embedder_models:/root/.cache"
    assert cmd[cmd.index("--name") + 1] == "rag-stress-gemma4-e2b-11091"


def test_build_docker_command_cpu_uses_cpu_image_and_threads():
    cmd = build_docker_command(chat_spec(placement="cpu", threads=16), StressDefaults(), 11092)
    assert "--gpus" not in cmd
    assert "ghcr.io/ggml-org/llama.cpp:server" in cmd and "server-cuda" not in " ".join(cmd)
    assert cmd[cmd.index("-ngl") + 1] == "0"
    assert cmd[cmd.index("-t") + 1] == "16"


def test_build_docker_command_split_and_cpu_moe():
    split = build_docker_command(chat_spec(placement="split"), StressDefaults(), 11093)
    assert split[split.index("--fit") + 1] == "on" and "-ngl" not in split
    pinned = build_docker_command(chat_spec(placement="split", n_gpu_layers=20), StressDefaults(), 11093)
    assert pinned[pinned.index("-ngl") + 1] == "20"
    moe = build_docker_command(chat_spec(cpu_moe=True), StressDefaults(), 11094)
    assert "--cpu-moe" in moe
    partial = build_docker_command(chat_spec(n_cpu_moe=12, extra_args=["--reasoning", "off"]), StressDefaults(), 11094)
    assert partial[partial.index("--n-cpu-moe") + 1] == "12" and partial[-2:] == ["--reasoning", "off"]


def test_build_docker_command_embedding_flags():
    cmd = build_docker_command(embed_spec(batch=4096, ubatch=4096), StressDefaults(), 11091)
    assert "--embeddings" in cmd and "--no-mmproj" not in cmd
    assert cmd[cmd.index("-b") + 1] == "4096" and cmd[cmd.index("-ub") + 1] == "4096"


def test_model_spec_requires_source_and_labels_placement():
    with pytest.raises(ValueError):
        ModelSpec(name="x", kind="chat")
    assert chat_spec(cpu_moe=True).placement_label == "gpu+cpu-moe"
    assert chat_spec(placement="split", n_cpu_moe=4).placement_label == "split+cpu-moe:4"
    endpoint = ModelSpec(name="ollama-26b", kind="chat", endpoint="http://localhost:11434", model_id="gemma4:26b")
    assert endpoint.request_model == "gemma4:26b"


def test_load_suite_resolves_model_library(tmp_path: Path):
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(
        """
name: demo
defaults: {duration_s: 30, base_port: 12000}
models:
  e2b: {kind: chat, hf_repo: ggml-org/gemma-4-E2B-it-GGUF:Q8_0, max_tokens: 64}
scenarios:
  - name: alone
    models: [{use: e2b, placement: gpu}]
  - name: mixed
    duration_s: 15
    models:
      - {use: e2b, placement: cpu, threads: 8, max_tokens: 32}
      - {name: ext, kind: embedding, endpoint: http://localhost:11434, model_id: nomic}
""",
        encoding="utf-8",
    )
    suite = load_suite(suite_file)
    assert suite.name == "demo" and suite.defaults.duration_s == 30 and suite.defaults.base_port == 12000
    assert [s.name for s in suite.scenarios] == ["alone", "mixed"]
    alone = suite.scenarios[0].models[0]
    assert alone.name == "e2b" and alone.max_tokens == 64 and alone.placement == "gpu"
    cpu, ext = suite.scenarios[1].models
    assert cpu.placement == "cpu" and cpu.threads == 8 and cpu.max_tokens == 32 and cpu.hf_repo.endswith(":Q8_0")
    assert ext.endpoint == "http://localhost:11434" and ext.request_model == "nomic"
    assert suite.scenarios[1].duration_s == 15


def test_load_suite_unknown_reference(tmp_path: Path):
    suite_file = tmp_path / "bad.yaml"
    suite_file.write_text("name: bad\nscenarios:\n  - name: s\n    models: [{use: nope}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown model 'nope'"):
        load_suite(suite_file)


def test_default_suite_loads_and_covers_requested_models():
    from backend.cli.stress_commands import DEFAULT_SUITE, dry_run_lines

    suite = load_suite(DEFAULT_SUITE)
    names = {s.name for s in suite.scenarios}
    assert {"embeddinggemma-2-gpu", "gemma4-e2b-gpu", "gemma4-e4b-gpu", "gemma4-26b-split", "gemma4-26b-cpu-moe", "mixed-gpu-and-ram"} <= names
    mixed = next(s for s in suite.scenarios if s.name == "mixed-gpu-and-ram")
    assert {m.placement for m in mixed.models} == {"gpu", "cpu"}
    lines = dry_run_lines(suite)
    assert any("--cpu-moe" in line for line in lines) and any("--fit on" in line for line in lines)


def test_parse_offloaded_layers():
    assert parse_offloaded_layers("load_tensors: offloaded 12/35 layers to GPU") == "12/35"
    assert parse_offloaded_layers("nothing here") is None
    log = """load_tensors:   CPU_Mapped model buffer size =   136.00 MiB
load_tensors:        CUDA0 model buffer size =   144.44 MiB
load_tensors:        CUDA1 model buffer size =    10.00 MiB
"""
    assert parse_model_buffers(log) == (154.4, 136.0)
    assert parse_model_buffers("no buffers") == (None, None)


# ---------------------------------------------------------------------------
# Load generation
# ---------------------------------------------------------------------------


def test_synthetic_text_is_deterministic_and_seed_dependent():
    assert synthetic_text(50, 1) == synthetic_text(50, 1)
    assert synthetic_text(50, 1) != synthetic_text(50, 2)
    assert len(synthetic_text(50, 3).split()) == 50


async def test_load_generator_chat_counts_tokens_and_latency():
    server = FakeLlamaServer()
    async with mock_http(server) as http:
        gen = LoadGenerator(chat_spec(max_tokens=16, prompt_words=40), "http://model:8080", http)
        stats = await gen.run(0.2)
    assert stats.requests > 0 and stats.errors == 0
    assert stats.completion_tokens == stats.requests * 16
    assert stats.prompt_tokens > stats.requests * 40  # prompt words + instruction
    assert len(stats.latencies_ms) == stats.requests
    assert stats.server_predicted_per_s and stats.server_predicted_per_s[0] == 42.0
    bodies = [json.loads(r.content) for r in server.requests]
    prompts = {b["messages"][0]["content"] for b in bodies}
    assert len(prompts) == len(bodies)  # every request has a distinct prompt (no prompt-cache hits)
    assert all(b["model"] == "gemma4-e2b" and b["stream"] is False for b in bodies)


async def test_load_generator_embedding_and_result_folding():
    server = FakeLlamaServer()
    async with mock_http(server) as http:
        gen = LoadGenerator(embed_spec(batch_size=4, text_words=20), "http://model:8080/", http)
        stats = await gen.run(0.2)
    assert stats.texts == stats.requests * 4 and stats.prompt_tokens == stats.requests * 600
    result = finish_model_result(gs.ModelResult(name="e", kind="embedding", placement="gpu", source="x"), stats)
    assert result.success and result.texts_per_s > 0 and result.tokens_per_s == result.prompt_tokens_per_s
    assert result.latency_p95_ms is not None and result.latency_mean_ms is not None


async def test_load_generator_counts_errors_without_raising():
    server = FakeLlamaServer(fail_requests=True)
    async with mock_http(server) as http:
        stats = await LoadGenerator(chat_spec(concurrency=1), "http://model:8080", http).run(0.1)
    assert stats.requests == 0 and stats.errors > 0 and stats.error_samples[0].startswith("HTTP 500")
    result = finish_model_result(gs.ModelResult(name="c", kind="chat", placement="gpu", source="x"), stats)
    assert result.success is False and "HTTP 500" in (result.error or "")


# ---------------------------------------------------------------------------
# Runner (end to end with fakes)
# ---------------------------------------------------------------------------


def make_runner(docker: FakeDocker, server: FakeLlamaServer, smi: FakeSmi, progress: Optional[List[str]] = None) -> StressRunner:
    async def no_hardware() -> None:
        return None

    return StressRunner(
        http_factory=lambda: mock_http(server),
        command_runner=docker,
        sampler_factory=lambda d: TelemetrySampler(interval_s=d.sample_interval_s, reader=smi, host_stats=lambda: (12.5, 40.0)),
        hardware_collector=no_hardware,
        progress=(progress.append if progress is not None else None),
    )


async def test_runner_end_to_end_mixed_scenario():
    smi, docker, server, progress = FakeSmi(), FakeDocker(smi=None), FakeLlamaServer(), []
    docker.smi = smi
    suite = StressSuite(
        name="t",
        defaults=fast_defaults(),
        scenarios=[
            Scenario(
                name="mixed",
                description="gpu + ram",
                models=[embed_spec(), chat_spec(placement="cpu", threads=4)],
            )
        ],
    )
    result = await make_runner(docker, server, smi, progress).run(suite, duration_s=1)

    assert result.kind == "gpu_stress" and result.suite == "t" and result.nvidia_smi_available
    assert len(result.scenarios) == 1
    scenario = result.scenarios[0]
    assert scenario.success, scenario.error
    assert scenario.duration_s == 1
    assert [m.name for m in scenario.models] == ["embeddinggemma-2", "gemma4-e2b"]
    embed, chat = scenario.models
    assert embed.placement == "gpu" and chat.placement == "cpu"
    assert embed.port == 11091 and chat.port == 11092
    assert embed.container == "rag-stress-embeddinggemma-2-11091"
    assert embed.offloaded_layers == "35/35" and embed.build == "b11541-test"
    assert (embed.gpu_model_buffer_mb, embed.cpu_model_buffer_mb) == (144.4, 136.0)
    assert embed.vram_delta_mb == 3200  # 1200 -> 4400 after the first container started
    assert embed.requests > 0 and chat.requests > 0 and embed.errors == 0
    assert chat.tokens_per_s > 0 and embed.texts_per_s > 0
    # telemetry
    assert scenario.gpu is not None and scenario.gpu.temp_max_c >= scenario.gpu.temp_start_c
    assert {s.phase for s in scenario.samples} >= {"load"}
    assert scenario.gpu.throttle_seconds.get("power_cap", 0) > 0
    assert scenario.gpu.host_cpu_mean_load_pct == 12.5
    # docker lifecycle: both containers started and removed
    runs = [c for c in docker.calls if c[1] == "run"]
    rms = [c for c in docker.calls if c[1] == "rm"]
    assert len(runs) == 2
    assert "--gpus" in runs[0] and "--gpus" not in runs[1]
    assert {c[-1] for c in rms} >= {"rag-stress-embeddinggemma-2-11091", "rag-stress-gemma4-e2b-11092"}
    assert any("ready in" in line for line in progress) and any("load 1s" in line for line in progress)


async def test_runner_marks_scenario_failed_when_container_dies():
    smi, docker, server = FakeSmi(), FakeDocker(status="exited"), FakeLlamaServer(healthy=False)
    suite = StressSuite(name="t", defaults=fast_defaults(), scenarios=[Scenario(name="dead", models=[chat_spec()])])
    result = await make_runner(docker, server, smi).run(suite, duration_s=1)
    scenario = result.scenarios[0]
    assert scenario.success is False
    assert "exited" in (scenario.error or "")
    assert scenario.models[0].requests == 0
    assert any(c[1] == "rm" and c[-1] == "rag-stress-gemma4-e2b-11091" for c in docker.calls)
    assert scenario.gpu is not None  # telemetry still summarised


async def test_runner_only_filter_and_unknown_name():
    smi, docker, server = FakeSmi(), FakeDocker(), FakeLlamaServer()
    suite = StressSuite(
        name="t",
        defaults=fast_defaults(),
        scenarios=[Scenario(name="a", models=[chat_spec()]), Scenario(name="b", models=[chat_spec()])],
    )
    runner = make_runner(docker, server, smi)
    result = await runner.run(suite, only=["b"], duration_s=1)
    assert [s.name for s in result.scenarios] == ["b"]
    with pytest.raises(ValueError, match="no scenario matches"):
        await runner.run(suite, only=["zzz"], duration_s=1)


async def test_runner_endpoint_model_starts_no_container():
    smi, docker, server = FakeSmi(), FakeDocker(), FakeLlamaServer()
    spec = ModelSpec(name="ollama", kind="chat", endpoint="http://ollama:11434", model_id="gemma4:26b", concurrency=1)
    suite = StressSuite(name="t", defaults=fast_defaults(), scenarios=[Scenario(name="ext", models=[spec])])
    result = await make_runner(docker, server, smi).run(suite, duration_s=1)
    model = result.scenarios[0].models[0]
    assert model.success and model.container is None and model.port is None and model.source == "http://ollama:11434"
    assert not any(c[1] in {"run", "rm"} for c in docker.calls)
    assert all(json.loads(r.content)["model"] == "gemma4:26b" for r in server.requests if r.url.path == "/v1/chat/completions")


async def test_sampler_without_nvidia_smi_still_records_host_stats(monkeypatch):
    monkeypatch.setattr(gs.shutil, "which", lambda name: None)
    sampler = TelemetrySampler(interval_s=0.01, host_stats=lambda: (7.0, 3.0))
    assert sampler.nvidia_smi_available is False
    sample = await sampler.sample_once()
    assert sample.temperature_c is None and sample.host_cpu_pct == 7.0


async def test_cleanup_stress_containers_uses_label():
    docker = FakeDocker()
    removed = await gs.cleanup_stress_containers(docker)
    assert removed == ["aaa", "bbb"]
    assert docker.calls[0][-1] == f"label={gs.STRESS_LABEL}"
    assert docker.calls[1] == ["docker", "rm", "-f", "aaa", "bbb"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    from backend.cli.main import HAS_TYPER

    if not HAS_TYPER:
        pytest.skip("typer not installed")
    from typer.testing import CliRunner

    import backend.cli.stress_commands as stress
    from backend.cli.main import app

    return CliRunner(), app, stress


def test_cli_gpu_stress_dry_run_prints_docker_commands():
    runner, app, stress = _cli()
    result = runner.invoke(app, ["bench", "gpu-stress", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "docker run -d --name rag-stress-embeddinggemma-2-11091" in result.output
    assert "--cpu-moe" in result.output and "--fit on" in result.output
    assert "[mixed-gpu-and-ram]" in result.output


def test_cli_gpu_stress_json_output_with_injected_runner(monkeypatch, tmp_path: Path):
    runner, app, stress = _cli()
    smi, docker, server = FakeSmi(), FakeDocker(), FakeLlamaServer()
    docker.smi = smi

    def factory(progress):
        return make_runner(docker, server, smi)

    monkeypatch.setattr(stress, "RUNNER_FACTORY", factory)
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(
        "name: cli\ndefaults: {duration_s: 5, warmup_s: 0, cooldown_s: 0, baseline_s: 0, sample_interval_s: 0.01}\n"
        "models:\n  e2b: {kind: chat, hf_repo: ggml-org/gemma-4-E2B-it-GGUF:Q8_0, concurrency: 1}\n"
        "scenarios:\n  - name: one\n    models: [{use: e2b}]\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "result.json"
    result = runner.invoke(
        app,
        ["--format", "json", "bench", "gpu-stress", "--suite", str(suite_file), "--duration", "1", "--output", str(out_file)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["kind"] == "gpu_stress" and payload["suite"] == "cli"
    assert payload["scenarios"][0]["models"][0]["requests"] > 0
    assert json.loads(result.output)["suite"] == "cli"


def test_cli_gpu_stress_table_output_and_failure_exit_code(monkeypatch, tmp_path: Path):
    runner, app, stress = _cli()
    monkeypatch.setenv("COLUMNS", "250")  # keep rich from wrapping table cells
    smi, docker, server = FakeSmi(), FakeDocker(status="exited"), FakeLlamaServer(healthy=False)
    monkeypatch.setattr(stress, "RUNNER_FACTORY", lambda progress: make_runner(docker, server, smi, progress=[]))
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(
        "name: cli\ndefaults: {duration_s: 5, warmup_s: 0, cooldown_s: 0, baseline_s: 0, sample_interval_s: 0.01}\n"
        "models:\n  e2b: {kind: chat, hf_repo: ggml-org/gemma-4-E2B-it-GGUF:Q8_0}\n"
        "scenarios:\n  - name: dead\n    models: [{use: e2b}]\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["bench", "gpu-stress", "--suite", str(suite_file), "--duration", "1"])
    assert result.exit_code == 2, result.output
    assert "FAILED" in result.output and "thermal" in result.output.lower()


def test_cli_gpu_stress_cleanup(monkeypatch):
    runner, app, stress = _cli()

    async def fake_cleanup(command_runner=None):
        return ["aaa"]

    monkeypatch.setattr(stress, "cleanup_stress_containers", fake_cleanup)
    result = runner.invoke(app, ["bench", "gpu-stress", "--cleanup"])
    assert result.exit_code == 0, result.output
    assert "removed 1 container" in result.output
