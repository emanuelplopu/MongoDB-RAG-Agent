<#
.SYNOPSIS
    Run the embedding validation checks against real chunks from the index.

.DESCRIPTION
    Wrapper around `quellexctl bench embeddings`. Samples chunks from MongoDB,
    embeds them with the given provider and reports query latency, throughput
    by batch size, vector normalisation, Matryoshka truncation stability,
    pseudo-query retrieval sanity, image support (llama.cpp) and ranking
    agreement with the vectors already stored in the index.

    Works against the compose `embedder` (CPU) / `embedder-gpu` (NVIDIA)
    services on http://localhost:11090, or any OpenAI-compatible endpoint.

.EXAMPLE
    .\scripts\benchmark-embeddings.ps1 -Database rag_parhelion
    .\scripts\benchmark-embeddings.ps1 -Provider openai -Model text-embedding-3-small -Sample 100 -Json
    .\scripts\benchmark-embeddings.ps1 -BaseUrl http://gpu-box:11090 -Output checks-gpu.json
#>
param(
    [string]$Provider = "llamacpp",
    [string]$Model = "embeddinggemma-2",
    [string]$BaseUrl = "http://localhost:11090",
    [string]$MongoUri = $(if ($env:MONGODB_URI) { $env:MONGODB_URI } else { "mongodb://localhost:11017/?directConnection=true" }),
    [string]$Database = $(if ($env:MONGODB_DATABASE) { $env:MONGODB_DATABASE } else { "rag_db" }),
    [string]$Collection = "chunks",
    [int]$Sample = 200,
    [string]$BatchSizes = "1,8,32,64",
    [string]$TruncateDims = "512,256",
    [int]$LatencyRuns = 10,
    [switch]$NoImages,
    [switch]$NoCompareIndex,
    [string]$Output = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $args = @()
    if ($Json) { $args += @("--format", "json") }
    $args += @(
        "bench", "embeddings",
        "--provider", $Provider,
        "--model", $Model,
        "--base-url", $BaseUrl,
        "--mongodb-uri", $MongoUri,
        "--database", $Database,
        "--collection", $Collection,
        "--sample", $Sample,
        "--batch-sizes", $BatchSizes,
        "--truncate-dims", $TruncateDims,
        "--latency-runs", $LatencyRuns
    )
    if ($NoImages) { $args += "--no-images" }
    if ($NoCompareIndex) { $args += "--no-compare-index" }
    if ($Output) { $args += @("--output", $Output) }

    & uv run python -m backend.cli @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
