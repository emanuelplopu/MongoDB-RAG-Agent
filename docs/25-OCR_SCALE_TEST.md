# OCR scale test: 1,000 scanned pages + 1,000 phone photos (2026-10-16)

**Question.** After the engine switch and the `§` repair of `docs/24` (Tesseract
`deu+eng`, ~7 % of pages with material loss on 42 hand-checked pages), how does
the production OCR path hold up on a *large* sample of the firm's NAS, which
failure modes remain, and what pushes the material-loss rate down?

**Verdict (short).** On 1,000 scanned pages the production path of the
morning (Tesseract + § repair, Docling 2.137) loses a sentence or a value on
far more pages than the 42-page sample suggested, and the losses are not OCR
recognition errors. The four classes, in order of damage:

1. **Orientation flips.** Docling runs Tesseract's orientation detection on
   every OCR rectangle and rotates by whatever comes back, ignoring the
   reported confidence. Courier court extracts, centred bold lines, tables and
   sparse pages come back as 180°-soup (`*qaynze6yoanp` for `durchgeführt.`,
   a whole payment table, a whole Patientenbrief page). Every rotation proposed
   with confidence < 1 on upright pages was wrong; the band up to ~5 is mixed
   (a confidence-3.99 flip of an upright Courier page); genuine rotations score
   ≥ 10.
2. **Page headers and footers were never indexed.** Docling labels the
   Geschäftszahl header of court documents, `Hinterlegt am …` ERV stamps and
   the IBAN/UID footers of letters as *furniture*, and both
   `export_to_markdown()` and the HybridChunker skip that content layer by
   default. This affects every format, not only scans: the stored `content`
   and the chunks of a born-digital Ladung never contained its Geschäftszahl.
3. **Faint blocks dropped by Tesseract's page segmentation** (`--psm 3`): on
   some renders whole blocks (vital signs, diagnosis, a findings table) are
   classified as non-text and never OCRed, although Tesseract reads them with
   `--psm 6`.
4. **Photos produce noise.** A phone photo of a car or a building site yields
   10-280 noise tokens (`> Pe GR a yt ik A Deu rer`) that would be indexed;
   photographed letters, forms and screenshots are readable (word-like token
   ratio > 0.6, OCR confidence > 0.6).

The branch `feature/ocr-scale-test` adds (all configurable, defaults chosen
by the measurements below): a Tesseract model with an orientation-confidence
guard and verify-by-retry (`OCR_OSD_MIN_CONFIDENCE`), a full-page fallback for
pages where the layout model finds no text, page furniture in content and
chunks (`INGEST_PAGE_FURNITURE`), an image noise gate
(`OCR_IMAGE_NOISE_GATE`), a per-file OCR resolution (`OCR_SCAN_DPI`,
`OCR_IMAGE_TARGET_PX`), `OCR_PSM`, `OCR_TESSDATA_PATH`, and a converter cache
(Docling re-initialised its pipeline for every file). Results in
"Before / after".

## The sample

Drawn read-only from the CIFS volume `recallhub_share_daten`
(`scripts/ocr_sample_nas.py`, seed 20261016, 4,275 of 10,964 case folders
walked in 292 s; everything stays in
`C:\RecallHub-Verify\bench\ocr-scale-2026-10-16\sample\` with
`manifest.jsonl` / `manifest.csv`, client data, not in git):

| set | files | pages | what | reference text |
|---|---|---|---|---|
| `scans` | 351 PDF + 105 images | 1,000 | scanned PDFs without text layer (page subsets of ≤ 4 pages re-saved with pypdfium2) and scanner JPG/TIF pages; 173 files at 300 dpi, 150 at 200 dpi, 20 at ≤ 150 dpi | none (proxy metrics + manual audit) |
| `refscans` | 71 PDF | 151 | scanned PDFs that carried a scanner OCR text layer, re-saved as image-only PDFs so the pipeline must OCR them | the scanner's text layer (good, not perfect) |
| `rendered` | 60 PDF | 100 | born-digital pages (webERV court mail, Firmenbuch extracts, letters) rendered at 300 dpi into image-only PDFs | exact |
| `photos` | 1,000 JPG | 1,000 | phone/camera photos (EXIF make: Apple 251, Samsung 151, Canon 100, Sony 87, Huawei 68, …; median long side 2,816 px, max 9,248) | none |
| `small` | 60 | 60 | images under 1,000 px (mail logos, signatures) | none |

Areas: `ADVOKAT Akten` 579 scan pages, `jurXpert/documents` 407, `LITERATUR`
8, `Texte` 6 (the last two hold almost no scans).

## How it was measured

`scripts/ocr_scan_check.py` (the production converter, now with `--shard K/N`,
per-page Docling OCR confidence, OSD events, the production content layers and
noise-gate verdict) runs in throwaway containers of the side-tagged worker
image with the branch's `src/` mounted (`scripts/ocr_scale_run.sh`; plain
`docker run`, never Compose, the live stack was not touched). The OCR
configuration of a run comes from `OCR_*` / `INGEST_*` environment variables,
i.e. exactly the production settings. `scripts/ocr_scale_eval.py` turns the
outputs into:

- **material loss (reference sets)** - a page loses material when a reference
  line of ≥ 5 words has < 60 % of its words in the OCR text (a dropped
  sentence) or a reference *value* (amount, date, Geschäftszahl, § reference,
  IBAN) is absent after normalisation (a dropped value);
- **proxy flags (all sets)** - a letter-soup run (≥ 4 tokens of which ≥ 3 and
  half are vowel-less or 1-2 letters, short German words excepted) or a
  Docling OCR confidence < 0.75 on a page with text;
- throughput per page and kind, OSD rotations / failures, glued tokens.

The proxy was calibrated by hand on 16 random `scans` pages (page image vs OCR
text): 6 of 10 flagged pages had real material loss (5 orientation flips, 1
dropped block), 4 were false alarms of the soup heuristic, 0 of 6 unflagged
pages lost anything. Flag precision ≈ 0.6, recall high.

## Baseline (production path of 2026-10-16 morning)

TBD

## Experiments

TBD

## Before / after

TBD

## Photo throughput recommendation

TBD

## What changed in the code

All new behaviour is a setting, declared identically in `src/settings.py`
and `backend/core/config.py`, passed through by `docker-compose.yml` for the
worker, both backends and the `cli` service, documented in `.env.example`.
The code defaults are the values validated above, so a Compose file without
the entries (the hand-maintained live one) gets them too.

- `src/ingestion/ocr_tesseract.py` (new): `RecallHubTesseractCliOcrModel`,
  Docling's Tesseract CLI model with (1) an **orientation guard**: a rotation
  Tesseract's OSD proposes for an OCR rectangle is applied outright only when
  its confidence reaches `OCR_OSD_MIN_CONFIDENCE`; (2) **verify-by-retry**: a
  suppressed rotation is tested by OCRing the rectangle both ways and keeping
  the reading with the higher character-weighted confidence (boxes of a
  rotated reading are mapped back into the unrotated rectangle); (3) an
  **empty-page fallback**: a page without text layer on which the layout
  model found no text region is OCRed as a whole. Registered with Docling's
  OCR factory under the options kind `tesseract_recallhub`; selected by
  `build_ocr_options()` whenever the guard is on, the stock model otherwise.
- `src/ingestion/ingest.py`: `OcrConfig` gained `scan_dpi`
  (`OCR_SCAN_DPI`, raster resolution of PDF pages for OCR; Docling's own
  216), `image_target_px` (`OCR_IMAGE_TARGET_PX`, long-side pixel size of the
  OCR bitmap for image files, chosen per image from its size and DPI tag;
  `0` = Docling's 3× behaviour), `psm` (`OCR_PSM`), `tessdata_path`
  (`OCR_TESSDATA_PATH`) and `osd_min_confidence`; `resolve_ocr_config()`
  reads them with the same fall-back rules as the engine. Converters are
  **cached per thread, configuration and OCR scale**: Docling keeps its
  initialised pipeline (layout model, OCR engine probe) per
  `DocumentConverter` instance and `process_file` built a new one for every
  file. `export_document_markdown()` / `document_content_layers()` include
  Docling's **furniture layer** (page headers and footers) when
  `INGEST_PAGE_FURNITURE` is on; `image_text_is_noise()` /
  `conversion_ocr_score()` implement the **image noise gate**
  (`OCR_IMAGE_NOISE_GATE`): an image whose OCR output is letter soup is stored
  with a `[Image: … - no readable text]` placeholder, like an image without
  text today.
- `src/ingestion/chunker.py`: the HybridChunker gets a serializer provider
  whose `MarkdownParams.layers` include the furniture layer, because the
  chunker only visits the content layers its serializer declares (body only
  by default).
- Harness: `scripts/ocr_sample_nas.py` (sampler), `scripts/ocr_scale_run.sh`
  (parallel throwaway containers), `scripts/ocr_scale_eval.py` (metrics and
  before/after comparison); `scripts/ocr_scan_check.py` gained `--shard`,
  `--no-render`, `.ref.txt` references, per-page OCR confidence, OSD /
  retry / fallback counters, the production content layers and the noise-gate
  verdict, and a `pypdfium2 >= 5` fix (`get_pos` → `get_bounds`).
- Tests: `backend/tests/test_ocr_scale_config.py` (scale rules, settings
  agreement, option mapping, OSD guard, rotated-box mapping, converter cache,
  furniture export and chunker provider, noise gate). The pure tests run on
  the host; the Docling-dependent ones need the production image's Docling
  (2.137; the host venv has 2.64 and skips them) - run them inside the image
  with pytest mounted (see "How it was measured").
