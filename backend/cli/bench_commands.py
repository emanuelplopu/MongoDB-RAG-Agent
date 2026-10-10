"""``quellexctl bench`` — benchmark and validation commands.

Usage:
    quellexctl bench embeddings --provider llamacpp --model embeddinggemma-2 --base-url http://localhost:11090
    quellexctl bench embeddings --provider openai --model text-embedding-3-small --sample 100 --output checks.json

The command samples real chunks from MongoDB (defaults to ``MONGODB_URI`` /
``MONGODB_DATABASE``), runs :class:`backend.services.embedding_checks.EmbeddingCheckRunner`
against the provider, and prints a summary table (or JSON with ``--format json``).
It talks to MongoDB and the embedding server directly, so it works on a GPU box
without the backend API running.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from backend.cli.main import HAS_TYPER, OutputFormatter, app, console, run_async
from backend.services.embedding_checks import (
    CheckOptions,
    EmbeddingCheckRunner,
    EmbeddingChecksResult,
    ProviderSpec,
    SampleInfo,
)

if HAS_TYPER:
    import typer
    from rich.table import Table

    bench_app = typer.Typer(help="Benchmarks and validation checks (embeddings).", no_args_is_help=True)
    app.add_typer(bench_app, name="bench")
else:  # pragma: no cover - typer missing
    bench_app = None


SampleLoader = Callable[[str, str, str, int], Awaitable[Tuple[List[str], Optional[List[List[float]]], Optional[str]]]]


async def load_index_sample(
    mongodb_uri: str, database: str, collection: str, limit: int
) -> Tuple[List[str], Optional[List[List[float]]], Optional[str]]:
    """Load ``limit`` chunks (content + stored embedding) from MongoDB.

    Returns:
        ``(texts, reference_vectors, reference_model)``; the vectors are ``None``
        when the sampled chunks carry no embeddings.
    """
    from pymongo import AsyncMongoClient

    client = AsyncMongoClient(mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        coll = client[database][collection]
        projection = {"content": 1, "embedding": 1, "metadata.embedding_model": 1}
        docs = [d async for d in coll.find({"embedding": {"$exists": True}}, projection).limit(limit)]
        if not docs:
            docs = [d async for d in coll.find({"content": {"$exists": True}}, projection).limit(limit)]
    finally:
        await client.close()
    texts = [str(d.get("content", "")) for d in docs if d.get("content")]
    vectors = [d["embedding"] for d in docs if d.get("content") and isinstance(d.get("embedding"), list)]
    reference = vectors if len(vectors) == len(texts) and vectors else None
    model = next((d.get("metadata", {}).get("embedding_model") for d in docs if d.get("metadata")), None)
    return texts, reference, model


# Injection points for tests.
SAMPLE_LOADER: SampleLoader = load_index_sample
RUNNER_FACTORY: Callable[[], EmbeddingCheckRunner] = EmbeddingCheckRunner


def _parse_int_list(value: str) -> List[int]:
    return [int(v) for v in value.split(",") if v.strip()]


def _summary_rows(result: EmbeddingChecksResult) -> List[List[str]]:
    rows: List[List[str]] = []
    for r in result.results:
        if not r.success:
            rows.append([r.provider, "FAILED", r.error or "", "", "", "", "", ""])
            continue
        best = max(r.throughput, key=lambda p: p.tokens_per_s) if r.throughput else None
        trunc = ", ".join(f"{t.dim}d={t.topk_overlap:.2f}" for t in r.truncation) or "-"
        retrieval = f"{r.retrieval.top1_hits}/{r.retrieval.queries} top1, {r.retrieval.top5_hits}/{r.retrieval.queries} top5" if r.retrieval else "-"
        image = "yes" if r.image and r.image.supported else ("no" if r.image else "-")
        rows.append(
            [
                r.provider,
                f"{r.vectors.dimension}d{' ✓' if r.vectors.normalized else ' (not normalised)'}" if r.vectors else "-",
                f"{r.latency.p50_ms:.0f} / {r.latency.p95_ms:.0f} ms" if r.latency else "-",
                f"{best.texts_per_s:.1f} texts/s, {best.tokens_per_s:.0f} tok/s (b{best.batch_size})" if best else "-",
                trunc,
                retrieval,
                image,
                f"{r.agreement_with_index:.2f}" if r.agreement_with_index is not None else "-",
            ]
        )
    return rows


if HAS_TYPER:

    @bench_app.command("embeddings")
    def embeddings(
        provider: str = typer.Option("llamacpp", "--provider", help="llamacpp | openai | ollama | vllm | custom"),
        model: str = typer.Option("embeddinggemma-2", "--model", "-m", help="Model name as the server expects it"),
        base_url: Optional[str] = typer.Option(None, "--base-url", help="Server root (llama.cpp/ollama) or /v1 URL"),
        api_key: Optional[str] = typer.Option(None, "--api-key", envvar="EMBEDDING_API_KEY"),
        mongodb_uri: str = typer.Option(
            os.environ.get("MONGODB_URI", "mongodb://localhost:11017/?directConnection=true"), "--mongodb-uri"
        ),
        database: str = typer.Option(os.environ.get("MONGODB_DATABASE", "rag_db"), "--database", "-d"),
        collection: str = typer.Option("chunks", "--collection", "-c"),
        sample: int = typer.Option(200, "--sample", "-n", min=10, max=2000, help="Chunks to sample from the index"),
        batch_sizes: str = typer.Option("1,8,32,64", "--batch-sizes"),
        truncate_dims: str = typer.Option("512,256", "--truncate-dims", help="MRL dims to test; empty to skip"),
        truncate_dim: Optional[int] = typer.Option(None, "--truncate-dim", help="Truncate every output to this dim"),
        latency_runs: int = typer.Option(10, "--latency-runs", min=1, max=100),
        images: bool = typer.Option(True, "--images/--no-images", help="Probe image embedding (llama.cpp)"),
        compare_index: bool = typer.Option(True, "--compare-index/--no-compare-index", help="Rank-agreement vs stored vectors"),
        query_prefix: Optional[str] = typer.Option(None, "--query-prefix", help="Override auto-detected task prefix"),
        document_prefix: Optional[str] = typer.Option(None, "--document-prefix", help="Override auto-detected task prefix"),
        output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write the full JSON result here"),
    ) -> None:
        """Run the embedding validation checks against real chunks from the index."""
        fmt = OutputFormatter(app.state.get("format", "table") if hasattr(app, "state") else "table")

        async def _run() -> EmbeddingChecksResult:
            texts, reference, reference_model = await SAMPLE_LOADER(mongodb_uri, database, collection, sample)
            if not texts:
                raise RuntimeError(f"No chunks with content found in {database}.{collection}")
            spec = ProviderSpec(
                provider_type=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                query_prefix=query_prefix or "",
                document_prefix=document_prefix or "",
                truncate_dim=truncate_dim,
            )
            options = CheckOptions(
                sample_size=sample,
                batch_sizes=_parse_int_list(batch_sizes) or [32],
                truncate_dims=_parse_int_list(truncate_dims),
                latency_runs=latency_runs,
                check_images=images,
            )
            info = SampleInfo(
                source="index",
                count=len(texts),
                avg_chars=round(sum(len(t) for t in texts) / len(texts), 1),
                database=database,
                collection=collection,
                reference_model=reference_model if (compare_index and reference) else None,
                reference_dimension=len(reference[0]) if (compare_index and reference) else None,
            )
            return await RUNNER_FACTORY().run(
                [spec], texts, options, info, reference_vectors=reference if compare_index else None
            )

        try:
            result = run_async(_run())
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error(str(exc))
            raise typer.Exit(code=1)

        if output:
            output.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        if fmt.format == "json":
            print(result.model_dump_json(indent=2))
        else:
            headers = ["Provider", "Vectors", "Query p50 / p95", "Best throughput", "MRL overlap", "Pseudo-query recall", "Images", "Index agreement"]
            fmt.print_table(headers, _summary_rows(result), title=f"Embedding checks — {result.sample.count} chunks from {database}.{collection}")
            for r in result.results:
                if r.hardware and r.hardware.gpus:
                    gpus = ", ".join(f"{g.name} ({g.memory_total_mb} MB)" for g in r.hardware.gpus)
                    console.print(f"[dim]GPU(s) on {r.hardware.hostname}: {gpus}[/dim]")
                for warning in r.warnings:
                    console.print(f"[yellow]warning:[/yellow] {r.provider}: {warning}")
            if output:
                fmt.print_success(f"Full result written to {output}")

        if any(not r.success for r in result.results):
            raise typer.Exit(code=2)
