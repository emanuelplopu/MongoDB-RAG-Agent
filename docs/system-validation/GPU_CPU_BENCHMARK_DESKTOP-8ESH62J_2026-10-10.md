# Local embedding and GPU stress benchmark — DESKTOP-8ESH62J (2026-10-10)

Host: Intel Core Ultra 9 285HX (24 cores, 8P+16E, no HT), 256 GB RAM, NVIDIA RTX PRO 2000 Blackwell 16 GB
(driver 596.36, CUDA 13.2, 70 W power limit, WDDM). Docker Desktop VM: 23 CPUs, 23.5 GB.
Software: `ghcr.io/ggml-org/llama.cpp:server` / `:server-cuda` build b11541-f2918cabb, repo @ `c032ddf` + this change.
Raw results (JSON) sit next to this file.

## 1. EmbeddingGemma 2 validation checks (`quellexctl bench embeddings`)

Sample: 1,000 `rag_parhelion.chunks` documents (avg 694 chars, 222 Gemma tokens incl. the `title: none | text: `
prefix), reference vectors `text-embedding-3-large` 3072-d. Same chunks for every run. Servers started from the
compose profiles `embedder-gpu` (CUDA, `-ngl 999`) and `embedder` (CPU, `EMBEDDER_THREADS` 20 and 8).

| Metric | GPU RTX PRO 2000 (CUDA, all layers) | CPU 20 threads | CPU 8 threads (compose default) |
|---|---|---|---|
| Query latency cold | 130 ms | 78 ms | 73 ms |
| Query latency warm p50 / p95 | **48 / 50 ms** | **60 / 66 ms** | **59 / 62 ms** |
| Throughput batch 1 (32 texts) | 17.3 chunks/s, 454 tok/s | 15.8 chunks/s, 415 tok/s | - |
| Throughput batch 8 | 62.3 chunks/s, 13,828 tok/s | 9.2 chunks/s, 2,047 tok/s | - |
| Throughput batch 32 | 93.7 chunks/s, 20,786 tok/s | 9.7 chunks/s, 2,147 tok/s | 6.0 chunks/s, 1,330 tok/s |
| Throughput batch 64 | 92.9 chunks/s, 20,610 tok/s | 9.7 chunks/s, 2,151 tok/s | 5.8 chunks/s, 1,294 tok/s |
| Throughput batch 128 | 91.7 chunks/s, 20,338 tok/s | 9.7 chunks/s, 2,156 tok/s | - |
| Best throughput | **20,786 tok/s** (b32) | **2,156 tok/s** (b128) | **1,330 tok/s** (b32) |
| Vectors | 768-d, L2-normalised | 768-d, L2-normalised | 768-d, L2-normalised |
| MRL top-10 overlap 768 vs 512 / 256 | 0.919 / 0.838 | 0.921 / 0.839 | 0.920 / - |
| Pseudo-query recall top-1 / top-5 (100 queries) | 32 / 55 | 31 / 55 | 30 / 55 |
| Image embed (64 px PNG) | ok, 85 tok, 121 ms | ok, 85 tok, 416 ms | skipped |
| Top-10 agreement with stored 3-large vectors | 0.605 | 0.605 | 0.605 |
| Total check time | 52 s | 423 s | 341 s (2 batch sizes only) |

Reading the numbers:

- **The GPU is 9.7x faster than all 20 CPU threads** for document embedding (20.8K vs 2.15K Gemma tok/s) and
  15.6x faster than the compose default of 8 threads. Re-embedding the full 432K-chunk `rag_parhelion`
  index (~76 M Gemma tokens, see `EMBEDDINGGEMMA2_EVALUATION_2026-10-10.md`) would take **~1 h on this GPU**,
  ~10 h on 20 CPU threads, ~16 h on 8 threads.
- Throughput saturates at batch 32 on the GPU; larger batches do not help. The 4 server slots are not the
  limit either: during the benchmark the GPU sat at 88-93 % utilisation and exactly its 70 W power cap
  (87-90 C), so the card is power-bound. The client sends batches sequentially; concurrent clients give the
  same ~20K tok/s (see stress test below).
- Single-query latency is CPU-friendly: 48 ms on the GPU vs 60 ms on the CPU. A query-only deployment does
  not need the GPU; ingestion does.
- 8 -> 20 threads gives only 1.6x on the CPU because the extra threads land on E-cores. Set
  `EMBEDDER_THREADS=20` on this box (the compose default of 8 is tuned for smaller machines).
- Quality checks are identical across devices (same vectors): MRL 512-d keeps 92 % of the top-10
  neighbours, 256-d 84 %; agreement with the stored OpenAI vectors is 0.60 as in the earlier evaluation.
- The CLI summary table crashed on this Windows console (cp1252) because of a check-mark character; the
  table now uses ASCII. Run CLIs here with `PYTHONUTF8=1` anyway (the PowerShell wrappers set it).

## 2. GPU thermal / mixed-placement stress test (`quellexctl bench gpu-stress`)

Suite `backend/config/gpu_stress/gemma4.yaml`, 180 s load per scenario (default is 120 s), 10 s warmup,
30 s cooldown, 1 s telemetry. Models from ggml-org GGUFs: EmbeddingGemma 2 Q8_0, Gemma 4 E2B Q8_0,
Gemma 4 E4B Q8_0, Gemma 4 26B-A4B Q4_0 (MoE, 26B total / 4B active). Chat load = 4 clients,
300-word prompts, 128 completion tokens; embedding load = 4 clients x 32 texts.

Raw data: `gpu-stress-2026-10-10-rtx-pro-2000.json` (per-second telemetry included). Scenarios were run in
four invocations as the GGUFs finished downloading (~6 MB/s from Hugging Face) and merged; the extra row
`embeddinggemma-2-gpu-run1-cold` is the very first run of the evening, see the variance note below.

### Throughput and placement

| Scenario | Model (placement) | Load s | VRAM +MB | Layers GPU | Weights MB GPU / CPU-mapped | tok/s | prompt tok/s | req/s | p95 ms | Errors |
|---|---|---|---|---|---|---|---|---|---|---|
| embeddinggemma-2-gpu | embeddinggemma-2 (gpu) | 4.4 | 1,698 | 25/25 | 144 / 136 | 17,236 in | 17,236 | 2.19 | 2,310 | 0 |
| gemma4-e2b-gpu | gemma4-e2b (gpu) | 6.5 | 2,707 | 36/36 | 2,342 / 2,788 | 190.5 gen | 758 | 1.49 | 2,814 | 0 |
| gemma4-e4b-gpu | gemma4-e4b (gpu) | 8.7 | 5,354 | 43/43 | 4,788 / 3,536 | 117.6 gen | 468 | 0.92 | 4,540 | 0 |
| gemma4-26b-split | gemma4-26b (split, `--fit on`) | 17.3 | 14,116 | 31/31 | 12,285 / 13,923 | 66.7 gen | 266 | 0.52 | 8,110 | 0 |
| gemma4-26b-cpu-moe | gemma4-26b (gpu + experts in RAM) | 6.5 | 3,498 | 31/31 | 1,675 / 13,923 | 28.4 gen | 113 | 0.22 | 22,190 | 0 |
| mixed-gpu-and-ram | embeddinggemma-2 (gpu) | 4.3 | 1,695 | 25/25 | 144 / 136 | 9,819 in | 9,819 | 1.25 | 2,002 | 0 |
| mixed-gpu-and-ram | gemma4-e2b (gpu) | 6.5 | 2,707 | 36/36 | 2,342 / 2,788 | 112.7 gen | 450 | 0.88 | 4,921 | 0 |
| mixed-gpu-and-ram | gemma4-e4b (cpu, 16 threads) | 6.5 | 0 | - | 0 / 7,644 | 6.8 gen | 55 | 0.11 | 19,470 | 0 |
| embeddinggemma-2-gpu-run1-cold | embeddinggemma-2 (gpu) | 4.4 | 1,698 | - | - | 22,921 in | 22,921 | 2.92 | 1,455 | 0 |

`tok/s` is generated tokens/s summed over the 4 clients for chat models ("gen") and input tokens/s for the
embedder ("in"). Per-stream generation speed reported by llama.cpp: E2B 55 tok/s, E4B 34, 26B in VRAM 21,
26B with experts in RAM 10, E4B on CPU 7.7. "CPU-mapped" is llama.cpp's `CPU_Mapped` buffer: for the
E-models it is the per-layer embedding table that always stays in RAM, for the 26B it is the whole
memory-mapped file, so use `Layers GPU`, the GPU buffer and `VRAM +MB` to read placement.

### Thermal

| Scenario | Temp start/max/end C | To max s | Power mean/limit W | GPU util % | SM clock mean/min MHz | VRAM max MB | Throttle (load) | Host CPU % |
|---|---|---|---|---|---|---|---|---|
| embeddinggemma-2-gpu | 71 / 95 / 72 | 42 | 66 / 70 | 57 | 2,357 / 2,197 | 3,073 | power_cap 180s | 25 |
| gemma4-e2b-gpu | 74 / 95 / 76 | 61 | 70 / 70 | 81 | 2,172 / 1,987 | 3,986 | power_cap 180s | 16 |
| gemma4-e4b-gpu | 52 / 95 / 74 | 142 | 70 / 70 | 86 | 2,087 / 1,740 | 6,575 | power_cap 182s | 18 |
| gemma4-26b-split | 74 / 95 / 74 | 82 | 64 / 70 | 48 | 2,496 / 1,995 | 15,345 | power_cap 163s | 45 |
| gemma4-26b-cpu-moe | 74 / 87 / 75 | 105 | 42 / 70 | 28 | 2,355 / 1,552 | 4,859 | power_cap 12s | 69 |
| mixed-gpu-and-ram | 75 / 95 / 78 | 68 | 69 / 70 | 88 | 2,058 / 817 | 5,756 | power_cap 182s | 80 |
| embeddinggemma-2-gpu-run1-cold | 66 / 94 / 75 | 49 | 70 / 70 | 84 | 2,145 / 2,085 | 2,995 | power_cap 177s | 19 |

Fan peaked at 84-91 % in the GPU-bound scenarios (55 % with experts in RAM). Host RAM use peaked at 54 GB
(Docker VM page cache holding the 26 GB of GGUFs plus the models themselves).

### What this says about the box

- **The RTX PRO 2000 is power-limited, not thermally throttled.** Every GPU-bound scenario hit the 70 W cap
  for essentially the whole 180 s and settled at 94-95 C (reached after 42-142 s). `nvidia-smi` never
  raised the HW or SW thermal-slowdown flag; the only active throttle reason was `sw_power_cap`. SM clocks
  held 2.0-2.5 GHz on average. Expect ~95 C as the steady state for any sustained ingestion or chat load on
  this card; it recovers to 72-78 C within 30 s of idling. For multi-hour re-embedding runs keep the mini
  PC's airflow clear, but the card does protect itself via the power limit.
- **Mixed load is where clocks dip.** With the embedder and E2B on the GPU plus E4B on 16 CPU threads, the SM
  clock dropped to 817 MHz at one point, the card visited P3 and the memory clock stepped from 9001 to
  7001 MHz, while the host CPU sat at 80 %. Nothing failed (0 errors in 407 requests) but GPU throughput
  halved for both resident models (embedder 9.8K vs 17.2K tok/s, E2B 113 vs 190 tok/s) and the CPU model
  delivered 6.8 tok/s with a 19 s p95. Running a RAM-resident LLM next to the GPU workloads is possible
  but costs roughly half of the GPU throughput.
- **26B-A4B fits entirely in VRAM** on this 16 GB card at Q4_0 with 4 x 8192 context: `--fit on` kept all 31
  layers on the GPU (12.3 GB weights, 15.3 GB total VRAM in use) and generated 67 tok/s across 4 clients at
  48 % GPU utilisation (memory-bound MoE decode, 64 W). Forcing the experts into system RAM (`--cpu-moe`)
  frees 11 GB of VRAM but drops generation to 28 tok/s, the GPU idles at 28 % / 42 W and the CPU becomes the
  bottleneck (69 %). On this machine the RAM split only makes sense if the VRAM is needed for something else.
- **Gemma 4 E2B / E4B at Q8_0** give 190 / 118 tok/s aggregate (55 / 34 per stream) at 4 GB / 6.6 GB VRAM. Both
  keep a 2.8-3.5 GB per-layer embedding table in system RAM by design, so plan RAM for them even when "on
  the GPU".
- **Embedder throughput varies with host state.** The first, cold run reached 22.9K tok/s at 84 % GPU
  utilisation; the two later runs of the identical scenario gave 17.0-17.2K at 57-60 % utilisation (the
  sequential `bench embeddings` client measured 20.8K). The GPU was not the limit in the slower runs; they
  followed 26 GB of GGUF downloads/loads through the 23.5 GB Docker VM. Budget 17-23K tok/s for ingestion
  planning and re-run this scenario first on a fresh VM when comparing machines.

### Rerunning on other hardware

```powershell
.\scripts\gpu-stress.ps1 -Duration 180 -Output docs/system-validation/gpu-stress-<date>-<gpu>.json
```

First run downloads ~26 GB of GGUFs into the `recallhub_embedder_models` volume. Compare `temp_max_c`,
`throttle_seconds`, `sm_clock_min_load_mhz` and `power_mean_load_w` for thermals, `tokens_per_s` per model
for speed, and `offloaded_layers` / `vram_delta_mb` to see what a smaller card has to spill to RAM.
