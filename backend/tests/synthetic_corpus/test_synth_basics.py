"""Unit tests: config, formatting, identities, RIS parsing, seeds registry."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.evaluation.synthetic_corpus import ris as ris_mod
from backend.evaluation.synthetic_corpus.config import (
    RisConfig,
    SeedsConfig,
    SynthConfig,
    load_config,
    parse_override,
    resolve_api_key,
)
from backend.evaluation.synthetic_corpus.identities import IdentityFactory, extract_blocked_surnames
from backend.evaluation.synthetic_corpus.models import RisQuery
from backend.evaluation.synthetic_corpus.seeds import SeedRegistry, write_area_template
from backend.evaluation.synthetic_corpus.text_utils import contains_value, find_quote, format_date_de, format_eur, safe_filename

# ─── config ──────────────────────────────────────────────────────────────────


def test_defaults_use_requested_models() -> None:
    cfg = SynthConfig()
    assert cfg.llm.orchestrator.model == "gpt-6-astra"
    assert cfg.llm.generator.model == "gpt-6.1-sol"
    assert cfg.seeds.ris.min_interval_s >= 2.0


def test_load_config_merges_files_and_overrides(tmp_path: Path) -> None:
    f1 = tmp_path / "a.json"
    f1.write_text(json.dumps({"llm": {"concurrency": 2}, "run": {"run_name": "x"}}), encoding="utf-8")
    f2 = tmp_path / "b.json"
    f2.write_text(json.dumps({"llm": {"generator": {"model": "m2"}}}), encoding="utf-8")
    cfg = load_config([f1, f2], ["llm.concurrency=9", 'run.areas=["mietrecht_mietzins_raeumung"]', "firm.name=Kanzlei Test"])
    assert cfg.llm.concurrency == 9
    assert cfg.llm.generator.model == "m2"
    assert cfg.llm.orchestrator.model == "gpt-6-astra"
    assert cfg.run.run_name == "x"
    assert cfg.run.areas == ["mietrecht_mietzins_raeumung"]
    assert cfg.firm.name == "Kanzlei Test"


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config([], ["llm.concurency=3"])


def test_ris_interval_cannot_go_below_rules() -> None:
    with pytest.raises(ValidationError):
        RisConfig(min_interval_s=0.5)


def test_parse_override_types() -> None:
    assert parse_override("a.b=3") == (["a", "b"], 3)
    assert parse_override("a=true") == (["a"], True)
    assert parse_override("a=hello world") == (["a"], "hello world")
    with pytest.raises(ValueError):
        parse_override("novalue")


def test_resolve_api_key_prefers_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_API_KEY=from-file\n", encoding="utf-8")
    cfg = SynthConfig().llm.model_copy(update={"env_file": str(env)})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert resolve_api_key(cfg) == "from-file"
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert resolve_api_key(cfg) == "from-env"
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(RuntimeError):
        resolve_api_key(cfg.model_copy(update={"env_file": None}))


# ─── text utils ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [(0, "EUR 0,00"), (7864.8, "EUR 7.864,80"), (1234567.891, "EUR 1.234.567,89"), (-12.5, "EUR -12,50")])
def test_format_eur(value: float, expected: str) -> None:
    assert format_eur(value) == expected


def test_format_date_and_matching() -> None:
    assert format_date_de("2025-03-04") == "04.03.2025"
    assert contains_value("Betrag: EUR  7.864,80 offen", "EUR 7.864,80")
    assert not contains_value("EUR 7.864,81", "EUR 7.864,80")
    quote, prefix, suffix = find_quote("Die Forderung von EUR 7.864,80\nist fällig.", "EUR 7.864,80", context=10)
    assert quote == "EUR 7.864,80" and prefix.endswith("von ") and suffix.startswith("\nist")
    assert safe_filename('a/b:c*?"<>| ') == "a-b-c------"


# ─── identities ──────────────────────────────────────────────────────────────


def test_identities_deterministic_unique_and_blocklisted() -> None:
    a = IdentityFactory(1, blocklist=["Huber"])
    b = IdentityFactory(1, blocklist=["Huber"])
    pa = [a.person(f"P{i}", "x", "X", scope="c1", gender="female") for i in range(30)]
    pb = [b.person(f"P{i}", "x", "X", scope="c1", gender="female") for i in range(30)]
    assert [p.name for p in pa] == [p.name for p in pb]
    surnames = [p.name.split(" ")[-1] for p in pa]
    assert len(set(surnames)) == len(surnames)
    assert "Huber" not in surnames
    company = a.person("P99", "mandant", "M", scope="c1", kind="legal", name_word="Tischlerei", legal_form="GmbH")
    assert "Tischlerei" in company.name and company.name.endswith("GmbH")
    assert company.company_register_no.startswith("FN ")
    place = a.person("P98", "x", "X", scope="c1", postcode="2340")
    assert place.city == "Mödling"


def test_firm_has_gendered_staff_and_domain() -> None:
    firm = IdentityFactory(5).firm(None, "Wien", None, None, None, 2, 1, 1)
    assert firm.name.endswith("Rechtsanwälte OG")
    labels = [s.label for s in firm.staff]
    assert labels[:2] == ["Rechtsanwalt", "Rechtsanwältin"]
    assert all(s.email.endswith(firm.email_domain) for s in firm.staff)


def test_extract_blocked_surnames() -> None:
    text = "Rechtsanwalt Dr. Max Mustermann; Richterin Anna Bergmann; Mag. Paul Gruber (Wien)"
    assert {"Mustermann", "Bergmann", "Gruber"} <= extract_blocked_surnames([text])


# ─── RIS ─────────────────────────────────────────────────────────────────────

_XML = """<?xml version="1.0" encoding="utf-8"?><risdok xmlns="http://www.bka.gv.at"><metadaten/><nutzdaten>
<abschnitt><kzinhalt><absatz typ="kz">Kopfzeile</absatz></kzinhalt>
<ueberschrift typ="titel">Kopf</ueberschrift><absatz typ="sv">Der Oberste Gerichtshof hat <i>als</i> Revisionsgericht</absatz>
<fzinhalt><absatz typ="fz">www.ris.bka.gv.at Seite 1</absatz></fzinhalt>
<ueberschrift typ="titel">Spruch</ueberschrift><absatz typ="sp">Der Revision wird nicht Folge gegeben.</absatz></abschnitt></nutzdaten></risdok>"""


def test_ris_xml_to_text_skips_headers_and_footers() -> None:
    text = ris_mod.ris_xml_to_text(_XML)
    assert "## Kopf" in text and "als Revisionsgericht" in text and "## Spruch" in text
    assert "Kopfzeile" not in text and "Seite 1" not in text


def test_ris_client_search_fetch_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(ris_mod.time, "sleep", lambda s: sleeps.append(s))
    calls: list[str] = []
    ref = {"Data": {"Metadaten": {"Technisch": {"ID": "JJT_1", "Organ": "OGH"},
                                  "Judikatur": {"Geschaeftszahl": {"item": "6Ob1/25a"}, "Entscheidungsdatum": "2025-01-02"}},
                    "Dokumentliste": {"ContentReference": {"Urls": {"ContentUrl": [
                        {"DataType": "Xml", "Url": "https://ogd.example/JJT_1.xml"},
                        {"DataType": "Html", "Url": "https://ogd.example/JJT_1.html"}]}}}}}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["User-Agent"].startswith("RecallHub-synthetic-corpus")
        if request.url.path.endswith(".xml"):
            return httpx.Response(200, text=_XML)
        return httpx.Response(200, json={"OgdSearchResult": {"OgdDocumentResults": {"OgdDocumentReference": ref}}})

    client = ris_mod.RisClient(RisConfig(), tmp_path, contact="test@example.org")
    client._http = httpx.Client(transport=httpx.MockTransport(handler), headers=client._http.headers)
    refs = client.search(RisQuery(suchworte="Mahnklage", gericht="OGH"))
    decision = client.fetch(refs[0])
    assert decision is not None and decision.geschaeftszahl == "6Ob1/25a" and "Revision" in decision.text
    assert "CC BY 4.0" in decision.citation
    # second time everything comes from the cache
    client.search(RisQuery(suchworte="Mahnklage", gericht="OGH"))
    client.fetch(refs[0])
    assert len(calls) == 2
    assert sleeps and all(s <= 2.0 for s in sleeps)


# ─── seeds ───────────────────────────────────────────────────────────────────


def test_builtin_seeds_are_consistent() -> None:
    reg = SeedRegistry(SeedsConfig())
    assert {"inkasso_mahnklage_exekution", "mietrecht_mietzins_raeumung", "bau_gewaehrleistung_gutachten",
            "arbeitsrecht_entlassung", "datenschutz_schadenersatz"} <= set(reg.areas)
    for area in reg.areas.values():
        assert any(t.is_cost_note for t in area.document_types)
        assert all(c in reg.courts for c in area.courts)
    assert all(c.area in reg.areas for c in reg.cases.values())


def test_user_area_dir_extends_registry(tmp_path: Path) -> None:
    write_area_template(tmp_path, "familienrecht_test", "Familienrecht Test")
    reg = SeedRegistry(SeedsConfig(areas_dirs=[str(tmp_path)]))
    assert "familienrecht_test" in reg.areas
    assert reg.areas["familienrecht_test"].name == "Familienrecht Test"


def test_find_quote_prefers_occurrence_matching_hints() -> None:
    text = "Gesendet: 04.06.2025 08:38\nBetreff: Abrechnung\n\nDas Fremdgeld wurde am 04.06.2025 an die Mandantin weitergeleitet."
    quote, prefix, _ = find_quote(text, "04.06.2025", context=20, hints="Weiterleitung Fremdgeld an Mandantin")
    assert "Fremdgeld wurde am" in prefix
    quote, prefix, _ = find_quote(text, "04.06.2025", context=20)
    assert prefix.endswith("Gesendet: ")


def test_house_numbers_are_realistic() -> None:
    f = IdentityFactory(3)
    for i in range(40):
        street = f.person(f"P{i}", "x", "X", scope="h").street
        number = street.rsplit(" ", 1)[-1].split("/")[0]
        assert number.isdigit() and 1 <= int(number) <= 85


def test_example_config_with_comments_loads() -> None:
    from backend.evaluation.synthetic_corpus.config import DATA_DIR

    cfg = load_config([DATA_DIR / "configs" / "kanzlei_v1.example.json"])
    assert cfg.llm.concurrency == 6 and cfg.run.run_name == "kanzlei_v1"
