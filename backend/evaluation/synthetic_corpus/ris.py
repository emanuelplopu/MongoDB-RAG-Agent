"""RIS OGD client for seed court decisions.

Uses the public RIS OGD API (v2.6). Decisions are free works under § 7 Abs 1
UrhG and the OGD data is CC BY 4.0. The client follows the RIS OGD FAQ rules:
one request at a time, at least two seconds apart, with an identifying
User-Agent. Every response is cached on disk so reruns do not hit RIS again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from .config import RisConfig
from .models import RisQuery

logger = logging.getLogger(__name__)

_NS = "{http://www.bka.gv.at}"
_SKIP_TAGS = {"kzinhalt", "fzinhalt", "metadaten", "layoutdaten"}


class RisDecision(BaseModel):
    """A decision with metadata and extracted plain text."""

    document_id: str
    geschaeftszahl: str
    court: str
    decision_date: str
    url_xml: str
    url_html: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.court} {self.geschaeftszahl} ({self.decision_date}), RIS {self.document_id}, CC BY 4.0"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def ris_xml_to_text(xml_text: str) -> str:
    """Convert a RIS Judikatur XML document to readable text.

    Section headings (``ueberschrift``) become Markdown headings, paragraphs
    (``absatz``) become lines; page header/footer blocks are skipped.
    """
    root = ET.fromstring(xml_text)
    lines: list[str] = []

    def walk(node: ET.Element) -> None:
        tag = node.tag.replace(_NS, "")
        if tag in _SKIP_TAGS:
            return
        if tag == "ueberschrift":
            text = " ".join("".join(node.itertext()).split())
            if text:
                lines.append(f"\n## {text}")
            return
        if tag == "absatz":
            if node.get("typ") in {"kz", "fz"}:
                return
            text = " ".join("".join(node.itertext()).split())
            if text:
                lines.append(text)
            return
        for child in node:
            walk(child)

    walk(root)
    return "\n".join(lines).strip()


class RisClient:
    """Rate-limited, caching RIS Judikatur client."""

    def __init__(self, cfg: RisConfig, cache_dir: Path, contact: Optional[str] = None):  # noqa: D107
        self.cfg = cfg
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        ua = cfg.user_agent + (f" ({contact})" if contact else "")
        self._http = httpx.Client(timeout=cfg.timeout_s, headers={"User-Agent": ua}, follow_redirects=True)
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def _get(self, url: str, params: Optional[dict[str, str]] = None) -> httpx.Response:
        wait = self.cfg.min_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self._http.get(url, params=params)
        finally:
            self._last_request = time.monotonic()
        resp.raise_for_status()
        return resp

    def _cached_json(self, name: str, url: str, params: dict[str, str]) -> dict[str, Any]:
        path = self.cache_dir / f"{name}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        data = self._get(url, params).json()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def search(self, query: RisQuery, page_size: Optional[str] = None) -> list[dict[str, Any]]:
        """Run a Judikatur search and return raw document references."""
        params: dict[str, str] = {
            "Applikation": query.applikation,
            "Suchworte": query.suchworte,
            "Dokumenttyp.SucheInEntscheidungstexten": "true",
            "DokumenteProSeite": page_size or self.cfg.search_page_size,
            "Sortierung.SortDirection": "Descending",
        }
        if query.gericht:
            params["Gericht"] = query.gericht
        if query.entscheidungsdatum_von:
            params["EntscheidungsdatumVon"] = query.entscheidungsdatum_von
        if query.norm:
            params["Norm"] = query.norm
        name = "search_" + hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
        data = self._cached_json(name, f"{self.cfg.base_url}/Judikatur", params)
        results = data.get("OgdSearchResult", {}).get("OgdDocumentResults", {})
        return _as_list(results.get("OgdDocumentReference"))

    def by_geschaeftszahl(self, geschaeftszahl: str, applikation: str = "Justiz") -> list[dict[str, Any]]:
        """Look up decisions by case number (e.g. ``6Ob116/17b``)."""
        params = {
            "Applikation": applikation,
            "Geschaeftszahl": geschaeftszahl,
            "Dokumenttyp.SucheInEntscheidungstexten": "true",
        }
        name = "gz_" + re.sub(r"[^A-Za-z0-9]+", "_", geschaeftszahl)
        data = self._cached_json(name, f"{self.cfg.base_url}/Judikatur", params)
        results = data.get("OgdSearchResult", {}).get("OgdDocumentResults", {})
        return _as_list(results.get("OgdDocumentReference"))

    def fetch(self, reference: dict[str, Any]) -> Optional[RisDecision]:
        """Download and parse the decision behind a search reference."""
        data = reference.get("Data", {})
        meta = data.get("Metadaten", {})
        tech, jud = meta.get("Technisch", {}), meta.get("Judikatur", {})
        doc_id = tech.get("ID", "")
        urls: dict[str, str] = {}
        for content in _as_list(data.get("Dokumentliste", {}).get("ContentReference")):
            for u in _as_list(content.get("Urls", {}).get("ContentUrl")):
                urls.setdefault(u.get("DataType", ""), u.get("Url", ""))
        if not doc_id or "Xml" not in urls:
            return None
        cache = self.cache_dir / f"doc_{doc_id}.json"
        if cache.is_file():
            return RisDecision.model_validate_json(cache.read_text(encoding="utf-8"))
        xml_text = self._get(urls["Xml"]).text
        gz = jud.get("Geschaeftszahl", {})
        gz_items = _as_list(gz.get("item") if isinstance(gz, dict) else gz)
        decision = RisDecision(
            document_id=doc_id,
            geschaeftszahl=", ".join(str(x) for x in gz_items),
            court=tech.get("Organ", ""),
            decision_date=jud.get("Entscheidungsdatum", ""),
            url_xml=urls["Xml"],
            url_html=urls.get("Html", ""),
            text=ris_xml_to_text(xml_text),
        )
        cache.write_text(decision.model_dump_json(), encoding="utf-8")
        return decision
