# Quellex Profiler

## Overview

Standalone, dependency-free hardware profiling tool for pre-sales assessment of client machines.
Inspects the host system and recommends the optimal Quellex/RecallHub configuration — which model
orchestrates, which models handle worker tasks, which embedding model to use, expected throughput,
privacy score, and monthly cost range.

Built for law-firm (Kanzlei) and enterprise demos: **no Quellex install, no MongoDB, no Docker,
no network calls** required on the target machine.

Source lives in `quellex-profiler/` at the repo root.

## Purpose

- Profile client hardware **before** a RecallHub/Quellex deployment.
- Recommend concrete model assignments across orchestrator, worker, and embedding roles.
- Generate a privacy score reflecting how much data stays on-premise.
- Estimate monthly cloud-API costs based on a 10-user, 200k-query/month workload.
- Run as a single binary — no runtime, no install, no admin rights needed.

## Supported Platforms

| OS      | Architectures | GPU Detection                                  |
|---------|---------------|-------------------------------------------------|
| Windows | x86_64, ARM64 | `nvidia-smi` + WMI `Win32_VideoController`      |
| macOS   | x86_64, ARM64 | `nvidia-smi` + `system_profiler` + `sysctl`     |
| Linux   | x86_64, ARM64 | `nvidia-smi` + `lspci` + `rocm-smi` (optional)  |

## Hardware Detection

- **CPU** — brand, vendor, physical/logical core count, max clock speed, instruction flags (AVX, AVX2, AVX-512).
- **RAM** — total physical memory in GB.
- **GPU** — NVIDIA (CUDA cores, VRAM, compute capability, driver version), AMD discrete + integrated, Intel Arc + integrated (UHD/Iris), Apple Silicon unified memory.
- **Architecture** — x86_64 vs ARM64, Apple Silicon flag.

## 5-Tier Model Recommendations

| Tier | Label | Requirements | Orchestrator | Worker | Embedding |
|------|-------|-------------|-------------|--------|-----------|
| 1 | Cloud-Only | < 16 GB RAM or no capable GPU | GPT-4o (cloud) | Gemini 2.0 Flash (cloud) | OpenAI text-embedding-3-small |
| 2 | Hybrid Small | 16-32 GB RAM, iGPU or 4-8 GB VRAM | Gemini 2.5 Pro (cloud) | Llama 3.2 3B (local) | BGE Small EN v1.5 (local) |
| 3 | Local Balanced | 32-64 GB RAM, 10-20 GB VRAM / Apple 32 GB+ | Qwen 2.5 14B or Llama 3.1 8B | Llama 3.2 3B | BGE-M3 |
| 4 | Local Premium | 64+ GB RAM, 22+ GB VRAM / Apple 64 GB+ | Qwen 2.5 32B | Llama 3.1 8B | BGE-M3 |
| 5 | Workstation | 128+ GB RAM, 48+ GB VRAM | Llama 3.3 70B | Qwen 2.5 14B | BGE-M3 |

Older NVIDIA cards (compute capability < 7.0) are automatically downgraded one tier.

## Privacy Scoring

| Component runs locally | Points |
|------------------------|--------|
| Embeddings             | +50    |
| Worker model           | +25    |
| Orchestrator model     | +25    |

Score range: **0** (everything cloud) to **100** (fully on-premise).
Embedding locality weighs heaviest because it determines whether raw documents ever leave the firm.

## Cost Estimation

Monthly EUR estimates assume a 10-user Kanzlei with ~200k queries/month.
Cloud-only tier shows the highest cost; fully local tiers report €0.
Estimates are labelled approximate and intended for demo/comparison purposes only.

## CLI Usage

```
quellex-profiler                         # detect + open local web dashboard
quellex-profiler --serve                 # start dashboard without auto-opening browser
quellex-profiler --json                  # print full JSON report to stdout
quellex-profiler --html report.html      # write single-file offline HTML report
quellex-profiler --port 17600            # override default port (17645)
quellex-profiler --host 0.0.0.0          # bind all interfaces
quellex-profiler --version              # print version
```

## Output Formats

1. **Web Dashboard** (default) — local HTTP server at `http://127.0.0.1:17645/` with interactive SVG diagram and print-friendly PDF export.
2. **JSON** (`--json`) — machine-readable `ProfilerReport` for automation and CI/CD pipelines.
3. **HTML Report** (`--html PATH`) — self-contained single-file HTML for client handover.

## Building Single-File Binaries

Requires building on a host matching the target OS + architecture (PyInstaller does not cross-compile).

```powershell
# Windows
.\build.ps1
```

```bash
# macOS / Linux
./build.sh
```

Artifacts land under `dist/`:
- `quellex-profiler-win-x64.exe`
- `quellex-profiler-win-arm64.exe`
- `quellex-profiler-macos-x64`
- `quellex-profiler-macos-arm64`
- `quellex-profiler-linux-x64`
- `quellex-profiler-linux-arm64`

## Running from Source (development)

```powershell
cd quellex-profiler
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m quellex_profiler
```

## Tests

```powershell
cd quellex-profiler
pip install -e ".[test]"
pytest
```

26 tests cover detection parsers and golden tier classifications.

## Integration with RecallHub / Quellex

- **profiles.yaml** — JSON output can pre-populate the `profiles.yaml` model assignments used by the backend.
- **backend/core/config.py** — tier recommendations map directly to the model-selection settings consumed at runtime.
- **Privacy score** — informs data-handling policies: score < 50 means documents will transit cloud APIs and may require additional client consent.

## Architecture

```
quellex_profiler/
├── detect/          # OS, CPU, RAM, GPU detection (platform-specific)
├── recommend/
│   ├── engine.py    # SystemProfile → Recommendation
│   ├── models.py    # Model catalog + tok/s lookup table
│   └── tiers.py     # Tier classification rules
├── report/
│   ├── html_renderer.py
│   └── json_export.py
├── ui/              # Embedded web dashboard (HTML/CSS/JS)
├── schema.py        # Typed dataclasses: SystemProfile, Recommendation, ProfilerReport
├── server.py        # Local HTTP server for dashboard
└── cli.py           # Entry point and argument parsing
```

## Notes

- This tool performs **static analysis and recommendation only**. It does not download models, run inference, or contact any external API.
- Tokens/sec estimates come from a curated lookup table of public benchmarks, not live measurement.
- AMD discrete GPU support through Ollama requires ROCm on Linux; on Windows, Ollama falls back to CPU.
- Intel Arc dGPU inference via Ollama is experimental — the tool will flag this in caveats.
