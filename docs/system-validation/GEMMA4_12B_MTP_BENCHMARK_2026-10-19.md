# Gemma 4 12B: multi-token-prediction drafter benchmark (2026-10-19)

Machine: DESKTOP-8ESH62J (Core Ultra 9 285HX, RTX PRO 2000 Blackwell 16 GB at its 70 W power cap), Windows 11,
Ollama 0.40.6 (native app, llama.cpp/CUDA runner), `rag-embedder-gpu` container resident on the same GPU (~3.7 GB).

## Why

Ollama re-published every `gemma4` tag around 2026-10-04. The new builds are imatrix-calibrated Q4_K_M
quantizations with a separate vision projector and a `draft` layer holding Google's MTP drafter
(`draft_num_predict 3`). The copies on this PC were the April/June 2026 builds without a drafter. This run
measures what the re-pull changes for the production model `gemma4:12b`.

## Models

| Tag | Weights | Drafter | Note |
|---|---|---|---|
| `gemma4:12b-june` | June 2026 build, Q4_K_M, blob `1278394b…` | none | `ollama cp` of the previous `gemma4:12b` |
| `gemma4:12b-nodraft` | new build, Q4_K_M imatrix, blob `bb722270…` | none | built from the new blobs minus the `DRAFT` line |
| `gemma4:12b` | new build, same blob `bb722270…` | `7008a656…` (465 MB, gemma4-assistant, Q8_0) | registry manifest `6114515d63c1`, pulled 2026-10-19 23:05 |

## Method

Three German prompts of ~34 k characters (8.4–9.3 k tokens) built from the synthetic law-firm corpus
(`C:\RecallHub_Synthetic\kanzlei_v1\written`, areas arbeitsrecht_01, bau_01, datenschutz_01) followed by a
structured-answer instruction. `/api/generate`, `num_ctx 16384`, `num_predict 512`, `temperature 0`, `seed 42`,
`think false` where the model supports thinking. Order per prompt: june, nodraft, draft; two repetitions each.
Rep 1 is a cold prefill (the model switch drops the prompt cache), rep 2 measures decode on a cached prompt.
Scripts: `scripts/ollama-mtp-bench/`. Raw rows: `gemma4-12b-mtp-2026-10-19-compare.jsonl` and
`gemma4-12b-mtp-2026-10-19-june-baseline.jsonl` next to this file.

## Results

| Model | Decode tok/s (median, 6 runs) | Decode range | Cold prefill tok/s (median) | Load s | GPU memory used (nvidia-smi, incl. embedder) |
|---|---|---|---|---|---|
| `gemma4:12b-june` | 30.2 | 30.0–30.4 | 1313 | 4.0 | 12.5 GB |
| `gemma4:12b-nodraft` | 30.1 | 30.0–30.3 | 1287 | 4.0 | 12.5 GB |
| `gemma4:12b` (MTP) | 50.9 | 50.1–55.3 | 1232 | 5.1 | 13.1 GB |

- Decode speed-up from the drafter: 1.67x–1.82x (median 1.69x) on identical weights.
- The re-quantization itself changes speed by nothing measurable (june vs nodraft).
- Prefill is ~4–6 % slower with the drafter (the draft model also ingests the prompt); load time +1 s;
  ~600 MB more GPU memory. `ollama ps` reports 1.5 GB for the drafter model, a known reporting bug
  (ollama/ollama#17251).
- llama-server was started with `--spec-type draft-mtp --spec-draft-n-max 3 --spec-draft-backend-sampling`
  and logged draft acceptance 0.56–0.61 with a mean accepted length of 2.7 of 3 draft tokens per step.
- The GPU sat at its 70 W cap for every run; temperature rose from 44 °C to 93 °C across the series and the
  drafter runs came last, so the gain is not a thermal artefact.
- Outputs: the drafter and no-drafter answers start identically (first 110 characters equal on all three
  prompts) and diverge later; rep 1 and rep 2 of the *same* model also differ, so greedy decoding is not
  bit-reproducible on this stack regardless of the drafter. The June build answers in a letter style
  ("Sehr geehrte Kolleginnen und Kollegen…"), the new build mostly starts with a heading; both are usable.

## What it means for a RAG answer here

For a 9 k-token prompt and a 512-token answer: June ≈ 7.0 s prefill + 17.0 s decode ≈ 24 s; new build with
drafter ≈ 7.4 s + 10.0 s ≈ 17.4 s, i.e. ~1.4x end to end. Short prompts approach the 1.7x decode figure.
The "2.85x"/"up to 3x" marketing numbers are for dense 31B on A100 or Pixel TPU paths; Google's own chart
gives 1.5x for the 26B MoE on an A100.

## Operational notes

- The production tag `gemma4:12b` now resolves to the new build; the backend's agent path disables thinking
  via `reasoning_effort none` (`backend/agent/ollama_options.py`), which matters because the new build has
  `thinking` capability with default `true` while the June build had none.
- Rollback: `ollama cp gemma4:12b-june gemma4:12b`.
- The evaluation baseline should be re-run on the new weights; they are a different quantization.
- Ollama 0.40.6 was installed from the app's staged bundle through a one-shot scheduled task so the relaunched
  app does not live in a Claude session's process tree. Right after the relaunch the server answered pulls with
  "upgrade in progress" for about a minute and restarted itself once more.
- The pull (8.0 GB) took 93 min over the TCL-TNRZ uplink (0.4–2.7 MB/s).
