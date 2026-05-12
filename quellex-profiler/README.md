# Quellex Profiler

Standalone, dependency-free profiler that inspects a prospective client's
machine and visualises exactly how Quellex would be configured on it -
which model orchestrates, which models are workers, which embedding model,
expected tokens/sec, privacy score, and cost range.

Built for Kanzlei / client demos: no Quellex install, no MongoDB, no Docker,
no network calls required on the target machine.

## Supported platforms

| OS      | Architectures   | GPU detection                                |
|---------|-----------------|----------------------------------------------|
| Windows | x86_64, ARM64   | nvidia-smi + WMI `Win32_VideoController`     |
| macOS   | x86_64, ARM64   | nvidia-smi + `system_profiler` + `sysctl`    |
| Linux   | x86_64, ARM64   | nvidia-smi + `lspci` + `rocm-smi` (optional) |

Detectors work for NVIDIA discrete, AMD discrete + integrated, Intel Arc +
integrated (UHD/Iris), and Apple Silicon unified memory.

## Running (no build)

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m quellex_profiler
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m quellex_profiler
```

The dashboard opens at `http://127.0.0.1:17645/`.

### CLI flags

```
quellex-profiler                         # detect + open local dashboard
quellex-profiler --serve                 # same, but don't auto-open browser
quellex-profiler --json                  # print full report to stdout
quellex-profiler --html report.html      # write offline single-file report
quellex-profiler --port 17600            # override bind port
quellex-profiler --host 0.0.0.0          # bind all interfaces (not recommended)
```

## Building single-file binaries

Requires building on a host matching the target OS + architecture.
PyInstaller does not cross-compile.

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

Double-click or run from a terminal. The tool prints the local URL and opens
the default browser.

## Tests

```powershell
# from quellex-profiler/
pip install -e ".[test]"
pytest
```

## Tiers

| Tier              | Requirements                                   | Orchestrator              | Worker          | Embedding            |
|-------------------|------------------------------------------------|---------------------------|-----------------|----------------------|
| Cloud-Only        | < 16 GB RAM or no capable GPU                  | GPT-4o (cloud)            | Gemini 2.0 Flash| OpenAI v3 small      |
| Hybrid-Small      | 16-32 GB RAM, iGPU or 4-8 GB VRAM dGPU         | Gemini 2.5 Pro (cloud)    | Llama 3.2 3B    | BGE Small EN v1.5    |
| Local-Balanced    | 32-64 GB RAM, 10-20 GB VRAM / Apple 32 GB+     | Qwen 2.5 14B or Llama 3.1 8B | Llama 3.2 3B | BGE-M3               |
| Local-Premium     | 64+ GB RAM, 22+ GB VRAM / Apple 64 GB+         | Qwen 2.5 32B              | Llama 3.1 8B    | BGE-M3               |
| Workstation       | 128+ GB RAM, 48+ GB VRAM                       | Llama 3.3 70B             | Qwen 2.5 14B    | BGE-M3               |

Older NVIDIA cards (compute capability < 7.0) are auto-downgraded one tier.

## Privacy score

- Local embeddings: +50 (documents never leave the firm)
- Local worker:     +25
- Local orchestrator: +25

## Notes

This tool is a static analysis + recommendation engine only. It does NOT
download models, run inference, or contact Quellex / external APIs.
Tokens/sec estimates come from a curated lookup table of public benchmarks.
