# Scanned pages: OCR text vs. direct image embedding (spike, 2026-10-15)

**Question.** For scanned documents, does embedding the page *image* directly
with multimodal EmbeddingGemma 2 retrieve the right documents better than
embedding the Docling OCR *text* the production pipeline stores today?

**Verdict: no — not as a drop-in.** In the realistic mixed index (scans
competing with clean text documents) image embedding is clearly **worse** than
OCR text, and adding the image vector on top of the OCR chunks (hybrid) gains
**nothing**. Image embedding only wins when scanned pages compete solely against
*other scanned pages*. The cause is measured and structural: a text query's
cosine to a page image is ~0.06–0.08 lower than to text, so in one shared
cosine index image vectors are outranked even by irrelevant text chunks. The
throughput price is ~9× per token and ~11× per page. **Recommendation: keep
OCR text as the scan representation; do not add image embedding to the
default pipeline.** Details below.

Raw results: `C:\RecallHub_Synthetic\kanzlei_v1\bench\scan_embedding\scan_embedding_bench.json`.
Harness: `backend/evaluation/synthetic_corpus/scan_embedding_bench.py`
(tests: `backend/tests/synthetic_corpus/test_scan_embedding_bench.py`).

## Setup

- **Corpus:** synthetic Austrian law-firm corpus `kanzlei_v1` (`docs/20-SYNTHETIC_CORPUS.md`),
  profile `synthetic-kanzlei`, DB `rag_synth_kanzlei_v1`: 122 primary documents,
  of which **27 are scanned pages** (`scan_jpg`, 150 DPI, 1241×1754 grey JPEG with
  rotation/noise/blur; multi-page scans are one file and one document per page).
  655 stored text chunks, 768-d.
- **Gold:** 45 questions, 41 answerable. **18 have a scanned page as a gold
  document** — the evaluation subset where the arms differ. 3 are typed `scan_ocr`.
- **Embedder:** the live llama.cpp `rag-embedder-gpu`, `ggml-org/embeddinggemma-2-GGUF:Q8_0`,
  768-d, `vision: true`, on an RTX PRO 2000 (16 GB). Same model, dimensions and
  prefix convention in every arm — only the scan representation differs.
- **Arms** (scanned pages only; all other documents are their text chunks in every arm):
  - `ocr_text` – the stored Docling OCR chunks (current production behaviour)
  - `image`    – one direct image embedding per page, via the multimodal
    `{"input": {"prompt_string": "<media_marker>", "multimodal_data": ["<base64>"]}}`
    request (`docs/04`, `embedding_checks.embed_image`)
  - `hybrid`   – OCR chunks **and** the image vector
- **Indexes:** `mixed` = all 122 documents (production-like); `scan_only` = the
  27 scanned pages alone (isolates representation quality).
- **Retrieval + scoring:** exact in-memory cosine, top-10, scored with the
  existing `bench.score_ranking` (document-level `recall@k`/`hit@k`/`mrr`,
  passage-level `span_recall@k`) against the existing gold set.
- **Prefixes:** none. The production OpenAI-compatible embedding path sends raw
  text (EmbeddingGemma's task prefixes are only applied by the validation
  checks), so the baseline matches the live index. Verified: re-embedding 60
  stored chunks gives cosine **0.9999** (min 0.9998) against the vectors already
  in MongoDB, i.e. the `ocr_text` arm *is* the production index.

## Results

### Mixed index (production-like) — the headline

Scan-subset questions (n = 18):

| arm | vectors | recall@5 | recall@10 | hit@5 | MRR | span@10 |
|---|---|---|---|---|---|---|
| `ocr_text` (today) | 655 | **0.422** | **0.645** | **0.722** | **0.512** | 0.556 |
| `image` | 582 | 0.181 | 0.451 | 0.500 | 0.318 | 0.509 |
| `hybrid` | 682 | 0.422 | 0.645 | 0.722 | 0.511 | 0.556 |

Per question (recall@10, image vs. OCR): **gained 1, lost 9, tied 8.**
Typical losses: `inkasso_01-q05` (scan_ocr) gold page rank 1 → 10;
`bau_01-q08` 1 → 7; `mietrecht_01-q01` 3 → 7; `cross-q01` 3 → not in top-10.

The three explicit `scan_ocr` questions: OCR recall@10 0.667 / MRR 0.37 vs.
image 0.333 / 0.033. All 41 answerable questions: OCR recall@10 0.587 / MRR 0.443
vs. image 0.528 / 0.383; hybrid equals OCR.

`hybrid == ocr_text` to three decimals means the image vector **never** earned a
top-10 slot that an OCR chunk had not already earned.

### Scan-only index — representation quality in isolation

Scan-subset questions (n = 18), only the 27 scanned pages indexed:

| arm | vectors | recall@5 | hit@5 | MRR |
|---|---|---|---|---|
| `ocr_text` | 100 | 0.444 | 0.889 | 0.687 |
| `image` | 27 | **0.513** | **1.000** | **0.812** |
| `hybrid` | 127 | 0.444 | 0.889 | 0.676 |

Here the image ranks the gold page first in 13/18 questions. The clearest case
is `bau_01-q05` ("Welche ÖNORM … ergänzend vereinbart?"): OCR mangles the
page badly enough that the OCR arm **never** retrieves it in either index,
while the image embedding ranks it #1 among scans — yet it still does not reach
the mixed top-10. The image captures the content; it loses the *competition*.

### Why: the cross-modal similarity gap (measured)

Mean cosine of each scan-subset query (n = 18) to:

| target | mean cosine |
|---|---|
| its gold page's **image** | 0.745 |
| its gold page's best **OCR chunk** | 0.801 |
| the best irrelevant **clean-text** chunk in the corpus | **0.826** |
| the best **other scanned page's image** | 0.735 |

- Image beats the best irrelevant clean-text chunk: **0 / 18**.
- OCR chunk beats it: 6 / 18 (retrieval is hard here in general).
- Image beats every other image: 13 / 18 (what `scan_only` measures).
- Same page, OCR chunk minus image: **+0.056**.

Text-to-text similarity is structurally higher than text-to-image in this
model's space. In one shared cosine index that handicap is fixed and larger
than the typical margin between relevant and irrelevant, so image vectors sink
below clean-text noise regardless of how well they encode the page. A plausible
contributor: the vision encoder emits a **constant 966 tokens per page**
(min = max = 966 across all 27), i.e. it rescales a dense A4 page to a fixed
grid, which keeps layout/gist but not the fine print (amounts, Geschäftszahlen,
norm numbers) that `exact_token`/`single_fact` questions ask for.

### Fact retention (the "44 %" figure)

Measured with the existing `bench._fidelity` on `scan_jpg` (117 planned facts over 27 pages):

| ingestion | facts found in content / chunks |
|---|---|
| 2026-10-11 (bench1/2/3; the "OCR keeps 44 %" number) | **0.436** |
| 2026-10-14 re-ingestion (current index, local embedder, newer worker image) | **0.667** |

So the current OCR loses a third of the scan facts, not more than half; the
43.6 % figure is stale. Retention is a text metric and cannot be computed for
an image vector; the comparable number is retrieval recall of the scanned page,
reported above. Facts that still get lost are long German phrases and amounts
(e.g. `Kollektivvertrag für Angestellte und Lehrlinge in Handelsbetrieben`,
`EUR 3.600,00`, `Beschäftigungsgruppe E, Stufe 3`).

## Cost (live GPU embedder, measured in isolation)

| phase | items | tokens | wall | items/s | tokens/s |
|---|---|---|---|---|---|
| text chunks (all 655) | 655 | 141,350 | 8.3 s | 79.1 | **17,072** |
| page images (all 27) | 27 | 26,082 | 14.3 s | 1.89 | **1,821** |

- **Per token** the vision path is **~9.4× slower**; GPU utilisation hit 100 %
  (mean 90 %) during images vs. low during text.
- **Per page:** a scanned page is ~3.7 OCR chunks ≈ ~800 text tokens (≈ 0.05 s)
  vs. 966 image tokens (≈ 0.53 s) → **~11× more wall-clock per page**, for a
  similar token count.
- **VRAM:** no pressure. The model sits at ~4.15 GB resident either way (text
  phase 4,145–4,149 MiB, image phase 4,149–4,151 MiB); the vision path allocated
  ~395 MiB once on first image use and kept it. The price is compute time, not memory.
- For a NAS corpus of N scanned pages, budget roughly **N × 0.5 s** of GPU time
  on this card for image vectors, on top of the OCR that is still needed for the
  text, quotes and citations the agent relies on.

## Recommendation

1. **Keep OCR text as the scan representation. Do not add image embedding to
   the default pipeline.** It is worse in the only index that matters (mixed),
   hybrid gains nothing, and it costs ~11× per page. The task's bar for sketching
   an ingestion option ("only if clearly positive") is not met.
2. The result is **situational, not negative in principle**: the image vector
   encodes the page well (scan-only MRR 0.81 vs 0.69). Making that usable would
   need a **separate scan-image retrieval lane** — image vectors in their own
   index, searched per modality, merged by rank fusion (RRF) rather than by raw
   cosine in a shared index — plus the agent still needs OCR text for quotes and
   citations. That is a retrieval design project, not a toggle; revisit only if
   OCR quality on the real NAS scans turns out materially worse than here.
3. The cheaper lever is **OCR quality**: retention already moved 43.6 % → 66.7 %
   between ingestions, and the remaining losses are specific (long phrases,
   amounts, umlauts/norm codes). `_fidelity` needs the synthetic corpus's
   planned-fact ledger, so it cannot be run on real scans; the equivalent
   pre-ingestion check for the NAS is to OCR a sample of its scanned pages with
   the same Docling path and spot-check the output for exactly those loss
   classes before committing the whole share.

## Caveats

- Small subset (18 scan questions, 3 `scan_ocr`); directions are consistent
  across every metric and both indexes, but single-question swings are large.
- Synthetic scans at 150 DPI with mild degradation; real NAS scans may be worse
  (favours image) or higher-resolution (no help — the encoder rescales to 966 tokens).
- Exact cosine, not MongoDB `$vectorSearch` ANN; no prefixes (matches production);
  the live search API was not re-queried because the Quellex backend enforces
  login and the bench client has no credential option — the stored-vector check
  above stands in for it.
- 2026-10-15 only; no RRF/hybrid-with-text-index variant was tried.

## Reproduce

```powershell
# from the repo root with the project venv (uv in a worktree may pick an unsupported Python)
python -m backend.evaluation.synthetic_corpus.scan_embedding_bench `
  --run-dir C:\RecallHub_Synthetic\kanzlei_v1 --embedder-url http://127.0.0.1:11090
```

Needs the stack's MongoDB (`rag_synth_kanzlei_v1`) and the GPU embedder. If
`rag-embedder-gpu` is down with `nvidia-container-cli: … no adapters were found`,
GPU passthrough to the Docker VM has dropped: `wsl --shutdown` followed by
`docker desktop restart` reboots the VM and restores it (`docker desktop restart`
alone restarts the engine inside the same VM and does **not** fix it).
