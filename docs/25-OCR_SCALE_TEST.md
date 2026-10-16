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

Tesseract `deu+eng` + `§` repair, Docling 2.137 defaults (OSD rotation on
every rectangle, body content layer only, `scale=3.0`), run `base`:

| set | pages | words | letter-soup runs (pages) | glued tokens | mean OCR confidence | pages < 0.75 | flagged (proxy) | s/page median (mean) |
|---|---|---|---|---|---|---|---|---|
| `scans` | 1,045 | 104,252 | 260 (164) | 299 | 0.854 | 88 | 202 (19.3 %) | 3.75 (4.26) |
| `refscans` | 151 | 15,826 | 52 (31) | 43 | 0.844 | 14 | 38 (25.2 %) | 5.5 (6.84) |
| `rendered` | 100 | 9,149 | 38 (20) | 23 | 0.861 | 14 | 25 (25.0 %) | 5.25 (7.82) |
| `photos` (first 150 of the 1,000) | 166 | 3,658 | 31 (19) | 12 | 0.532 | 20 | 31 (18.7 %) | 10.1 (13.6), max 58 |

| reference set | mean word recall | sentences lost | values lost | material-loss pages |
|---|---|---|---|---|
| `refscans` (scanner text layer) | 0.762 | 169 / 1,199 (14.1 %) | 89 / 477 (18.7 %) | **73 / 151 (48.3 %)** |
| `rendered` (exact text, clean 300-dpi renders) | 0.867 | 28 / 613 (4.6 %) | 199 / 570 (34.9 %) | **57 / 100 (57.0 %)** |

The lost values of both reference sets: 140 dates, 33 Geschäftszahlen, 31
amounts, 15 IBANs, 11 § references. 57 of the 130 loss pages had an OSD
rotation applied. On the clean renders 28 of the 57 loss pages lost only
header/footer values (the furniture class), 27 lost body text - almost all
of it to orientation flips (a Courier Firmenbuch extract read upside down
end to end; the second line of a Ladung, `Diese Ladung und ein
Lichtbildausweis sind mitzubringen!`, as
`jusßBungnzyw puys sIaMSnePIIAySIT Ula pun Bunpe7] aseiq`).

Manual audit of 16 random `scans` pages (page image vs OCR text): material
loss on 6 pages = 5 orientation flips (a whole literature page, a mail
header + signature block, a police-report block, a Porsche invoice, a VW
invoice with mangled dates) and 1 page whose vital-signs/diagnosis blocks
were never OCRed; the other 10 pages were complete. Stratified over the
flagged/unflagged split that is an estimated **~12 % of scan pages with
material loss** (6 of 10 flagged × 19.3 % + 0 of 6 unflagged × 80.7 %),
against the 7 % of the 42-page sample in `docs/24` - the larger sample
simply contains more Courier court extracts, tables and sparse pages, which
is where OSD fails.

## Experiments

Each variant ran on the two reference sets (151 + 100 pages) with page
furniture off, so the numbers isolate the OCR change; `guard` is the
orientation guard at threshold 3 with verify-by-retry, everything else is
stacked on it. Seconds per page are inflated and noisy because up to eleven
containers shared the 23 cores; use them only relative to each other.

| variant | refscans: recall / sentences lost / values lost / loss pages | rendered: recall / sentences lost / values lost / loss pages | kept? |
|---|---|---|---|
| baseline (production of the morning) | 0.762 / 169 / 89 / 73 | 0.867 / 28 / 199 / 57 | – |
| `guard` (OSD confidence ≥ 3, retry below) | 0.795 / **109** / **60** / 68 | 0.897 / 22 / 180 / 55 | yes |
| `guard10` (threshold 10) | identical to `guard` | identical to `guard` | **yes**: the retry recovers the genuine rotations between 3 and 10, and an upright Courier page was flipped at 3.99 |
| `guard_dpi` (+ PDF raster 300 dpi) | 0.782 / 128 / 76 / 70 | 0.885 / 26 / 198 / 56 | no: worse than `guard` alone and +12 % time |
| `psm6` (Tesseract `--psm 6`) | 0.800 / 109 / 62 / 65 | 0.903 / 22 / 182 / 54 | no: +0.005 recall, but letter-soup runs 36 → 45 (refscans) and 26 → 46 (rendered) |
| `psm11` (sparse text) | 0.790 / 115 / 82 / 70 | 0.881 / 24 / 191 / 58 | no |
| `best` (tessdata_best float models) | 0.786 / 137 / 63 / 67 | 0.886 / 22 / 176 / 52 | no: lower recall on both sets, more letter soup, +42 % time |
| `guard10_px` (OCR bitmap long side 3,500 px for images, 105 scan images) | flagged pages 19 → 24, low-score pages 8 → 13, 4 fixed / 9 regressed, same speed | – | no: replaced by the megapixel budget, which leaves A4 scan images at Docling's scale |

On the 1,045 `scans` pages (no reference) the guard alone moved the proxy
metrics as follows: flagged pages 202 → 170, letter-soup runs 260 → 225,
glued tokens 299 → 204, mean OCR confidence 0.854 → 0.871, 39 pages fixed
vs 1 regressed by verdict (the one regression, a rotated flyer, exposed that
the retry rotated 90°/270° rectangles the wrong way - fixed before the final
run). Guard statistics of that run: 371 rotations proposed, 197 below the
threshold and verified by retry, 21 of them genuine and recovered, 176
rejected; 4 empty-page fallbacks.

The reference-based "material-loss page" rate stays high on `refscans`
after every variant because the scanner's text layer also contains what
Tesseract cannot read by design: rotated letterhead side blocks, file-path
footers, table header lines reflowed into Markdown cells, and strike-through
text. The rate is a strict upper bound; the sentence and value counts and
the manual audit are the numbers to compare.

## Before / after

Run `final` = the branch defaults: orientation guard at confidence 10 with
verify-by-retry and the empty-page fallback, page furniture in content and
chunks, image noise gate, 20 Mpx image OCR budget; everything else as
before (Tesseract `deu+eng` fast models, 216 dpi, psm 3, `§` repair).

| reference set | | base | final |
|---|---|---|---|
| `rendered` (100 pages, exact text) | mean word recall | 0.867 | **0.946** |
| | sentences lost | 28 / 613 | **8 / 613 (1.3 %)** |
| | values lost | 199 / 570 | **135 / 570** |
| | material-loss pages | 57 (57 %) | **28 (28 %)** |
| `refscans` (151 pages, scanner text layer) | mean word recall | 0.762 | **0.810** |
| | sentences lost | 169 / 1,199 | **101 / 1,199 (8.4 %)** |
| | values lost | 89 / 477 | **47 / 477 (9.9 %)** |
| | material-loss pages | 73 (48 %) | **55 (36 %)** |

What is left on the clean renders is mostly the reference itself: the
rendered pages carry the Geschäftszahl and dates in a header that Docling
reads as one furniture line per page, and 23 of the 28 remaining loss pages
are such header/footer values the OCR has in a slightly different spelling
(`5 FAM 20/209` for `5 FAM 20/20g` at 216 dpi, a space inside an IBAN) or
table header lines reflowed into Markdown cells; the per-page sentence loss
is 1.3 %. On the scanner-text-layer pages the residual is dominated by what
the reference contains that Tesseract cannot read: rotated letterhead side
blocks (`MICRO Beteiligungs GmbH … UID …` printed vertically), file-path
footers, strike-through lines, and handwriting.

`scans` (1,045 pages, no reference): the proxy flags are not comparable
between `base` and `final` because the final run carries the header/footer
lines too, and ERV code footers (`001 037 C**** 000520 2015v BK* 001 001
00001 B7Z7 ZB`), fax headers and stamps trip the letter-soup heuristic. The
like-for-like run is `final_body` (the final OCR configuration with page
furniture off):

| `scans`, 1,045 pages | base | final_body |
|---|---|---|
| flagged pages (proxy) | 202 (19.3 %) | **164 (15.7 %)** |
| letter-soup runs (pages) | 260 (164) | **210 (136)** |
| glued tokens | 299 | **192** |
| pages with OCR confidence < 0.75 | 88 | **56** |
| mean OCR confidence | 0.854 | 0.873 |
| pages fixed / regressed by verdict | – | **38 / 0** |
| s/page median (mean), under shared CPU | 3.75 (4.26) | 4.06 (4.64) |

A paired diff of 30 random pages between `base` and `final`
found 12 identical pages and 18 pages that only *gained* text (IBAN/UID/FN
footers, Geschäftszahl headers, page numbers, a signature line) or had a
flipped block restored (`:Bunueunmes` → `Sachbearbeiter`); no page lost a
line.

Manual audit of 24 random `final` scan pages (12 flagged, 12 unflagged):
one page with a garbled stamp/address line (`t piplatz 28` for
`Hauptplatz 28`, Geschäftszahl and body intact), one page of handwritten
notes (soup, nothing printed to lose), one fax header with a wrong year in
the fax timestamp, 21 pages complete; the flagged pages were dotted fill
lines, asterisk rules of a Grundbuch extract, ERV code lines and a
handwriting page. Stratified estimate: **~2 % of scan pages with material
loss** (1 of 11 printed flagged pages × 22 %, 0 of 12 unflagged × 78 %),
down from ~12 % on the same sample population with the morning's path, and
below the 7 % the 42-page sample had suggested. Small samples: the honest
statement is "under 5 %", with the reference sets as the exact companions.

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
