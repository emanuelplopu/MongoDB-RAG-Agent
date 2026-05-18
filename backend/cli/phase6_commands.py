"""Phase 6 operator commands for quellexctl.

Adds three command groups on top of the Phase 4 Typer framework:

* ``quellexctl schedule …`` — list/get/add/run-now/pause/resume/daemon
  operations against :class:`MongoSchedulerStore` and
  :class:`SchedulerDaemon` (Phase 5 / Tasks 60–62).
* ``quellexctl experiment list|get`` — companions to the existing
  Phase 4 ``experiment run``. The Phase 6 ``--mode`` flag lives on the
  existing ``experiment run`` command and dispatches into
  :func:`dispatch_experiment_run_mode` here.
* ``quellexctl profiler …`` — model-test and strategy-profile runners
  built on :class:`backend.services.runtime_profiler.RuntimeProfiler`.

All commands share a small *test hooks* shim (see :data:`_TEST_HOOKS`)
that lets the unit tests inject in-memory stores and mocked
daemons/runners without standing up MongoDB. Production callers rely
on the default lazy factories that open the same
``pymongo.AsyncMongoClient`` handles the FastAPI lifespan uses.

Repo rule #3: only ``pymongo.AsyncMongoClient`` is used for new async
DB access introduced here; no Motor APIs are added.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

try:
    import typer
    from rich.console import Console
    from rich.table import Table

    HAS_TYPER = True
except ImportError:  # pragma: no cover - exercised via Phase 4 ImportError guard
    HAS_TYPER = False

from backend.cli.main import OutputFormatter, app, run_async

# ─── Test hooks ──────────────────────────────────────────────────────────────
#
# Tests populate this dict to override the lazy production factories.
# Keys (all optional):
#     scheduler_store        : SchedulerStore
#     scheduler_daemon       : SchedulerDaemon
#     experiment_job_store   : ExperimentJobStore
#     experiment_runner      : object with async run_job(job_id) → job
#     runtime_profiler       : object with async profile_model(**kwargs)
#     strategy_profile_func  : async (strategy_id, dataset_id) → dict
_TEST_HOOKS: dict[str, Any] = {}


def _install_test_hook(name: str, value: Any) -> None:
    """Install/replace a test hook. Intended for use from pytest."""
    _TEST_HOOKS[name] = value


def _clear_test_hooks() -> None:
    """Remove every installed hook (call from pytest teardown)."""
    _TEST_HOOKS.clear()


# ─── Default factories ───────────────────────────────────────────────────────


def _scheduler_store() -> Any:
    """Return the active :class:`SchedulerStore`.

    Falls back to a process-local :class:`InMemorySchedulerStore` when
    no test hook is installed and no MongoDB handle is configured.
    """
    hook = _TEST_HOOKS.get("scheduler_store")
    if hook is not None:
        return hook
    from backend.agent.strategy.scheduler_store import InMemorySchedulerStore

    if "_default_scheduler_store" not in _TEST_HOOKS:
        _TEST_HOOKS["_default_scheduler_store"] = InMemorySchedulerStore()
    return _TEST_HOOKS["_default_scheduler_store"]


def _scheduler_daemon() -> Any:
    """Return the active :class:`SchedulerDaemon` instance."""
    hook = _TEST_HOOKS.get("scheduler_daemon")
    if hook is not None:
        return hook
    from backend.scheduler.scheduler_daemon import SchedulerDaemon

    if "_default_scheduler_daemon" not in _TEST_HOOKS:
        _TEST_HOOKS["_default_scheduler_daemon"] = SchedulerDaemon(
            store=_scheduler_store()
        )
    return _TEST_HOOKS["_default_scheduler_daemon"]


def _experiment_job_store() -> Any:
    """Return the active :class:`ExperimentJobStore`."""
    hook = _TEST_HOOKS.get("experiment_job_store")
    if hook is not None:
        return hook
    from backend.agent.strategy.experiment_job_store import (
        InMemoryExperimentJobStore,
    )

    if "_default_experiment_job_store" not in _TEST_HOOKS:
        _TEST_HOOKS["_default_experiment_job_store"] = InMemoryExperimentJobStore()
    return _TEST_HOOKS["_default_experiment_job_store"]


def _experiment_runner() -> Any:
    """Return the active experiment runner. Tests must inject one."""
    hook = _TEST_HOOKS.get("experiment_runner")
    if hook is None:
        raise RuntimeError(
            "experiment runner is not configured. Install a hook via "
            "phase6_commands._install_test_hook('experiment_runner', …) "
            "or wire one through the production lifespan."
        )
    return hook


def _runtime_profiler() -> Any:
    """Return the active runtime profiler. Tests must inject one."""
    hook = _TEST_HOOKS.get("runtime_profiler")
    if hook is None:
        raise RuntimeError(
            "runtime profiler is not configured. Install a hook via "
            "phase6_commands._install_test_hook('runtime_profiler', …)."
        )
    return hook


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_window(window: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse a ``HH:MM-HH:MM`` window string into ``(start, end)``."""
    if not window:
        return None, None
    if "-" not in window:
        raise typer.BadParameter(
            f"--window must use 'HH:MM-HH:MM' form (got {window!r})"
        )
    start, end = window.split("-", 1)
    return start.strip(), end.strip()


def _parse_csv(value: Optional[str]) -> list[str]:
    """Split a comma-separated CLI value into a clean list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_contexts(value: Optional[str]) -> list[int]:
    """Parse a context-length spec like ``2k,6k,16k,32k`` into ints.

    Suffix ``k`` is treated as ``* 1024``. Bare integers are accepted
    as-is. Empty input returns an empty list (meaning "use suite default").
    """
    if not value:
        return []
    out: list[int] = []
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token.endswith("k"):
            out.append(int(float(token[:-1]) * 1024))
        else:
            out.append(int(token))
    return out


def _fmt_dt(value: Optional[datetime]) -> str:
    """Render a datetime as an ISO string, or ``—`` when missing."""
    if value is None:
        return "—"
    return value.isoformat()


def _json_default(value: Any) -> Any:
    """JSON encoder fallback for datetimes/enums/Pydantic models."""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if hasattr(value, "value"):
        return value.value
    return str(value)


def _dump_json(payload: Any) -> str:
    """Stable JSON dump using :func:`_json_default`."""
    return json.dumps(payload, indent=2, default=_json_default)


# ─── Typer groups ────────────────────────────────────────────────────────────


if HAS_TYPER:
    schedule_app = typer.Typer(
        name="schedule",
        help="Strategy scheduler operator commands (list/add/run-now/daemon).",
    )
    profiler_app = typer.Typer(
        name="profiler",
        help="Runtime profiler commands (model-test, strategy-profile).",
    )
    app.add_typer(schedule_app, name="schedule")
    app.add_typer(profiler_app, name="profiler")

    # Re-attach to the existing experiment group from eval_commands so the
    # `list`/`get` commands sit alongside the Phase 4 ``run``/``report``.
    from backend.cli.eval_commands import experiment_app  # noqa: E402

    console = Console()

# ─── Schedule commands ───────────────────────────────────────────────────────


async def _list_schedules() -> list[dict]:
    store = _scheduler_store()
    schedules = await store.list()
    return [s.model_dump(mode="python") for s in schedules]


async def _get_schedule(name: str) -> Optional[dict]:
    store = _scheduler_store()
    schedules = await store.list()
    for schedule in schedules:
        if schedule.name == name or schedule.id == name:
            return schedule.model_dump(mode="python")
    return None


async def _add_schedule(
    *,
    name: str,
    mode: str,
    tenant: str,
    datasets: list[str],
    strategies: list[str],
    cron: str,
    window: Optional[str],
    timezone_name: str,
    profile_key: Optional[str],
    candidate_generator: Optional[str],
) -> dict:
    """Persist a new :class:`StrategySchedule` and return the stored doc."""
    from backend.scheduler.models import StrategySchedule

    window_start, window_end = _parse_window(window)
    # Task 83: ``mode``/``tenant``/``profile_key``/``candidate_generator_id``
    # are first-class persisted fields on :class:`StrategySchedule` so the
    # CLI flags now flow straight through the constructor instead of being
    # patched onto the dumped payload after the fact.
    schedule = StrategySchedule(
        name=name,
        cron=cron,
        timezone=timezone_name,
        mode=mode,  # type: ignore[arg-type]  # validated by Literal
        tenant=tenant,
        profile_key=profile_key,
        candidate_generator_id=candidate_generator,
    )
    if window_start is not None:
        schedule.allowed_window_start = window_start
    if window_end is not None:
        schedule.allowed_window_end = window_end
    if datasets:
        schedule.dataset_id = datasets[0]
    if strategies:
        schedule.candidate_strategy_ids = list(strategies)

    store = _scheduler_store()
    stored = await store.upsert(schedule)
    return stored.model_dump(mode="python")


async def _set_schedule_paused(name: str, paused: bool, reason: Optional[str]) -> dict:
    store = _scheduler_store()
    schedules = await store.list()
    match = next(
        (s for s in schedules if s.name == name or s.id == name), None
    )
    if match is None:
        raise LookupError(f"Schedule {name!r} not found")
    updated = await store.update_runtime_state(
        match.id,
        paused=paused,
        paused_reason=reason if paused else "",
    )
    return updated.model_dump(mode="python") if updated is not None else {}


async def _run_now(name: str) -> str:
    store = _scheduler_store()
    daemon = _scheduler_daemon()
    schedules = await store.list()
    match = next(
        (s for s in schedules if s.name == name or s.id == name), None
    )
    if match is None:
        from backend.scheduler.scheduler_daemon import ScheduleNotFoundError

        raise ScheduleNotFoundError(name)
    return await daemon.run_now(match.id)


if HAS_TYPER:

    @schedule_app.command("list")
    def schedule_list(
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """List every persisted schedule with status and run timing."""
        fmt = OutputFormatter(output_format)
        try:
            results = run_async(_list_schedules())
        except Exception as exc:  # noqa: BLE001 - CLI surface
            fmt.print_error(f"Failed to list schedules: {exc}")
            raise typer.Exit(code=1)

        if output_format == "json":
            print(_dump_json(results))
            return
        if not results:
            console.print("[yellow]No schedules persisted.[/yellow]")
            return
        table = Table(title="Schedules")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name")
        table.add_column("Status")
        table.add_column("Paused")
        table.add_column("Mode")
        table.add_column("Tenant")
        table.add_column("Profile")
        table.add_column("Generator")
        table.add_column("Last Run")
        table.add_column("Next Run")
        for s in results:
            table.add_row(
                str(s.get("id", "—"))[:12],
                str(s.get("name", "—")),
                str(s.get("status", "—")),
                "yes" if s.get("paused") else "no",
                str(s.get("mode") or "—"),
                str(s.get("tenant") or "—"),
                str(s.get("profile_key") or "—"),
                str(s.get("candidate_generator_id") or "—"),
                _fmt_dt(s.get("last_run_at")),
                _fmt_dt(s.get("next_run_at")),
            )
        console.print(table)

    @schedule_app.command("get")
    def schedule_get(
        name: str = typer.Argument(..., help="Schedule name or id"),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """Show full details for a single schedule."""
        fmt = OutputFormatter(output_format)
        try:
            doc = run_async(_get_schedule(name))
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Failed to fetch schedule: {exc}")
            raise typer.Exit(code=1)
        if doc is None:
            fmt.print_error(f"Schedule {name!r} not found")
            raise typer.Exit(code=1)
        if output_format == "json":
            print(_dump_json(doc))
            return
        fmt.print_result(doc, title=f"Schedule: {name}")

    @schedule_app.command("add")
    def schedule_add(
        name: str = typer.Option(..., "--name", help="Schedule name"),
        mode: str = typer.Option(
            "regression", "--mode", help="Experiment mode for fired jobs"
        ),
        tenant: str = typer.Option(
            "recallhub", "--tenant", "-t", help="Tenant scope"
        ),
        datasets: str = typer.Option(
            ..., "--datasets", help="Comma-separated dataset ids"
        ),
        strategies: str = typer.Option(
            ..., "--strategies", help="Comma-separated strategy ids"
        ),
        cron: str = typer.Option(
            "0 22 * * 1-5", "--cron", help="Cron expression (5-field)"
        ),
        window: Optional[str] = typer.Option(
            None, "--window", help="Allowed window 'HH:MM-HH:MM' local time"
        ),
        timezone_name: str = typer.Option(
            "Europe/Vienna", "--timezone", help="IANA timezone"
        ),
        profile_key: Optional[str] = typer.Option(
            None, "--profile-key", help="Optional profiles.yaml key"
        ),
        candidate_generator: Optional[str] = typer.Option(
            None, "--candidate-generator", help="Generator id for exploration mode"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """Create a new persisted :class:`StrategySchedule`."""
        fmt = OutputFormatter(output_format)
        try:
            dataset_ids = _parse_csv(datasets)
            strategy_ids = _parse_csv(strategies)
            doc = run_async(
                _add_schedule(
                    name=name,
                    mode=mode,
                    tenant=tenant,
                    datasets=dataset_ids,
                    strategies=strategy_ids,
                    cron=cron,
                    window=window,
                    timezone_name=timezone_name,
                    profile_key=profile_key,
                    candidate_generator=candidate_generator,
                )
            )
        except typer.BadParameter:
            raise
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Failed to add schedule: {exc}")
            raise typer.Exit(code=1)
        if output_format == "json":
            print(_dump_json(doc))
        else:
            fmt.print_success(f"Schedule '{name}' added (id={doc.get('id')})")

    @schedule_app.command("run-now")
    def schedule_run_now(
        name: str = typer.Argument(..., help="Schedule name or id"),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """Trigger an immediate exploration run for the named schedule."""
        fmt = OutputFormatter(output_format)
        try:
            trace_id = run_async(_run_now(name))
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"run-now failed: {exc}")
            raise typer.Exit(code=1)
        payload = {"schedule": name, "trace_id": trace_id}
        if output_format == "json":
            print(_dump_json(payload))
        else:
            fmt.print_success(
                f"Schedule '{name}' triggered (trace_id={trace_id})"
            )

    @schedule_app.command("pause")
    def schedule_pause(
        name: str = typer.Argument(..., help="Schedule name or id"),
        reason: Optional[str] = typer.Option(
            None, "--reason", "-r", help="Why the schedule is paused"
        ),
    ) -> None:
        """Pause a schedule. Survives daemon restarts."""
        fmt = OutputFormatter("table")
        try:
            run_async(_set_schedule_paused(name, True, reason))
        except LookupError as exc:
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Pause failed: {exc}")
            raise typer.Exit(code=1)
        fmt.print_success(f"Schedule '{name}' paused")

    @schedule_app.command("resume")
    def schedule_resume(
        name: str = typer.Argument(..., help="Schedule name or id"),
    ) -> None:
        """Clear the persisted pause flag on a schedule."""
        fmt = OutputFormatter("table")
        try:
            run_async(_set_schedule_paused(name, False, None))
        except LookupError as exc:
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Resume failed: {exc}")
            raise typer.Exit(code=1)
        fmt.print_success(f"Schedule '{name}' resumed")

    @schedule_app.command("daemon")
    def schedule_daemon(
        backend_url: Optional[str] = typer.Option(
            None, "--backend-url", help="Backend URL (informational)"
        ),
    ) -> None:
        """Run the scheduler daemon in the foreground.

        Intentional foreground blocking — operators run this under
        tmux/systemd per Blueprint 05 §12.
        """
        fmt = OutputFormatter("table")
        daemon = _scheduler_daemon()
        if backend_url:
            console.print(f"[cyan]Backend URL hint:[/cyan] {backend_url}")
        console.print("[green]SchedulerDaemon starting (Ctrl+C to stop)…[/green]")
        try:
            run_async(daemon.start())
        except KeyboardInterrupt:
            console.print("[yellow]SchedulerDaemon interrupted.[/yellow]")
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Daemon crashed: {exc}")
            raise typer.Exit(code=1)


# ─── Experiment commands (Phase 6 additions) ─────────────────────────────────


async def _build_candidates_for_dry_run(
    *,
    mode: str,
    strategies: list[str],
    datasets: list[str],
) -> dict:
    """Return a JSON-shaped preview of candidates without running them."""
    from backend.agent.strategy.exploration_modes import (
        ModeRegistry,
        UnknownExplorationModeError,
    )

    try:
        ModeRegistry.get(mode)
    except UnknownExplorationModeError as exc:
        raise ValueError(f"Unknown mode '{mode}': {exc}")
    return {
        "mode": mode,
        "candidates": list(strategies),
        "datasets": list(datasets),
        "dry_run": True,
    }


async def _create_and_run_job(
    *,
    mode: str,
    tenant: str,
    datasets: list[str],
    strategies: list[str],
    profile_key: Optional[str],
    candidate_generator: Optional[str],
    resource_limits: Optional[dict],
    watch: bool,
    no_profile: bool,
    progress_callback: Optional[Callable[[Any], Awaitable[None]]] = None,
) -> dict:
    """Create a job, transition QUEUED, and invoke the runner."""
    from backend.agent.strategy.experiment_job_models import (
        JobStatus,
        StrategyExperimentJob,
    )
    from backend.agent.strategy.scheduler_models import ResourceLimits

    store = _experiment_job_store()
    limits = ResourceLimits(**resource_limits) if resource_limits else None

    job = StrategyExperimentJob(
        tenant=tenant,
        mode=mode,  # type: ignore[arg-type]
        datasets=list(datasets),
        strategy_ids=list(strategies),
        profile_key=profile_key,
        candidate_generator_id=candidate_generator,
        resource_limits=limits,
        status=JobStatus.SCHEDULED,
    )
    await store.create(job)
    await store.update_status(job.id, JobStatus.QUEUED)

    runner = _experiment_runner()

    if watch and progress_callback is not None:
        # Drive the runner in a background task and stream progress.
        run_task = asyncio.create_task(runner.run_job(job.id))
        while not run_task.done():
            current = await store.get(job.id)
            if current is not None:
                await progress_callback(current)
            try:
                await asyncio.wait_for(asyncio.shield(run_task), timeout=0.05)
            except asyncio.TimeoutError:
                continue
        result = await run_task
    else:
        result = await runner.run_job(job.id)

    payload = result.model_dump(mode="python") if hasattr(result, "model_dump") else dict(result)
    payload["no_profile"] = no_profile
    return payload


def dispatch_experiment_run_mode(
    *,
    mode: str,
    strategies: list[str],
    datasets: list[str],
    tenant: str,
    profile_key: Optional[str],
    candidate_generator: Optional[str],
    max_cpu_pct: Optional[int],
    max_ram_pct: Optional[int],
    max_runtime_min: Optional[int],
    dry_run: bool,
    watch: bool,
    no_profile: bool,
    out: Optional[str],
    output_format: str,
) -> None:
    """Phase 6 ``experiment run --mode`` entrypoint.

    Called by :mod:`backend.cli.eval_commands` when the user supplies
    ``--mode`` on the existing ``experiment run`` command.
    """
    fmt = OutputFormatter(output_format)

    # Quick validation: known mode?
    from backend.agent.strategy.exploration_modes import (
        ModeRegistry,
        UnknownExplorationModeError,
    )

    try:
        ModeRegistry.get(mode)
    except UnknownExplorationModeError:
        fmt.print_error(
            f"Unknown experiment mode '{mode}'. "
            f"Known modes: {', '.join(ModeRegistry.known_modes())}"
        )
        raise typer.Exit(code=2)

    if dry_run:
        try:
            preview = run_async(
                _build_candidates_for_dry_run(
                    mode=mode, strategies=strategies, datasets=datasets
                )
            )
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Dry-run failed: {exc}")
            raise typer.Exit(code=1)
        if output_format == "json":
            print(_dump_json(preview))
        else:
            console.print(
                f"[cyan]Dry run[/cyan] mode={mode} "
                f"candidates={preview['candidates']} datasets={preview['datasets']}"
            )
        if out:
            _write_out(out, preview)
        return

    resource_limits: Optional[dict] = None
    if any(v is not None for v in (max_cpu_pct, max_ram_pct, max_runtime_min)):
        resource_limits = {}
        if max_cpu_pct is not None:
            resource_limits["max_cpu_utilization_pct"] = int(max_cpu_pct)
        if max_ram_pct is not None:
            resource_limits["max_ram_usage_pct"] = int(max_ram_pct)
        if max_runtime_min is not None:
            resource_limits["max_runtime_minutes"] = int(max_runtime_min)

    async def _progress(job: Any) -> None:
        progress = getattr(job, "progress", None)
        if progress is None:
            return
        sys.stdout.write(
            f"\r[watch] status={job.status.value if hasattr(job.status, 'value') else job.status} "
            f"completed={progress.completed_runs}/{progress.total_runs} "
            f"current={progress.current_strategy or '—'}"
        )
        sys.stdout.flush()
        await asyncio.sleep(1.0)

    try:
        payload = run_async(
            _create_and_run_job(
                mode=mode,
                tenant=tenant,
                datasets=datasets,
                strategies=strategies,
                profile_key=profile_key,
                candidate_generator=candidate_generator,
                resource_limits=resource_limits,
                watch=watch,
                no_profile=no_profile,
                progress_callback=_progress if watch else None,
            )
        )
    except Exception as exc:  # noqa: BLE001
        fmt.print_error(f"Experiment run failed: {exc}")
        raise typer.Exit(code=1)

    if watch:
        sys.stdout.write("\n")
        sys.stdout.flush()

    if output_format == "json":
        print(_dump_json(payload))
    else:
        fmt.print_success(
            f"Job {payload.get('id', '?')} finished with status "
            f"{payload.get('status', '?')}"
        )
    if out:
        _write_out(out, payload)


def _write_out(path: str, payload: Any) -> None:
    """Write a payload to ``path`` as JSON. Best-effort, never raises."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_dump_json(payload))
    except OSError as exc:
        console.print(f"[yellow]Failed to write {path}: {exc}[/yellow]")


async def _list_experiment_jobs(
    *,
    status: Optional[str],
    tenant: Optional[str],
    limit: int,
) -> list[dict]:
    from backend.agent.strategy.experiment_job_models import JobStatus

    store = _experiment_job_store()
    status_enum = JobStatus(status) if status else None
    jobs = await store.list(
        status=status_enum,
        tenant=tenant,
        limit=limit,
    )
    return [j.model_dump(mode="python") for j in jobs]


async def _get_experiment_job(job_id: str) -> Optional[dict]:
    store = _experiment_job_store()
    job = await store.get(job_id)
    if job is None:
        return None
    return job.model_dump(mode="python")


if HAS_TYPER:

    @experiment_app.command("list")
    def experiment_list(
        status: Optional[str] = typer.Option(
            None, "--status", help="Filter by job status"
        ),
        tenant: Optional[str] = typer.Option(
            None, "--tenant", "-t", help="Filter by tenant"
        ),
        limit: int = typer.Option(
            50, "--limit", "-n", help="Max rows returned"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """List recent experiment jobs."""
        fmt = OutputFormatter(output_format)
        try:
            jobs = run_async(
                _list_experiment_jobs(status=status, tenant=tenant, limit=limit)
            )
        except ValueError as exc:
            fmt.print_error(f"Invalid status filter: {exc}")
            raise typer.Exit(code=1)
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Failed to list jobs: {exc}")
            raise typer.Exit(code=1)

        if output_format == "json":
            print(_dump_json(jobs))
            return
        if not jobs:
            console.print("[yellow]No experiment jobs found.[/yellow]")
            return
        table = Table(title="Experiment Jobs")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Status")
        table.add_column("Mode")
        table.add_column("Tenant")
        table.add_column("Strategies")
        table.add_column("Created")
        for j in jobs:
            status_val = j.get("status")
            status_str = getattr(status_val, "value", status_val)
            table.add_row(
                str(j.get("id", "—"))[:12],
                str(status_str or "—"),
                str(j.get("mode", "—")),
                str(j.get("tenant", "—")),
                ",".join(j.get("strategy_ids", []))[:32] or "—",
                _fmt_dt(j.get("created_at")),
            )
        console.print(table)

    @experiment_app.command("get")
    def experiment_get(
        job_id: str = typer.Argument(..., help="Experiment job id"),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """Show details + summary for a single experiment job."""
        fmt = OutputFormatter(output_format)
        try:
            job = run_async(_get_experiment_job(job_id))
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"Failed to fetch job: {exc}")
            raise typer.Exit(code=1)
        if job is None:
            fmt.print_error(f"Experiment job {job_id!r} not found")
            raise typer.Exit(code=1)
        if output_format == "json":
            print(_dump_json(job))
        else:
            fmt.print_result(job, title=f"Experiment Job: {job_id}")


# ─── Profiler commands ───────────────────────────────────────────────────────


async def _profile_model(
    *,
    model: str,
    provider: str,
    contexts: list[int],
) -> list[dict]:
    profiler = _runtime_profiler()
    profiles = await profiler.profile_model(
        model=model,
        provider=provider,
        contexts=contexts or None,
    )
    return [
        p.model_dump(mode="python") if hasattr(p, "model_dump") else dict(p)
        for p in profiles
    ]


async def _profile_strategy(
    *,
    strategy_id: str,
    dataset_id: str,
) -> dict:
    """Aggregate runtime measurements for a strategy on a dataset.

    Tests inject a ``strategy_profile_func`` hook for end-to-end
    deterministic behaviour. Production defaults to running the
    profiler against the strategy's resolved worker model and emitting
    an aggregate ``{"latency_ms": ..., "tps": ...}`` summary.
    """
    hook = _TEST_HOOKS.get("strategy_profile_func")
    if hook is not None:
        return await hook(strategy_id, dataset_id)

    profiler = _runtime_profiler()
    profiles = await profiler.profile_model(
        model=strategy_id, provider="ollama", contexts=None
    )
    profile_list = [
        p.model_dump(mode="python") if hasattr(p, "model_dump") else dict(p)
        for p in profiles
    ]
    if not profile_list:
        return {
            "strategy_id": strategy_id,
            "dataset_id": dataset_id,
            "profiles": 0,
            "avg_latency_ms": 0,
            "avg_tps": 0.0,
        }
    avg_latency = sum(p.get("total_latency_ms", 0) for p in profile_list) / len(
        profile_list
    )
    avg_tps = sum(p.get("tokens_per_second", 0.0) for p in profile_list) / len(
        profile_list
    )
    return {
        "strategy_id": strategy_id,
        "dataset_id": dataset_id,
        "profiles": len(profile_list),
        "avg_latency_ms": round(avg_latency, 2),
        "avg_tps": round(avg_tps, 2),
    }


if HAS_TYPER:

    @profiler_app.command("model-test")
    def profiler_model_test(
        model: str = typer.Option(..., "--model", help="Model identifier"),
        provider: str = typer.Option(
            "ollama", "--provider", help="Provider identifier"
        ),
        contexts: Optional[str] = typer.Option(
            None,
            "--contexts",
            help="Comma-separated context sizes (e.g. 2k,6k,16k,32k)",
        ),
        out: Optional[str] = typer.Option(
            None, "--out", help="Optional file path to write JSON results"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """Run the standardized profiler suite against a single model."""
        fmt = OutputFormatter(output_format)
        try:
            ctx_list = _parse_contexts(contexts)
            results = run_async(
                _profile_model(model=model, provider=provider, contexts=ctx_list)
            )
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"profile_model failed: {exc}")
            raise typer.Exit(code=1)

        if output_format == "json":
            print(_dump_json(results))
        else:
            table = Table(title=f"Runtime profile: {provider}/{model}")
            table.add_column("Test", style="cyan")
            table.add_column("Ctx tokens", justify="right")
            table.add_column("Latency ms", justify="right")
            table.add_column("TPS", justify="right")
            table.add_column("Success")
            for r in results:
                table.add_row(
                    str(r.get("test_name", "—")),
                    str(r.get("context_tokens", "—")),
                    str(r.get("total_latency_ms", "—")),
                    f"{r.get('tokens_per_second', 0):.2f}",
                    "✓" if r.get("success") else "✗",
                )
            console.print(table)
        if out:
            _write_out(out, results)

    @profiler_app.command("strategy-profile")
    def profiler_strategy_profile(
        strategy: str = typer.Option(..., "--strategy", help="Strategy spec id"),
        dataset: str = typer.Option(..., "--dataset", help="Dataset id"),
        out: Optional[str] = typer.Option(
            None, "--out", help="Optional file path to write JSON results"
        ),
        output_format: str = typer.Option(
            "table", "--format", "-f", help="Output format: table or json"
        ),
    ) -> None:
        """Profile a strategy against a dataset and print the aggregate."""
        fmt = OutputFormatter(output_format)
        try:
            payload = run_async(
                _profile_strategy(strategy_id=strategy, dataset_id=dataset)
            )
        except Exception as exc:  # noqa: BLE001
            fmt.print_error(f"strategy-profile failed: {exc}")
            raise typer.Exit(code=1)
        if output_format == "json":
            print(_dump_json(payload))
        else:
            fmt.print_result(payload, title=f"Strategy profile: {strategy}")
        if out:
            _write_out(out, payload)
