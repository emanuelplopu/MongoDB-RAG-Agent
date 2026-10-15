"""Command line interface: ``python -m backend.evaluation.synthetic_corpus`` or ``quellexctl synth``.

Examples::

    synth init-config --out synth.json
    synth show-config -c synth.json --set llm.concurrency=8
    synth areas list
    synth cases new --area mietrecht_mietzins_raeumung --id mietrecht_02 --title "..." --description "..."
    synth run -c synth.json --run-name kanzlei_v1
    synth run --until plan --case inkasso_01
    synth status --run-name kanzlei_v1
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import DATA_DIR, SynthConfig, default_config_json, load_config
from .models import CaseSeed
from .seeds import SeedRegistry, write_area_template, write_case_seed

app = typer.Typer(name="synth", help="Synthetic Austrian law-firm corpus generator.", no_args_is_help=True)
areas_app = typer.Typer(help="Legal areas (Rechtsgebiete).", no_args_is_help=True)
cases_app = typer.Typer(help="Case seeds.", no_args_is_help=True)
app.add_typer(areas_app, name="areas")
app.add_typer(cases_app, name="cases")
console = Console()

ConfigOpt = typer.Option(None, "--config", "-c", help="JSON config file(s), merged in order.")
SetOpt = typer.Option(None, "--set", "-s", help="Override: dotted.path=value (value parsed as JSON if possible).")
RunNameOpt = typer.Option(None, "--run-name", help="Shortcut for run.run_name.")
OutputOpt = typer.Option(None, "--output-root", help="Shortcut for run.output_root.")
AreaOpt = typer.Option(None, "--area", "-a", help="Restrict to these areas (repeatable).")
CaseOpt = typer.Option(None, "--case", help="Restrict to these case seeds (repeatable).")
ForceOpt = typer.Option(False, "--force", help="Redo the stage even if its output exists.")
VerboseOpt = typer.Option(False, "--verbose", "-v", help="Debug logging.")
ConcOpt = typer.Option(None, "--concurrency", help="Shortcut for llm.concurrency.")
OrchOpt = typer.Option(None, "--orchestrator-model", help="Shortcut for llm.orchestrator.model.")
GenOpt = typer.Option(None, "--generator-model", help="Shortcut for llm.generator.model.")


def _cfg(
    config: Optional[list[Path]],
    sets: Optional[list[str]],
    run_name: Optional[str] = None,
    output_root: Optional[str] = None,
    areas: Optional[list[str]] = None,
    cases: Optional[list[str]] = None,
    concurrency: Optional[int] = None,
    orchestrator: Optional[str] = None,
    generator: Optional[str] = None,
) -> SynthConfig:
    overrides = list(sets or [])
    if run_name:
        overrides.append(f"run.run_name={run_name}")
    if output_root:
        overrides.append(f"run.output_root={json.dumps(output_root)}")
    if areas:
        overrides.append(f"run.areas={json.dumps(areas)}")
    if cases:
        overrides.append(f"run.cases={json.dumps(cases)}")
    if concurrency:
        overrides.append(f"llm.concurrency={concurrency}")
    if orchestrator:
        overrides.append(f"llm.orchestrator.model={json.dumps(orchestrator)}")
    if generator:
        overrides.append(f"llm.generator.model={json.dumps(generator)}")
    return load_config([str(p) for p in config or []], overrides)


def _logging(verbose: bool) -> None:
    from rich.logging import RichHandler

    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s",
                        handlers=[RichHandler(console=console, show_path=False)], force=True)
    for noisy in ("httpx", "openai", "faker", "fontTools", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _print(obj: Any) -> None:
    console.print_json(json.dumps(obj, ensure_ascii=False, default=str))


@app.command("init-config")
def init_config(out: Path = typer.Option(Path("synth.config.json"), "--out", "-o"), force: bool = False) -> None:
    """Write the complete default configuration (every setting) to a JSON file."""
    if out.exists() and not force:
        raise typer.BadParameter(f"{out} exists; use --force")
    out.write_text(default_config_json() + "\n", encoding="utf-8")
    console.print(f"wrote {out}")


@app.command("show-config")
def show_config(config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt) -> None:
    """Print the effective configuration after merging files and overrides."""
    console.print_json(_cfg(config, sets).model_dump_json())


@areas_app.command("list")
def areas_list(config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt) -> None:
    """List legal areas with their source files."""
    reg = SeedRegistry(_cfg(config, sets).seeds)
    table = Table("id", "name", "doc types", "roles", "cases", "file")
    for a in sorted(reg.areas.values(), key=lambda a: a.id):
        n_cases = sum(1 for c in reg.cases.values() if c.area == a.id)
        table.add_row(a.id, a.name, str(len(a.document_types)), str(len(a.roles)), str(n_cases), str(reg.sources[f"area:{a.id}"]))
    console.print(table)


@areas_app.command("show")
def areas_show(area_id: str, config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt) -> None:
    """Show one area as JSON."""
    reg = SeedRegistry(_cfg(config, sets).seeds)
    console.print_json(reg.areas[area_id].model_dump_json())


@areas_app.command("new")
def areas_new(
    area_id: str,
    name: str = typer.Option(..., "--name"),
    directory: Path = typer.Option(DATA_DIR / "areas", "--dir", help="Target directory (add it to seeds.areas_dirs if not built-in)."),
) -> None:
    """Write a template JSON for a new legal area."""
    console.print(f"wrote {write_area_template(directory, area_id, name)}")


@cases_app.command("list")
def cases_list(area: Optional[str] = None, config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt) -> None:
    """List case seeds."""
    reg = SeedRegistry(_cfg(config, sets).seeds)
    table = Table("id", "area", "title", "start", "streitwert", "file")
    for c in sorted(reg.cases.values(), key=lambda c: c.id):
        if area and c.area != area:
            continue
        table.add_row(c.id, c.area, c.title, c.start_date or "", f"{c.streitwert or ''}", str(reg.sources[f"case:{c.id}"]))
    console.print(table)


@cases_app.command("show")
def cases_show(case_id: str, config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt) -> None:
    """Show one case seed as JSON."""
    reg = SeedRegistry(_cfg(config, sets).seeds)
    console.print_json(reg.cases[case_id].model_dump_json())


@cases_app.command("new")
def cases_new(
    area: str = typer.Option(..., "--area"),
    case_id: str = typer.Option(..., "--id"),
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option(..., "--description"),
    start_date: Optional[str] = typer.Option(None, "--start-date"),
    streitwert: Optional[float] = typer.Option(None, "--streitwert"),
    documents: Optional[int] = typer.Option(None, "--documents"),
    ris: Optional[list[str]] = typer.Option(None, "--ris", help="RIS Geschäftszahl to use as seed (repeatable)."),
    instructions: str = typer.Option("", "--instructions"),
    directory: Path = typer.Option(DATA_DIR / "cases", "--dir", help="Target directory (add it to seeds.cases_dirs if not built-in)."),
    config: Optional[list[Path]] = ConfigOpt,
    sets: Optional[list[str]] = SetOpt,
) -> None:
    """Create a new case seed JSON (validated against the known areas)."""
    reg = SeedRegistry(_cfg(config, sets).seeds)
    if area not in reg.areas:
        raise typer.BadParameter(f"unknown area {area}; known: {sorted(reg.areas)}")
    seed = CaseSeed(id=case_id, area=area, title=title, description=description, start_date=start_date,
                    streitwert=streitwert, documents_target=documents, ris_geschaeftszahlen=ris or [],
                    extra_instructions=instructions)
    console.print(f"wrote {write_case_seed(directory, seed)}")


def _pipeline(cfg: SynthConfig) -> Any:
    from .pipeline import Pipeline

    return Pipeline(cfg)


def _stage_cmd(stage: str) -> Any:
    def command(
        config: Optional[list[Path]] = ConfigOpt,
        sets: Optional[list[str]] = SetOpt,
        run_name: Optional[str] = RunNameOpt,
        output_root: Optional[str] = OutputOpt,
        area: Optional[list[str]] = AreaOpt,
        case: Optional[list[str]] = CaseOpt,
        concurrency: Optional[int] = ConcOpt,
        orchestrator_model: Optional[str] = OrchOpt,
        generator_model: Optional[str] = GenOpt,
        force: bool = ForceOpt,
        verbose: bool = VerboseOpt,
    ) -> None:
        _logging(verbose)
        cfg = _cfg(config, sets, run_name, output_root, area, case, concurrency, orchestrator_model, generator_model)
        pipe = _pipeline(cfg)
        if stage == "render":
            result = pipe.stage_render(force)
        elif stage == "validate":
            result = pipe.stage_validate()
        else:
            result = asyncio.run(getattr(pipe, f"stage_{stage}")(force))
        _print(result)
        if stage == "validate" and not result.get("ok", False):
            raise typer.Exit(code=1)

    command.__doc__ = f"Run the '{stage}' stage."
    return command


for _stage in ("seeds", "plan", "write", "render", "questions", "validate"):
    app.command(_stage)(_stage_cmd(_stage))


@app.command("run")
def run(
    config: Optional[list[Path]] = ConfigOpt,
    sets: Optional[list[str]] = SetOpt,
    run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt,
    area: Optional[list[str]] = AreaOpt,
    case: Optional[list[str]] = CaseOpt,
    concurrency: Optional[int] = ConcOpt,
    orchestrator_model: Optional[str] = OrchOpt,
    generator_model: Optional[str] = GenOpt,
    until: str = typer.Option("validate", "--until", help="Last stage: seeds, plan, write, render, questions, validate."),
    force: bool = ForceOpt,
    verbose: bool = VerboseOpt,
) -> None:
    """Run all stages (resumable: finished artifacts are reused unless --force)."""
    _logging(verbose)
    cfg = _cfg(config, sets, run_name, output_root, area, case, concurrency, orchestrator_model, generator_model)
    pipe = _pipeline(cfg)
    results = asyncio.run(pipe.run(until, force))
    _print(results)
    console.print(f"run directory: {cfg.run.run_dir.resolve()}")
    if until == "validate" and not results.get("validate", {}).get("ok", False):
        raise typer.Exit(code=1)


@app.command("status")
def status(
    config: Optional[list[Path]] = ConfigOpt,
    sets: Optional[list[str]] = SetOpt,
    run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt,
) -> None:
    """Show per-case progress of a run."""
    cfg = _cfg(config, sets, run_name, output_root)
    pipe = _pipeline(cfg)
    table = Table("case", "area", "ris", "planned docs", "written", "files", "questions")
    for cid, s in pipe.status().items():
        table.add_row(cid, s["area"], str(s["ris"]), str(s.get("planned_documents", "")), str(s.get("written", "")),
                      str(s["rendered_files"]), str(s["questions"]))
    console.print(table)
    usage = cfg.run.run_dir / "reports" / "usage.json"
    if usage.is_file():
        _print(json.loads(usage.read_text(encoding="utf-8")).get("cumulative", {}))


BaseUrlOpt = typer.Option("http://127.0.0.1:11001", "--base-url",
                          help="Backend URL of the stack under test (127.0.0.1, not localhost: on Windows localhost costs ~2 s per connection).")
SampleOpt = typer.Option(5.0, "--sample-interval", help="Seconds between resource samples (GPU, host, containers, Ollama).")
ProfileOpt = typer.Option("synthetic-kanzlei", "--profile", help="Profile the corpus is ingested into.")
DatabaseOpt = typer.Option("rag_synth_kanzlei_v1", "--database", help="MongoDB database of that profile.")
MongoOpt = typer.Option("mongodb://localhost:11017/?directConnection=true", "--mongo-uri")
BenchOpt = typer.Option("bench1", "--bench-name", help="Results go to <run_dir>/bench/<bench-name>/.")


def _bench_ctx(config: Optional[list[Path]], sets: Optional[list[str]], run_name: Optional[str], output_root: Optional[str],
               base_url: str, profile: str, database: str, mongo_uri: str, bench_name: str, sample_interval: float = 5.0) -> Any:
    from .bench import BenchContext

    cfg = _cfg(config, sets, run_name, output_root)
    return BenchContext(run_dir=cfg.run.run_dir, bench_name=bench_name, base_url=base_url, profile=profile,
                        database=database, mongo_uri=mongo_uri, sample_interval_s=sample_interval)


@app.command("bench-ingest")
def bench_ingest_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, base_url: str = BaseUrlOpt, profile: str = ProfileOpt, database: str = DatabaseOpt,
    mongo_uri: str = MongoOpt, bench_name: str = BenchOpt,
    clean: bool = typer.Option(True, "--clean/--incremental", help="Clean the profile database before ingesting."),
    sample_interval: float = SampleOpt, verbose: bool = VerboseOpt,
) -> None:
    """Ingest the run's corpus into a profile of a running stack and benchmark the ingestion."""
    from .bench import bench_ingest

    _logging(verbose)
    ctx = _bench_ctx(config, sets, run_name, output_root, base_url, profile, database, mongo_uri, bench_name, sample_interval)
    result = bench_ingest(ctx, clean=clean)
    _print({k: v for k, v in result.items() if k not in ("timeline", "resource_samples")})


@app.command("bench-retrieval")
def bench_retrieval_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, base_url: str = BaseUrlOpt, profile: str = ProfileOpt, database: str = DatabaseOpt,
    mongo_uri: str = MongoOpt, bench_name: str = BenchOpt, k: int = typer.Option(10, "--k"), verbose: bool = VerboseOpt,
) -> None:
    """Score the search API (hybrid, semantic, text) on the gold questions."""
    from .bench import bench_retrieval, load_gold

    _logging(verbose)
    ctx = _bench_ctx(config, sets, run_name, output_root, base_url, profile, database, mongo_uri, bench_name)
    result = bench_retrieval(ctx, load_gold(ctx.run_dir), k=k)
    _print(result["summary"])


@app.command("bench-chat")
def bench_chat_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, base_url: str = BaseUrlOpt, profile: str = ProfileOpt, database: str = DatabaseOpt,
    mongo_uri: str = MongoOpt, bench_name: str = BenchOpt,
    mode: str = typer.Option("fast", "--mode", help="Agent mode: fast, thinking or auto."),
    limit: Optional[int] = typer.Option(None, "--limit"), question: Optional[list[str]] = typer.Option(None, "--question"),
    keep_sessions: bool = typer.Option(False, "--keep-sessions"),
    concurrency: int = typer.Option(1, "--concurrency", help="Questions in parallel (load probe; results in chat_<mode>_c<N>.jsonl)."),
    sample_interval: float = SampleOpt, verbose: bool = VerboseOpt,
) -> None:
    """Run the gold questions through the streaming chat endpoint and score them (resumable)."""
    from .bench import bench_chat, load_gold

    _logging(verbose)
    ctx = _bench_ctx(config, sets, run_name, output_root, base_url, profile, database, mongo_uri, bench_name, sample_interval)
    summary = bench_chat(ctx, load_gold(ctx.run_dir), mode, limit=limit, keep_sessions=keep_sessions, ids=question,
                         concurrency=concurrency)
    _print({"answerable": summary["answerable"], "failure_stages": summary["failure_stages"], "anomalies": summary.get("anomalies"),
            "telemetry_html": summary.get("telemetry_html")})


@app.command("bench-query-rewrite")
def bench_query_rewrite_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, base_url: str = BaseUrlOpt, profile: str = ProfileOpt, database: str = DatabaseOpt,
    mongo_uri: str = MongoOpt, bench_name: str = BenchOpt,
    chat_file: str = typer.Option("chat_thinking.jsonl", "--chat-file"), verbose: bool = VerboseOpt,
) -> None:
    """Compare retrieval for the planner's queries (as written and sanitized) with the plain question."""
    from .bench import bench_query_rewrite, load_gold

    _logging(verbose)
    ctx = _bench_ctx(config, sets, run_name, output_root, base_url, profile, database, mongo_uri, bench_name)
    _print(bench_query_rewrite(ctx, load_gold(ctx.run_dir), chat_file)["summary"])


@app.command("bench-rescore")
def bench_rescore_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, base_url: str = BaseUrlOpt, profile: str = ProfileOpt, database: str = DatabaseOpt,
    mongo_uri: str = MongoOpt, bench_name: str = BenchOpt,
    mode: Optional[list[str]] = typer.Option(None, "--mode", help="Only these chat modes (default: all)."),
    log_since: Optional[str] = typer.Option(None, "--log-since", help="Also count orchestrator log statistics from this time (RFC 3339)."),
    log_until: Optional[str] = typer.Option(None, "--log-until"),
) -> None:
    """Rescore stored chat results with the current scorer and rewrite their summaries (no LLM calls)."""
    from .bench import load_gold, rescore_chat_files

    ctx = _bench_ctx(config, sets, run_name, output_root, base_url, profile, database, mongo_uri, bench_name)
    for m, s in rescore_chat_files(ctx, load_gold(ctx.run_dir), mode, log_since, log_until).items():
        _print({"mode": m, "answerable": s["answerable"], "failure_stages": s["failure_stages"]})


@app.command("bench-ab")
def bench_ab_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt,
    a: str = typer.Option(..., "--a", help="Bench name of arm A (baseline)."),
    b: str = typer.Option(..., "--b", help="Bench name of arm B."),
    mode: Optional[list[str]] = typer.Option(None, "--mode", help="Chat modes to compare (default: thinking, fast)."),
) -> None:
    """Paired A/B comparison of two benchmark runs, question by question (writes ab_<a>_vs_<b>.json into B)."""
    from .bench import compare_runs

    cfg = _cfg(config, sets, run_name, output_root)
    _print(compare_runs(cfg.run.run_dir / "bench", a, b, tuple(mode) if mode else ("thinking", "fast")))


@app.command("bench-report")
def bench_report_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, bench_name: str = BenchOpt,
    baseline: Optional[str] = typer.Option(None, "--baseline", help="Earlier bench name to compare the chat runs with."),
) -> None:
    """Render REPORT.md and the HTML telemetry pages from the benchmark results of a run."""
    from .bench_report import write_report
    from .telemetry_html import write_all_telemetry_html

    cfg = _cfg(config, sets, run_name, output_root)
    bench_dir = cfg.run.run_dir / "bench"
    for page in write_all_telemetry_html(bench_dir / bench_name):
        console.print(f"wrote {page}")
    console.print(f"wrote {write_report(bench_dir / bench_name, bench_dir / baseline if baseline else None)}")


@app.command("bench-telemetry")
def bench_telemetry_cmd(
    config: Optional[list[Path]] = ConfigOpt, sets: Optional[list[str]] = SetOpt, run_name: Optional[str] = RunNameOpt,
    output_root: Optional[str] = OutputOpt, bench_name: str = BenchOpt,
    run: Optional[list[str]] = typer.Option(None, "--run", help="Series to render, e.g. chat_thinking, chat_fast_c3 or ingest "
                                                                "(default: every resources_*.jsonl of the bench)."),
    verbose: bool = VerboseOpt,
) -> None:
    """Render the one-page HTML telemetry report (GPU, host, containers, questions) of a benchmark run.

    Works while a run is still going: the sampler appends to the files this reads.
    """
    from .telemetry_html import write_all_telemetry_html, write_telemetry_html

    _logging(verbose)
    cfg = _cfg(config, sets, run_name, output_root)
    bench_dir = cfg.run.run_dir / "bench" / bench_name
    pages = [write_telemetry_html(bench_dir, r) for r in run] if run else write_all_telemetry_html(bench_dir)
    if not pages:
        raise typer.BadParameter(f"no resources_*.jsonl in {bench_dir}")
    for page in pages:
        console.print(f"wrote {page}")


def main() -> None:
    """Entry point for ``python -m``."""
    app()
