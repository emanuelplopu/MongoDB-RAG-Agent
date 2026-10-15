# OCR quality check of the production scan path (2026-10-15)

**Question.** Before the law firm's NAS corpus (profile `diejuristin`) is
ingested: how well does the production OCR path handle *its* scans, and does
ingestion need a pre-processing step (deskew, higher DPI, another OCR engine)?

**Status.** The harness exists and is validated, but the firm's scans could
not be measured yet: the NAS share is not reachable from the server (see
"Blocker"). What *is* measured is the production OCR path on the synthetic
Austrian law-firm scans with ground truth, plus two what-if variants. The
real-sample section below is a template to fill once a sample folder exists.

**Verdict so far (synthetic scans, ground truth known).** The production
engine (Docling 2.137 → auto → RapidOCR/onnxruntime, CPU) keeps ~84 % of the
words of a clean 150-DPI scan, but loses whole lines as letter soup on
*perfectly legible* text (about one run of 1-2 lines per page) and glues
tokens together (`1110Wien`, `Stufe3`, `EUR400`). Doubling the resolution
does **not** fix either (word recall 0.836 → 0.861, same number of garbled
runs, 1.8× slower), so DPI / deskew pre-processing is not the lever; the
recognition stage of the engine is. **Swapping the engine for Tesseract
(`deu+eng`) on the same pages gives 0.99 word recall, zero letter-soup
runs, zero glued tokens and all 49 amounts / dates / Geschäftszahlen /
§-references intact, at the same speed.** See "Variants" and
"Recommendation".

Harness: `scripts/ocr_scan_check.py` (no pipeline change). Raw outputs of
the synthetic dry run: session scratchpad `dryrun/out*` (not kept in git).

## Blocker: no access to the firm's scans from this server

- `C:\RecallHub-Verify\mounts\diejuristin` (the folder the live profile
  points at as `/app/mounts/diejuristin`) is empty on the host.
- No SMB session or drive mapping exists on the server; `net view
  \\192.168.43.250` (ORCA, SMB 445 open) answers *Access is denied*
  anonymously; the server/share behind the office `J:` drive
  (`J:\ADVOKAT Akten`, `J:\jurXpert\documents`, `J:\LITERATUR`, `J:\Texte`)
  and a read-only account are still unknown (`docs/21-LAN_ACCESS.md`,
  `docs/22-DOCUMENT_FOLDERS_AND_SHARES.md`).
- Claude does not enter share credentials. To unblock, an administrator
  either maps the share once (`net use J: \\ORCA\<share> /user:<ro-user>`
  in a terminal, or `installer\scripts\Add-NetworkShare.ps1` so it appears
  under `C:\RecallHub-Verify\mounts\<name>`) or copies 10-20 scanned files
  (contracts, court letters, cost notes, pages with handwritten notes;
  PDF/JPG/TIF) into `C:\RecallHub-Verify\bench\ocr-scan-sample-<date>\in`.

## How to run the check (read-only, no Mongo, no ingestion job)

The harness builds the *same* converter the ingestion worker builds —
`src.ingestion.ingest.create_document_converter()` for PDFs and the
`ImageFormatOption` branch of `process_file` for JPG/PNG/TIF — inside the
`recallhub-ingestion-worker` image, so Docling version (2.137.0), OCR
engine (auto → RapidOCR with onnxruntime) and device (CPU: the live
worker has no GPU reservation) are exactly production's.

```powershell
# sample folder: <sample>\in holds the scans (optionally <stem>.gold.txt with the expected text)
$S = "C:\RecallHub-Verify\bench\ocr-scan-sample-2026-10-15"
docker run --rm -v "${S}\in:/data/in:ro" -v "${S}\out:/data/out" -v "${S}\cache:/root/.cache" `
  -v "C:\dev\repos\MongoDB-RAG-Agent\scripts\ocr_scan_check.py:/app/ocr_scan_check.py:ro" `
  --entrypoint python recallhub-ingestion-worker:latest /app/ocr_scan_check.py /data/in /data/out
```

(In Git Bash set `MSYS_NO_PATHCONV=1`, otherwise `/app/...` is rewritten to a
Windows path.) Per input file the output folder gets `page_NN.png` (the
scan, for eyeballing), `page_NN.txt` (OCR text of that page, items grouped
by Docling provenance), `document.md` (what production would store as
`content`), `meta.json`; plus `summary.md` / `summary.json`. `meta.json`
records per page: embedded-image DPI (PDF image metadata, or pixels over
placed width; for images the DPI tag or pixels over A4 width), PDF
text-layer characters (a scanner-side OCR layer makes Docling skip OCR for
that area), conversion seconds, counts of amounts / dates /
Geschäftszahlen / norm codes / § references / umlauts, **garbled runs**
(≥ 4 alphabetic tokens of which half have no vowel or ≤ 2 letters — the
letter-soup signature), **merged tokens** (`HandelGmbH`, `Stufe3`,
`Datum:1`), and — with a gold text — word recall, line recall, the missing
words/lines and which amounts/dates/GZ were lost.

What-if knobs (experiments only; unset = production): `--upscale 2`
(resample image inputs), `--ocr-scale`, `--force-full-page-ocr`,
`--lang deu,eng`, `--engine tesseract|easyocr|rapidocr`. The engine must be
installed in the image; see "Variants" for the Tesseract build.

## Side findings about the production path (independent of the NAS)

1. **No model cache in the live worker.** `rag-ingestion-worker` has no
   Docling / HuggingFace / RapidOCR model files (`/root/.cache` holds only
   `ms-playwright`; no `HF_HOME`, no `DOCLING_ARTIFACTS_PATH`, no cache
   volume), and the image ships none either. The first scanned page after
   *every* worker recreate downloads ~500 MB (layout model
   `docling-project/docling-layout-heron`, TableFormer, RapidOCR ONNX
   models) from huggingface.co — in this run 3.9 min at ~2 MB/s before
   the first page converted — and fails outright without internet. Follow-up
   (not done here): a named volume on `/root/.cache` for the worker and
   the backends, or bake the models into `Dockerfile.base`
   (`docling-tools models download`), and mention it in
   `docs/14-OFFLINE_DEPLOYMENT.md`.
2. **CPU only.** Only `embedder-gpu` has a GPU reservation in the live
   Compose; `get_effective_device()` resolves to CPU in the worker. Steady
   state is ~5-7 s per scanned A4 page (layout + OCR), which bounds the
   NAS ingestion: ~500-700 scanned pages per hour per worker.
3. **Engine and language are Docling defaults.** `PdfPipelineOptions()` →
   `OcrAutoOptions(scale=3.0, lang=[], force_full_page_ocr=False)`; the
   auto model picked `rapidocr with onnxruntime` (EasyOCR, Tesseract,
   Nemotron are not installed). No German language hint is passed.
4. `export_to_markdown()` (what production stores) also drops nothing the
   OCR produced here — the losses below are all in the OCR output itself.

## Dry run on the synthetic corpus (ground truth available)

Sample: 8 first pages, one per document type, from
`C:\RecallHub_Synthetic\kanzlei_v1` (`scan_jpg`: 150 DPI, 1241×1754 grey
JPEG q72, ±rotation, noise 0.035, blur 0.5 — i.e. a *mediocre* office scan):
Dienstvertrag, Werkvertrag/Angebot, Übernahmeprotokoll, DSB-Aufforderung,
bedingter Zahlungsbefehl, Drittschuldnererklärung, Hauptmietvertrag, Ladung.
Gold = the text the generator rendered (manifest `text`).

| # | page | words | garbled runs | merged tokens | gold word recall | gold line recall | lost special values |
|---|---|---|---|---|---|---|---|
| 1 | Dienstvertrag (Nachtrag) | 164 | 1 | 4 | 0.790 | 0.548 | — |
| 2 | Angebot / Werkvertrag | 188 | 0 | 4 | 0.824 | 0.595 | — |
| 3 | Übernahmeprotokoll | 222 | 1 | 7 | 0.931 | 0.535 | — |
| 4 | DSB Aufforderung | 189 | 1 | 9 | 0.817 | 0.465 | date 08.01.2025 |
| 5 | Bedingter Zahlungsbefehl | 196 | 2 | 4 | 0.840 | 0.500 | EUR 3.500,00; § 244 |
| 6 | Drittschuldnererklärung | 169 | 1 | 6 | 0.833 | 0.659 | EUR 2.300,00; EUR 400,00; date 28.03.2025 |
| 7 | Hauptmietvertrag | 152 | 0 | 13 | 0.844 | 0.561 | EUR 880,00; EUR 1.100,00 |
| 8 | Ladung | 158 | 3 | 4 | 0.808 | 0.579 | — |
| | **mean / total** | | **9** | **51** | **0.836** | **0.555** | 8 of 31 |

5-9 s per page on CPU (first page +3.9 min model download). "Line recall"
is strict (a gold line must appear verbatim, so a glued token already
fails it); "word recall" is the better single number. All umlauts, all
Geschäftszahlen of the `21 C 752/25d` form and all but two dates survived.

### The two loss classes, with examples

1. **Whole lines become letter soup** on text that is perfectly legible in
   the scan (checked by eye on the PNGs). The affected lines are body-text
   lines in the middle of paragraphs; typically one run of 1-2 lines per
   page. The gold line
   `Dienstverhältnis. Es wird auf unbestimmte Zeit fortgesetzt. Dieser Nachtrag fasst die bisherigen`
   came back as `Dienai itiiagteit t it t gig`; `Verwendung: Buchhalterin
   mit Debitorenbuchhaltung und Betreuung der Bürobarkassa` as `Vurusgusg
   usg ug g ssguusa`; on the Ladung `vertreten durch Mag. Dr. Hugo Bader,
   Rechtsanwalt, …` as `Vert  t r en  t  ien`. This is the "long German
   phrases / whole lines dropped" class of the earlier fact-retention
   measurement (0.667 on the 2026-10-14 ingestion), and it is where the
   lost amounts (`EUR 2.300,00`, `EUR 880,00`) and `§ 244` went: they sat
   on a garbled line.
2. **Spaces vanish between tokens**: `Windisch HandelGmbH`, `1110Wien`,
   `BestehendesDienstverhältnis`, `Eintrittsdatum:01.05.2019`,
   `FN348307c`, `Stufe3`, `Geschäftszahl 44 Cg200/25b`, `EUR2`. 4-13 per
   page. Harmless for a human reader, but it breaks exact-value matching
   and BM25 term matching (`Stufe 3`, `FN 348307c`, `44 Cg 200/25b` are
   different tokens), which is why hybrid search on scans under-performs.

Not observed on this sample: umlaut damage, digit substitutions inside
amounts or dates that *were* recognised, table loss (no tables in the
sample), rotation problems (±1° handled).

## Variants (what would a pre-processing step buy?)

| variant | mean word recall | garbled runs | merged tokens | lost special values | s/page |
|---|---|---|---|---|---|
| production (RapidOCR, scale 3) | 0.836 | 9 | 51 | 8 | 5-9 |
| `--upscale 2` (≈ 300 DPI input) | 0.861 | 10 | 48 | 8 | 6-13 |
| `--engine tesseract --lang deu,eng` | **0.992** | **0** | **0** | **0** (49/49 kept) | 5-8 |

Doubling the input resolution changes nothing material: the same lines are
garbled (different soup), the same values are lost, and conversion is
1.8× slower. Deskew is moot (the harness pages are within ±1°, Docling's
layout model is rotation tolerant at that level).

### Tesseract comparison

Throwaway image `ocr-check-tesseract:tmp` = the worker image plus
`apt-get install tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng`
(Tesseract 5, Debian trixie), run with Docling's `TesseractCliOcrOptions`
(`lang=["deu","eng"]`, otherwise Docling defaults: same layout model, same
`scale=3.0`, OCR only on bitmap areas).

| # | page | words | garbled | merged | gold word recall | gold line recall | lost special values |
|---|---|---|---|---|---|---|---|
| 1 | Dienstvertrag (Nachtrag) | 219 | 0 | 0 | 0.991 | 0.857 | — |
| 2 | Angebot / Werkvertrag | 216 | 0 | 0 | 0.991 | 0.905 | — |
| 3 | Übernahmeprotokoll | 246 | 0 | 0 | 0.992 | 0.767 | — |
| 4 | DSB Aufforderung | 224 | 0 | 0 | 1.000 | 0.977 | — |
| 5 | Bedingter Zahlungsbefehl | 262 | 0 | 0 | 0.996 | 0.833 | — |
| 6 | Drittschuldnererklärung | 207 | 0 | 0 | 0.986 | 0.909 | — |
| 7 | Hauptmietvertrag | 173 | 0 | 0 | 1.000 | 0.902 | — |
| 8 | Ladung | 215 | 0 | 0 | 0.977 | 0.763 | — |
| | **mean / total** | | **0** | **0** | **0.992** | **0.864** | **0 of 49** |

Residual errors are of a different, benign kind: single-character
substitutions in proper names (`Ferstigasse` for `Ferstlgasse`, `4.` for
`1.` in a heading), and letterhead key/value blocks split into separate
items (`Dienstnehmerin:` and the name end up on different lines), which is
what keeps strict line recall below 1. Docling logged two harmless
`OSD failed … Too few characters` errors for tiny OCR rectangles (the page
text was complete). 4.7-7.8 s per page — Tesseract is not slower than
RapidOCR here because the layout model dominates.

## The firm's scans (to fill once a sample exists)

Template for the note once `C:\RecallHub-Verify\bench\ocr-scan-sample-<date>\in`
holds 10-20 pages (aim: contracts, court letters, cost notes, handwritten
annotations; PDF and image scans; note the scanner model if known):

- Sample list (file, type, pages, DPI, text layer yes/no)
- Per-page verdict: clean / partial / bad (bad = a garbled run or a lost
  amount/date/GZ in the page's key facts; partial = merged tokens or a
  dropped header/footer only)
- Failure examples (anonymised: no client names in this doc)
- Share of pages with material loss
- DPI distribution and whether PDFs carry a scanner text layer (if most
  do, Docling uses that layer and the OCR engine matters less)
- Recommendation

## Recommendation (provisional, from the synthetic evidence)

- **Ingest as-is is acceptable for finding documents** (0.84 word recall,
  dates and Geschäftszahlen intact, ~1 garbled run per page), but **not
  for exact-value questions** on scans (amounts in ~1 of 3 scanned pages
  land on a garbled line or are glued to the next token).
- **Do not spend effort on DPI / deskew pre-processing**: measured to be
  worthless for this failure mode.
- **The lever is the recognition engine.** Tesseract `deu+eng` removes
  both loss classes on this sample (0.84 → 0.99 word recall, 8 → 0 lost
  values) at no speed cost. The production change is small and
  self-contained: `pipeline_options.ocr_options =
  TesseractCliOcrOptions(lang=["deu","eng"])` in
  `create_document_converter()` and in the image branch of `process_file`
  (`src/ingestion/ingest.py`), `tesseract-ocr tesseract-ocr-deu
  tesseract-ocr-eng` in `backend/Dockerfile.base` (≈ 30 MB), and a
  setting to pick the engine/languages (`backend/core/config.py`,
  `src/settings.py`, `.env.example`). Before switching the live worker,
  re-run `quellexctl synth bench-ingest` on the synthetic corpus (the
  `_fidelity` scan metric should move from 0.667 towards ~0.95) and run
  this harness on the firm's real sample once it exists — Tesseract is
  known to be weaker than neural engines on *poor* scans (low contrast,
  faxes, handwriting), which the synthetic sample does not contain.
- Until the engine change ships, ingesting the NAS as-is is defensible for
  retrieval, but answers that quote amounts from scanned pages must be
  treated as unreliable, and the documents will need re-ingestion after
  the switch (the stored `content` is the OCR text).
- Independently of the engine: give the worker a model-cache volume (side
  finding 1) before the NAS ingestion, or the first scan after each
  restart costs minutes and needs internet.
