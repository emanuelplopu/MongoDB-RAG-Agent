"""Evaluate and compare OCR runs produced by ``scripts/ocr_scan_check.py`` on the scale sample.

Reads every ``meta.json`` (and the per-page ``page_NN.txt``) under ``<runs>/<label>/<set>/``,
joins it with the sample manifest and computes, per page:

* **reference sets** (``refscans``: scanner text layer, ``rendered``: exact text) - word
  recall, *sentence loss* (a reference line of >= 5 words of which fewer than 60 % are found
  in the OCR text) and *value loss* (an amount, date, Geschäftszahl, § reference, IBAN or
  phone number of the reference missing from the OCR text); a page with either is a
  **material-loss page**;
* **all sets** - the letter-soup heuristic (garbled runs), glued tokens, Docling's per-page
  OCR confidence, words, seconds; a page is **flagged** (material-loss proxy) when it has a
  garbled run or an OCR confidence below 0.75 while carrying text.

``eval`` writes ``<runs>/<label>/eval.json`` + ``eval.md``; ``compare`` writes
``<runs>/compare_<a>_vs_<b>.md`` with the page-level deltas, the pages that changed verdict
and the throughput per kind. Pure Python, no Docling needed::

    python scripts/ocr_scale_eval.py eval <bench>/runs base --manifest <bench>/sample/manifest.jsonl
    python scripts/ocr_scale_eval.py compare <bench>/runs base dpi300
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

AMOUNT_RE = re.compile(r"(?:EUR|€)\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d{1,3}(?:\.\d{3})*,\d{2}\s?(?:EUR|€|--)")
DATE_RE = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.\s?(?:19|20)\d{2}\b")
GZ_RE = re.compile(r"\b\d{1,3}\s?[A-Za-z]{1,3}\s?\d{1,5}\s?/\s?\d{2}[a-z]?\b")
PARA_RE = re.compile(r"§§?\s?\d+[a-z]?(?:\s?Abs\.?\s?\d+)?")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b")
PHONE_RE = re.compile(r"(?:\+43|0)\s?\d{1,4}[\s/-]?\d{3,}(?:[\s-]?\d{2,})?")
WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß]{4,}")
TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß]+")
VOWEL_RE = re.compile(r"[aeiouyäöüAEIOUYÄÖÜ]")
# Short German/English words and abbreviations that are not letter soup
SHORT_WORDS = frozenset(
    "ab am an ad au da du er es et im in ja je ob so um wo zu zb bz dr mag ing dipl dkfm mr ms "
    "of to is it at by on or as an if no we he be me my up us do go ag kg og eu ug eh nr tel fax "
    "bg lg og vwgh ogh ovg bgh hg ra rae ri".split()
)
LOW_CONF = 0.75
SENTENCE_MIN_WORDS = 5
SENTENCE_HIT_RATIO = 0.6


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("ß", "ss")
    return re.sub(r"\s+", " ", text)


def _squash(text: str) -> str:
    """Letters and digits only: tolerant to spacing/punctuation differences in values."""
    return re.sub(r"[^a-z0-9§]", "", _normalize(text))


@dataclass
class PageEval:
    set_name: str
    file: str
    page: int
    kind: str = ""  # manifest kind (scan_pdf, scan_image, refscan, rendered, photo, small)
    area: str = ""
    source: str = ""
    dpi: Optional[float] = None
    words: int = 0
    chars: int = 0
    garbled: int = 0
    garbled_examples: List[str] = field(default_factory=list)
    merged: int = 0
    ocr_score: Optional[float] = None
    seconds: float = 0.0  # file conversion seconds / pages of the file
    ocr_scale: Optional[float] = None
    osd_rotations: int = 0
    osd_failed: int = 0
    flagged: bool = False
    flag_reasons: List[str] = field(default_factory=list)
    # reference-based (refscans / rendered)
    has_ref: bool = False
    ref_words: int = 0
    word_recall: Optional[float] = None
    sentence_total: int = 0
    sentence_lost: int = 0
    lost_sentences: List[str] = field(default_factory=list)
    value_total: int = 0
    value_lost: int = 0
    lost_values: List[str] = field(default_factory=list)
    extra_values: List[str] = field(default_factory=list)  # values in OCR text absent from the reference
    material_loss: Optional[bool] = None
    error: str = ""


def _ref_pages(ref_text: str) -> List[str]:
    return ref_text.split("\f")


def _values(text: str) -> List[str]:
    out: List[str] = []
    for rx in (AMOUNT_RE, DATE_RE, GZ_RE, PARA_RE, IBAN_RE):
        out.extend(m.strip() for m in rx.findall(text))
    return [v for v in dict.fromkeys(out) if len(_squash(v)) >= 4]


def _sentence_loss(ref_page: str, ocr_vocab: set) -> Tuple[int, int, List[str]]:
    total = lost = 0
    examples: List[str] = []
    for line in ref_page.splitlines():
        words = [w for w in WORD_RE.findall(_normalize(line))]
        if len(words) < SENTENCE_MIN_WORDS:
            continue
        total += 1
        hits = sum(1 for w in words if w in ocr_vocab)
        if hits / len(words) < SENTENCE_HIT_RATIO:
            lost += 1
            if len(examples) < 6:
                examples.append(line.strip()[:110])
    return total, lost, examples


def _value_loss(ref_page: str, ocr_page: str) -> Tuple[int, int, List[str], List[str]]:
    ocr_sq = _squash(ocr_page)
    ref_vals = _values(ref_page)
    lost = [v for v in ref_vals if _squash(v) not in ocr_sq]
    ref_sq = _squash(ref_page)
    extra = [v for v in _values(ocr_page) if _squash(v) not in ref_sq]
    return len(ref_vals), len(lost), lost[:12], extra[:12]


def _garbled_runs(text: str) -> Tuple[int, List[str]]:
    """Same heuristic as the harness (kept here so older meta.json files are comparable)."""
    n = 0
    examples: List[str] = []
    for ln in text.splitlines():
        for run in re.split(r"[.,;:!?()]\s*", ln):
            toks = TOKEN_RE.findall(run)
            if len(toks) < 4:
                continue
            bad = sum(1 for t in toks if (len(t) <= 2 and t.lower() not in SHORT_WORDS) or not VOWEL_RE.search(t))
            if bad / len(toks) >= 0.5 and bad >= 3:
                n += 1
                if len(examples) < 5:
                    examples.append(run.strip()[:90])
                break
    return n, examples


def load_run(runs: Path, label: str, manifest: Dict[str, Dict[str, Any]]) -> List[PageEval]:
    pages: List[PageEval] = []
    run_dir = runs / label
    if not run_dir.is_dir():
        raise SystemExit(f"no such run: {run_dir}")
    for set_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        for file_dir in sorted(p for p in set_dir.iterdir() if p.is_dir()):
            meta_path = file_dir / "meta.json"
            if not meta_path.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            entry = manifest.get(Path(meta["file"]).name, {})
            ref_text = (file_dir / "gold.txt").read_text(encoding="utf-8", errors="replace") if (file_dir / "gold.txt").exists() else None
            ref_pages = _ref_pages(ref_text) if ref_text is not None else []
            n_pages = max(meta.get("pages", 0), len(meta.get("page_meta", [])), 1)
            osd = meta.get("osd") or {}
            for pm in meta.get("page_meta") or [{"page_no": 1}]:
                page_no = int(pm.get("page_no", 1))
                pe = PageEval(set_name=set_dir.name, file=meta["file"], page=page_no, kind=entry.get("kind", ""),
                              area=entry.get("area", ""), source=entry.get("source", ""), dpi=pm.get("dpi"),
                              seconds=round(float(meta.get("convert_seconds", 0.0)) / n_pages, 2),
                              ocr_scale=meta.get("ocr_scale"), osd_rotations=int(osd.get("rotations", 0)),
                              osd_failed=int(osd.get("osd_failed", 0)), error=meta.get("error", ""))
                page_txt = file_dir / f"page_{page_no:02d}.txt"
                text = page_txt.read_text(encoding="utf-8", errors="replace") if page_txt.exists() else ""
                pe.words = len(WORD_RE.findall(text))
                pe.chars = len(text)
                pe.garbled, pe.garbled_examples = _garbled_runs(text)
                pe.merged = int(pm.get("merged_tokens", 0))
                pe.ocr_score = pm.get("ocr_score")
                if pe.garbled:
                    pe.flag_reasons.append(f"garbled x{pe.garbled}")
                if pe.ocr_score is not None and pe.ocr_score < LOW_CONF and pe.words >= 5:
                    pe.flag_reasons.append(f"ocr_score {pe.ocr_score:.2f}")
                if meta.get("status") != "ok":
                    pe.flag_reasons.append("conversion error")
                pe.flagged = bool(pe.flag_reasons)
                if ref_pages and page_no - 1 < len(ref_pages):
                    ref_page = ref_pages[page_no - 1]
                    pe.has_ref = True
                    ref_words = WORD_RE.findall(_normalize(ref_page))
                    pe.ref_words = len(ref_words)
                    vocab = set(WORD_RE.findall(_normalize(text)))
                    if ref_words:
                        pe.word_recall = round(sum(1 for w in ref_words if w in vocab) / len(ref_words), 3)
                    pe.sentence_total, pe.sentence_lost, pe.lost_sentences = _sentence_loss(ref_page, vocab)
                    pe.value_total, pe.value_lost, pe.lost_values, pe.extra_values = _value_loss(ref_page, text)
                    pe.material_loss = bool(pe.sentence_lost or pe.value_lost)
                pages.append(pe)
    return pages


def load_manifest(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if not path or not path.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            out[Path(entry["out_file"]).name] = entry
    return out


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------

def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100.0 * n / d:.1f} %)" if d else "–"


def _mean(xs: Iterable[Optional[float]]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return round(statistics.fmean(vals), 3) if vals else None


def summarise(pages: List[PageEval]) -> Dict[str, Any]:
    by_set: Dict[str, List[PageEval]] = defaultdict(list)
    for p in pages:
        by_set[p.set_name].append(p)
    out: Dict[str, Any] = {"sets": {}, "pages": len(pages)}
    for name, ps in sorted(by_set.items()):
        ref = [p for p in ps if p.has_ref]
        row: Dict[str, Any] = {
            "pages": len(ps),
            "files": len({p.file for p in ps}),
            "errors": sum(1 for p in ps if p.error),
            "words": sum(p.words for p in ps),
            "pages_with_text": sum(1 for p in ps if p.words >= 5),
            "garbled_runs": sum(p.garbled for p in ps),
            "pages_with_garbled": sum(1 for p in ps if p.garbled),
            "merged_tokens": sum(p.merged for p in ps),
            "mean_ocr_score": _mean(p.ocr_score for p in ps),
            "pages_low_score": sum(1 for p in ps if p.ocr_score is not None and p.ocr_score < LOW_CONF and p.words >= 5),
            "flagged_pages": sum(1 for p in ps if p.flagged),
            "osd_rotations": sum(p.osd_rotations for p in ps),
            "osd_failed": sum(p.osd_failed for p in ps),
            "seconds_total": round(sum(p.seconds for p in ps), 1),
            "seconds_per_page_median": round(statistics.median(p.seconds for p in ps), 2) if ps else None,
            "seconds_per_page_mean": round(statistics.fmean(p.seconds for p in ps), 2) if ps else None,
            "seconds_per_page_max": round(max(p.seconds for p in ps), 1) if ps else None,
        }
        if ref:
            row.update({
                "ref_pages": len(ref),
                "mean_word_recall": _mean(p.word_recall for p in ref),
                "min_word_recall": min((p.word_recall for p in ref if p.word_recall is not None), default=None),
                "sentences": sum(p.sentence_total for p in ref),
                "sentences_lost": sum(p.sentence_lost for p in ref),
                "values": sum(p.value_total for p in ref),
                "values_lost": sum(p.value_lost for p in ref),
                "extra_values": sum(len(p.extra_values) for p in ref),
                "material_loss_pages": sum(1 for p in ref if p.material_loss),
                "material_loss_rate": round(sum(1 for p in ref if p.material_loss) / len(ref), 4),
            })
        out["sets"][name] = row
    return out


def eval_md(label: str, summary: Dict[str, Any], pages: List[PageEval]) -> str:
    lines = [f"# OCR run `{label}` – {summary['pages']} pages", ""]
    lines.append("| set | pages | files | err | words | garbled runs (pages) | glued | mean OCR score | low-score pages | flagged | OSD rot / fail | s/page median (mean, max) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, r in summary["sets"].items():
        lines.append(f"| {name} | {r['pages']} | {r['files']} | {r['errors']} | {r['words']} | {r['garbled_runs']} ({r['pages_with_garbled']}) | {r['merged_tokens']} | "
                     f"{r['mean_ocr_score']} | {r['pages_low_score']} | {_pct(r['flagged_pages'], r['pages'])} | {r['osd_rotations']} / {r['osd_failed']} | "
                     f"{r['seconds_per_page_median']} ({r['seconds_per_page_mean']}, {r['seconds_per_page_max']}) |")
    ref_sets = {k: v for k, v in summary["sets"].items() if "ref_pages" in v}
    if ref_sets:
        lines += ["", "## Reference-based (material loss = a sentence or a value of the reference is missing)", "",
                  "| set | ref pages | mean word recall | min | sentences lost | values lost | extra values | material-loss pages |",
                  "|---|---|---|---|---|---|---|---|"]
        for name, r in ref_sets.items():
            lines.append(f"| {name} | {r['ref_pages']} | {r['mean_word_recall']} | {r['min_word_recall']} | {_pct(r['sentences_lost'], r['sentences'])} | "
                         f"{_pct(r['values_lost'], r['values'])} | {r['extra_values']} | **{_pct(r['material_loss_pages'], r['ref_pages'])}** |")
        lines += ["", "### Material-loss pages (reference sets)", ""]
        for p in [p for p in pages if p.material_loss]:
            lines.append(f"- `{p.set_name}/{p.file}` p{p.page} (recall {p.word_recall}, score {p.ocr_score}): "
                         f"lost sentences {p.sentence_lost}/{p.sentence_total}, lost values {p.lost_values}; "
                         + "; ".join(f"«{s}»" for s in p.lost_sentences[:2]))
    lines += ["", "## Flagged pages without reference (letter soup / low OCR confidence)", ""]
    for p in [p for p in pages if p.flagged and not p.has_ref][:150]:
        lines.append(f"- `{p.set_name}/{p.file}` p{p.page} [{p.kind}] {', '.join(p.flag_reasons)}: " + " | ".join(p.garbled_examples[:2]))
    return "\n".join(lines) + "\n"


def compare_md(a: str, b: str, pa: List[PageEval], pb: List[PageEval], sa: Dict[str, Any], sb: Dict[str, Any]) -> str:
    key = lambda p: (p.set_name, p.file, p.page)  # noqa: E731
    da, db = {key(p): p for p in pa}, {key(p): p for p in pb}
    common = sorted(set(da) & set(db))
    lines = [f"# `{a}` vs `{b}` – {len(common)} common pages", ""]
    lines.append(f"| set | metric | {a} | {b} |")
    lines.append("|---|---|---|---|")
    for name in sorted(set(sa["sets"]) & set(sb["sets"])):
        ra, rb = sa["sets"][name], sb["sets"][name]
        for m in ("words", "garbled_runs", "pages_with_garbled", "merged_tokens", "mean_ocr_score", "pages_low_score", "flagged_pages",
                  "osd_rotations", "osd_failed", "seconds_per_page_median", "seconds_per_page_mean", "seconds_total",
                  "mean_word_recall", "min_word_recall", "sentences_lost", "values_lost", "extra_values", "material_loss_pages", "material_loss_rate"):
            if m in ra or m in rb:
                lines.append(f"| {name} | {m} | {ra.get(m)} | {rb.get(m)} |")
    lines += ["", "## Pages whose verdict changed", ""]
    changed = 0
    for k in common:
        x, y = da[k], db[k]
        xv = x.material_loss if x.has_ref else x.flagged
        yv = y.material_loss if y.has_ref else y.flagged
        if xv != yv:
            changed += 1
            what = "FIXED" if xv and not yv else "REGRESSED"
            detail = (f"recall {x.word_recall}->{y.word_recall}, lost values {x.lost_values}->{y.lost_values}, lost sentences {x.sentence_lost}->{y.sentence_lost}"
                      if x.has_ref else f"{x.flag_reasons}->{y.flag_reasons}; {x.garbled_examples[:1]}->{y.garbled_examples[:1]}")
            lines.append(f"- {what} `{k[0]}/{k[1]}` p{k[2]} [{x.kind}]: {detail}")
    lines.insert(3, f"Verdict changes: {changed} pages. ")
    ref_common = [k for k in common if da[k].has_ref]
    if ref_common:
        d = [((db[k].word_recall or 0) - (da[k].word_recall or 0)) for k in ref_common]
        lines += ["", f"Word recall on {len(ref_common)} reference pages: mean delta {statistics.fmean(d):+.4f}, "
                      f"improved {sum(1 for v in d if v > 0.005)}, worse {sum(1 for v in d if v < -0.005)}."]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("eval")
    e.add_argument("runs", type=Path)
    e.add_argument("label")
    e.add_argument("--manifest", type=Path, default=None)
    c = sub.add_parser("compare")
    c.add_argument("runs", type=Path)
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--manifest", type=Path, default=None)
    args = ap.parse_args(argv)
    manifest = load_manifest(args.manifest or (args.runs.parent / "sample" / "manifest.jsonl"))
    if args.cmd == "eval":
        pages = load_run(args.runs, args.label, manifest)
        summary = summarise(pages)
        (args.runs / args.label / "eval.json").write_text(
            json.dumps({"summary": summary, "pages": [asdict(p) for p in pages]}, ensure_ascii=False, indent=1), encoding="utf-8")
        md = eval_md(args.label, summary, pages)
        (args.runs / args.label / "eval.md").write_text(md, encoding="utf-8")
        print(md.split("## Flagged")[0])
        return 0
    pa, pb = load_run(args.runs, args.a, manifest), load_run(args.runs, args.b, manifest)
    md = compare_md(args.a, args.b, pa, pb, summarise(pa), summarise(pb))
    out = args.runs / f"compare_{args.a}_vs_{args.b}.md"
    out.write_text(md, encoding="utf-8")
    print(md.split("## Pages whose")[0])
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
