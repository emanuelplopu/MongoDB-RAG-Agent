"""``quellexctl bench gpu-stress`` — GPU thermal / mixed-placement stress test.

Usage:
    quellexctl bench gpu-stress                                   # default suite, all scenarios
    quellexctl bench gpu-stress --only gemma4-26b-split --duration 180 --output stress.json
    quellexctl bench gpu-stress --suite my-suite.yaml --dry-run   # print docker commands only
    quellexctl bench gpu-stress --cleanup                         # remove leftover stress containers

The suite file (default ``backend/config/gpu_stress/gemma4.yaml``) lists the
models (Hugging Face GGUF refs or existing endpoints), their placement (GPU,
RAM, split) and the scenarios. Only Docker, the NVIDIA Container Toolkit and
``nvidia-smi`` are needed on the host, so the same suite can be rerun on other
hardware and the JSON results compared.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

from backend.cli.bench_commands import bench_app
from backend.cli.main import HAS_TYPER, OutputFormatter, app, console, run_async
from backend.services.gpu_stress import (
    StressResult,
    StressRunner,
    StressSuite,
    build_docker_command,
    cleanup_stress_containers,
    load_suite,
)

if HAS_TYPER:
    import typer

DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "config" / "gpu_stress" / "gemma4.yaml"


def _default_runner(progress: Callable[[str], None]) -> StressRunner:
    return StressRunner(progress=progress)


# Injection point for tests.
RUNNER_FACTORY: Callable[[Callable[[str], None]], StressRunner] = _default_runner


def _fmt(value: Optional[float], digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}{suffix}"


def model_rows(result: StressResult) -> List[List[str]]:
    rows: List[List[str]] = []
    for scenario in result.scenarios:
        if not scenario.models:
            rows.append([scenario.name, "-", "FAILED", scenario.error or "", "", "", "", "", "", "", ""])
        for m in scenario.models:
            weights = "-" if m.gpu_model_buffer_mb is None else f"{_fmt(m.gpu_model_buffer_mb)} / {_fmt(m.cpu_model_buffer_mb)}"
            rows.append(
                [
                    scenario.name,
                    f"{m.name} ({m.placement})",
                    _fmt(m.load_time_s, 1),
                    _fmt(m.vram_delta_mb),
                    m.offloaded_layers or "-",
                    weights,
                    _fmt(m.tokens_per_s, 1) + (" gen" if m.kind == "chat" else " in"),
                    _fmt(m.prompt_tokens_per_s),
                    _fmt(m.requests_per_s, 2),
                    _fmt(m.latency_p95_ms),
                    str(m.errors) if m.success else f"FAILED: {m.error}",
                ]
            )
    return rows


def gpu_rows(result: StressResult) -> List[List[str]]:
    rows: List[List[str]] = []
    for scenario in result.scenarios:
        g = scenario.gpu
        if g is None:
            rows.append([scenario.name, "-", "-", "-", "-", "-", "-", "-"])
            continue
        throttle = ", ".join(f"{k} {v:.0f}s" for k, v in g.throttle_seconds.items()) or "none"
        rows.append(
            [
                scenario.name,
                f"{_fmt(g.temp_start_c)} / {_fmt(g.temp_max_c)} / {_fmt(g.temp_end_c)}",
                _fmt(g.time_to_max_s),
                f"{_fmt(g.power_mean_load_w)} / {_fmt(g.power_limit_w)}",
                _fmt(g.utilization_mean_load_pct),
                f"{_fmt(g.sm_clock_mean_load_mhz)} / {_fmt(g.sm_clock_min_load_mhz)}",
                _fmt(g.memory_used_max_mb),
                throttle,
                _fmt(g.host_cpu_mean_load_pct),
            ]
        )
    return rows


def dry_run_lines(suite: StressSuite) -> List[str]:
    lines: List[str] = [f"suite {suite.name}: {len(suite.scenarios)} scenario(s)"]
    for scenario in suite.scenarios:
        lines.append(f"[{scenario.name}] {scenario.description.strip()}")
        for index, spec in enumerate(scenario.models):
            port = suite.defaults.base_port + index
            if spec.endpoint:
                lines.append(f"  {spec.name}: endpoint {spec.endpoint} (model {spec.request_model})")
            else:
                lines.append("  " + " ".join(build_docker_command(spec, suite.defaults, port)))
    return lines


if HAS_TYPER:

    @bench_app.command("gpu-stress")
    def gpu_stress(
        suite: Path = typer.Option(DEFAULT_SUITE, "--suite", "-s", help="Suite YAML/JSON", exists=True, readable=True),
        only: Optional[List[str]] = typer.Option(None, "--only", help="Run only these scenario names (repeatable)"),
        duration: Optional[int] = typer.Option(None, "--duration", min=1, help="Override load duration (seconds)"),
        cooldown: Optional[int] = typer.Option(None, "--cooldown", min=0, help="Override cooldown sampling (seconds)"),
        output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the full JSON result here"),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print the docker commands and exit"),
        cleanup: bool = typer.Option(False, "--cleanup", help="Remove leftover stress containers and exit"),
    ) -> None:
        """Sustained GPU load test with per-second nvidia-smi telemetry (temperature, power, clocks, VRAM)."""
        fmt = OutputFormatter(app.state.get("format", "table") if hasattr(app, "state") else "table")

        if cleanup:
            removed = run_async(cleanup_stress_containers())
            fmt.print_success(f"removed {len(removed)} container(s)")
            return

        try:
            loaded = load_suite(suite)
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(f"cannot load suite {suite}: {exc}")
            raise typer.Exit(code=1)

        if dry_run:
            for line in dry_run_lines(loaded):
                print(line)
            return

        def progress(message: str) -> None:
            if fmt.format != "json":
                console.print(f"[dim]{message}[/dim]")

        try:
            result = run_async(RUNNER_FACTORY(progress).run(loaded, only=only or None, duration_s=duration, cooldown_s=cooldown))
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)

        if output:
            output.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        if fmt.format == "json":
            print(result.model_dump_json(indent=2))
        else:
            hw = result.hardware
            if hw:
                gpus = ", ".join(f"{g.name} ({g.memory_total_mb} MB, driver {g.driver_version})" for g in hw.gpus) or "no NVIDIA GPU"
                console.print(f"[dim]{hw.hostname}: {hw.cpu_count} CPUs, {hw.ram_gb} GB RAM, {gpus}[/dim]")
            fmt.print_table(
                ["Scenario", "Model (placement)", "Load s", "VRAM +MB", "Layers GPU", "Weights MB GPU / CPU-mapped", "tok/s", "prompt tok/s", "req/s", "p95 ms", "Errors"],
                model_rows(result),
                title=f"GPU stress - {result.suite} - throughput",
            )
            fmt.print_table(
                ["Scenario", "Temp start/max/end C", "To max s", "Power mean/limit W", "GPU util %", "SM clock mean/min MHz", "VRAM max MB", "Throttle (load)", "Host CPU %"],
                gpu_rows(result),
                title=f"GPU stress - {result.suite} - thermal",
            )
            for scenario in result.scenarios:
                for warning in scenario.warnings:
                    console.print(f"[yellow]warning:[/yellow] {scenario.name}: {warning}")
            if output:
                fmt.print_success(f"Full result written to {output}")

        if any(not s.success for s in result.scenarios):
            raise typer.Exit(code=2)
