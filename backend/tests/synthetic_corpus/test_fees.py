"""Tests for the RATG/GGG fee engine of the synthetic corpus generator.

All expected values are computed by hand from the tariff tables (BGBl. II
Nr. 131/2023 for the RATG; BGBl. II Nr. 51/2025 and 227/2026, BGBl. I Nr.
62/2026 and the statutory amounts for the GGG). No test touches the network.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.evaluation.synthetic_corpus.fees import (
    DEFAULT_TABLES_DIR,
    CostItemRequest,
    FeeEngine,
    FeeTableError,
)

D = Decimal
RATG_DAY = date(2025, 6, 1)


@pytest.fixture(scope="module")
def engine() -> FeeEngine:
    """Engine over the shipped tables."""
    return FeeEngine()


def ratg(post: str, basis: str, *, factor: int = 1, erv: str = "none", es: bool = True,
         on: date = RATG_DAY, description: str = "Leistung") -> CostItemRequest:
    """Build an RATG cost item."""
    return CostItemRequest(kind="ratg", post=post, service_date=on, bemessungsgrundlage=D(basis),
                           description=description, einheitssatz=es, einheitssatz_factor=factor, erv=erv)


# ─── Shipped tables ──────────────────────────────────────────────────────────


def test_shipped_tables_are_complete_and_sourced() -> None:
    files = sorted(DEFAULT_TABLES_DIR.glob("*.json"))
    stems = {f.stem for f in files}
    assert {"ratg_2023-05-01", "ggg_2022-05-01", "ggg_2025-04-01", "ggg_2026-08-01", "ggg_2026-10-01"} <= stems
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["law"] in {"RATG", "GGG"}
        assert data["valid_from"] == path.stem.split("_", 1)[1]
        assert set(data["source"]) >= {"bgbl", "ris_url", "retrieved"}
        assert isinstance(data["verified"], bool)
        assert data["notes"]


def test_reference_procedure_file_is_sourced() -> None:
    path = DEFAULT_TABLES_DIR.parent / "reference" / "austrian_procedure.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for section in ("value_limits", "court_registers", "deadlines", "interest_rates", "ratg_service_mapping"):
        for entry in data[section]:
            assert entry["source"], entry
            assert isinstance(entry["verified"], bool), entry
    engine = FeeEngine()
    for entry in data["ratg_service_mapping"]:
        # every mapped post must be priceable by the engine
        assert engine.ratg_fee(entry["post"], D("5000"), RATG_DAY) > 0


def test_table_for_selects_by_date(engine: FeeEngine) -> None:
    assert engine.table_for("RATG", date(2023, 5, 1))["table_id"] == "ratg_2023-05-01"
    assert engine.table_for("ggg", date(2025, 3, 31))["table_id"] == "ggg_2022-05-01"
    assert engine.table_for("GGG", date(2025, 4, 1))["table_id"] == "ggg_2025-04-01"
    assert engine.table_for("GGG", date(2026, 7, 31))["table_id"] == "ggg_2025-04-01"
    assert engine.table_for("GGG", date(2026, 8, 1))["table_id"] == "ggg_2026-08-01"
    assert engine.table_for("GGG", date(2026, 9, 30))["table_id"] == "ggg_2026-08-01"
    assert engine.table_for("GGG", date(2026, 10, 1))["table_id"] == "ggg_2026-10-01"
    assert engine.table_for("GGG", date(2030, 1, 1))["table_id"] == "ggg_2026-10-01"


def test_table_for_returns_a_copy(engine: FeeEngine) -> None:
    table = engine.table_for("RATG", RATG_DAY)
    table["posts"]["TP1"]["cap"] = "0"
    assert engine.ratg_fee("TP1", D("5000000"), RATG_DAY) == D("312.20")
    assert engine.ratg_fee("TP1", D("1000000"), RATG_DAY) == D("175.20")  # 143.402 + 31.832, below the cap


def test_dates_before_earliest_table_raise(engine: FeeEngine) -> None:
    with pytest.raises(FeeTableError):
        engine.table_for("RATG", date(2023, 4, 30))
    with pytest.raises(FeeTableError):
        engine.ratg_fee("TP3A", D("1000"), date(2023, 4, 30))
    with pytest.raises(FeeTableError):
        engine.ggg_fee("TP1", D("1000"), date(2022, 4, 30))
    with pytest.raises(FeeTableError):
        engine.table_for("NO", RATG_DAY)


# ─── RATG scales ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("basis", "expected"),
    [
        ("0", "35.10"),
        ("40", "35.10"),
        ("40.01", "52.50"),
        ("5450", "208.20"),
        ("10170", "346.60"),
        ("10170.01", "381.70"),  # first started step of 1 450
        ("11620", "381.70"),  # exactly one full step
        ("11620.01", "416.80"),  # second step started
        ("20000", "592.30"),  # 7 steps: 346.60 + 7 * 35.10
        ("34820", "943.30"),  # 17 steps
        ("34820.01", "978.40"),  # + flat 35.10 for 34 820 to 36 340
        ("36340", "978.40"),
        ("36341", "978.40"),  # 978.40 + 0.001 rounds to 10 cent
        ("50000", "992.10"),  # 978.40 + 1 vT of 13 660 = 992.06 -> 992.10
        ("363360", "1305.40"),  # 978.40 + 327.02 = 1305.42
        ("1000000", "1623.70"),  # 1305.42 + 0.5 vT of 636 640 = 1623.74
        ("100000000", "20770.60"),  # cap
    ],
)
def test_ratg_tp3a_bands_steps_and_cap(engine: FeeEngine, basis: str, expected: str) -> None:
    assert engine.ratg_fee("TP3A", D(basis), RATG_DAY) == D(expected)


@pytest.mark.parametrize(
    ("post", "basis", "expected"),
    [
        ("TP1", "5000", "21.50"),
        ("TP1", "10170", "35.10"),
        ("TP1", "100000", "117.10"),  # 110.70 + 0.1 vT of 63 660 = 117.066
        ("TP1", "5000000", "312.20"),  # cap
        ("TP2", "7864.80", "173.80"),
        ("TP2", "15000", "245.40"),  # 4 steps of 17.90
        ("TP2", "36340", "496.00"),  # 173.80 + 17 * 17.90 + 17.90
        ("TP3B", "1326.95", "195.20"),
        ("TP3B", "10170", "433.20"),
        ("TP3C", "50000", "1485.10"),  # 519.60 + 17 * 52.50 + 52.50 + 20.49
        ("TP5", "5000", "17.40"),  # 10.80 + 2 * 3.30
        ("TP5", "50000", "104.60"),  # cap
        ("TP6", "5000", "34.80"),  # twice TP 5
        ("TP6", "50000", "208.20"),  # 2 * 104.60 capped at 208.20
        ("TP8", "5000", "85.80"),  # 52.50 + 3 * 11.10, one half hour
        ("tp 3a", "40", "35.10"),  # case and blanks are ignored
    ],
)
def test_ratg_other_posts(engine: FeeEngine, post: str, basis: str, expected: str) -> None:
    assert engine.ratg_fee(post, D(basis), RATG_DAY) == D(expected)


def test_ratg_float_input_is_handled_like_its_decimal_text(engine: FeeEngine) -> None:
    assert engine.ratg_fee("TP3A", 10170.01, RATG_DAY) == D("381.70")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("post", "basis", "expected"),
    [
        ("TP3A:1h", "20000", "592.30"),
        ("TP3A:2h", "20000", "888.50"),  # 592.30 + half 296.15 -> 296.20
        ("TP3A:61min", "20000", "888.50"),  # second hour started
        ("TP3A:150min", "20000", "1184.70"),  # three started hours
        ("TP3B:2h", "10170", "649.80"),  # 433.20 + 216.60
        ("TP8:30min", "5000", "85.80"),
        ("TP8:45min", "5000", "171.60"),  # two started half hours
        ("TP8:5min", "5000", "34.30"),  # under 10 minutes: 4/10 of 85.80
    ],
)
def test_ratg_time_billing(engine: FeeEngine, post: str, basis: str, expected: str) -> None:
    assert engine.ratg_fee(post, D(basis), RATG_DAY) == D(expected)


@pytest.mark.parametrize("post", ["TP2:1h", "TP1:30min", "TP4", "TP9", "TP3D", "3A", "TP3A:0h", "TP3A:2x"])
def test_ratg_invalid_posts_raise(engine: FeeEngine, post: str) -> None:
    with pytest.raises(FeeTableError):
        engine.ratg_fee(post, D("1000"), RATG_DAY)


def test_ratg_negative_basis_raises(engine: FeeEngine) -> None:
    with pytest.raises(FeeTableError):
        engine.ratg_fee("TP1", D("-1"), RATG_DAY)


def test_einheitssatz_threshold_switch(engine: FeeEngine) -> None:
    assert engine.einheitssatz_rate(D("10170"), RATG_DAY) == D("0.60")
    assert engine.einheitssatz_rate(D("10170.01"), RATG_DAY) == D("0.50")
    assert engine.einheitssatz_rate(D("0"), RATG_DAY) == D("0.60")


# ─── GGG ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("post", "value", "on", "expected"),
    [
        # TP 1 (also Mahnklage) across the three valorisation periods
        ("TP1", "7864.80", date(2025, 3, 31), "792"),
        ("TP1", "7864.80", date(2025, 4, 1), "974"),
        ("TP1", "7864.80", date(2026, 7, 31), "974"),
        ("TP1", "7864.80", date(2026, 8, 1), "1030"),
        ("TP1", "7864.80", date(2026, 10, 1), "1030"),  # BBG 2027-2028 left the scale unchanged
        ("TP1", "150", date(2025, 4, 1), "31"),
        ("TP1", "150.01", date(2025, 4, 1), "59"),  # basis rounded up to 151
        ("TP1", "15000", date(2024, 9, 1), "792"),
        ("TP1", "350000", date(2025, 4, 1), "9576"),
        ("TP1", "350000.01", date(2025, 4, 1), "11165"),  # ceil(1.2 % of 350 001) + 6 964
        ("TP1", "1000000", date(2026, 8, 1), "19908"),  # 12 000 + 7 908
        ("TP2", "5000", date(2025, 4, 1), "749"),
        ("TP2", "400000", date(2026, 9, 1), "18687"),  # 7 200 + 11 487
        ("TP3", "1500", date(2025, 4, 1), "281"),
        ("TP3", "500000", date(2025, 4, 1), "25477"),  # 12 000 + 13 477
        ("TP4", "7864.80", date(2025, 6, 2), "246"),
        ("TP4", "70000", date(2025, 4, 1), "369"),
        ("TP4", "100000", date(2025, 4, 1), "450"),  # 369 + 2.7 per mille of 30 000
        ("TP4", "100000.50", date(2025, 4, 1), "451"),  # 369 + ceil(81.0027)
        ("TP4", "100000", date(2022, 5, 1), "381"),  # 300 + 81
        ("TP4_II", "5000", date(2025, 4, 1), "278"),  # ceil(1.5 * 185)
        ("TP4_III", "5000", date(2025, 4, 1), "370"),
        ("TP1_ARB", "2500", date(2025, 4, 1), "0"),
        ("TP1_ARB", "2500.01", date(2025, 4, 1), "224"),
        ("TP2_ARB", "1326.95", date(2026, 8, 1), "0"),
        ("tp 1", "300", date(2026, 8, 1), "62"),
    ],
)
def test_ggg_fees(engine: FeeEngine, post: str, value: str, on: date, expected: str) -> None:
    assert engine.ggg_fee(post, D(value), on) == D(expected).quantize(D("0.01"))


@pytest.mark.parametrize("post", ["TP5", "TP3A", "TP1_XYZ", "Pauschalgebühr"])
def test_ggg_invalid_posts_raise(engine: FeeEngine, post: str) -> None:
    with pytest.raises(FeeTableError):
        engine.ggg_fee(post, D("1000"), date(2025, 4, 1))


def test_ggg_2026_10_table_carries_new_family_fees(engine: FeeEngine) -> None:
    special = engine.table_for("GGG", date(2026, 10, 1))["special"]["family_law_flat_fees"]
    assert (special["TP1"], special["TP2"], special["TP3"]) == ("410", "449", "671")
    before = engine.table_for("GGG", date(2026, 9, 30))["special"]["family_law_flat_fees"]
    assert (before["TP1"], before["TP2"], before["TP3"]) == ("433", "475", "709")


# ─── Cost notes ──────────────────────────────────────────────────────────────


def test_cost_note_matches_real_appeal_cost_award(engine: FeeEngine) -> None:
    """OLG Linz 12 Ra 40/26b (12.08.2026): Berufungsbeantwortung, Streitwert EUR 1 326,95,
    costs EUR 658,99 including EUR 109,83 USt (TP 3B, threefold Einheitssatz, ERV)."""
    note = engine.cost_note([ratg("TP3B", "1326.95", factor=3, erv="further", on=date(2026, 5, 15))])
    line = note.lines[0]
    assert (line.base_amount, line.einheitssatz_amount, line.erv_amount) == (D("195.20"), D("351.36"), D("2.60"))
    assert note.ust_amount == D("109.83")
    assert note.total == D("658.99")


def test_cost_note_inkasso_case_with_court_fee_and_outlay(engine: FeeEngine) -> None:
    filed = date(2025, 3, 10)
    note = engine.cost_note([
        ratg("TP2", "7864.80", factor=2, erv="initial", on=filed, description="Mahnklage"),
        CostItemRequest(kind="ggg", post="TP1", service_date=filed, bemessungsgrundlage=D("7864.80"),
                        description="Pauschalgebühr TP 1 GGG"),
        CostItemRequest(kind="barauslage", post="Meldeauskunft", service_date=filed,
                        bemessungsgrundlage=D("0"), description="ZMR-Abfrage", amount=D("3.30")),
    ])
    mahnklage, court, outlay = note.lines
    # 173.80 + 120 % ES 208.56 + ERV 5.00
    assert (mahnklage.base_amount, mahnklage.einheitssatz_amount, mahnklage.erv_amount) == (
        D("173.80"), D("208.56"), D("5.00"))
    assert mahnklage.net_amount == D("387.36")
    assert mahnklage.table_id == "ratg_2023-05-01"
    assert (court.net_amount, court.table_id) == (D("792.00"), "ggg_2022-05-01")
    assert (outlay.net_amount, outlay.table_id) == (D("3.30"), "")
    assert note.fees_net == D("387.36")
    assert note.ust_rate == D("0.20")
    assert note.ust_amount == D("77.47")  # USt only on the RATG line
    assert note.court_fees == D("792.00")
    assert note.barauslagen == D("3.30")
    assert note.total == D("1260.13")


def test_cost_note_einheitssatz_threshold_and_erv_not_in_base(engine: FeeEngine) -> None:
    note = engine.cost_note([
        ratg("TP3A", "10170", erv="initial"),
        ratg("TP3A", "10170.01", erv="further"),
    ])
    low, high = note.lines
    assert (low.base_amount, low.einheitssatz_amount, low.erv_amount) == (D("346.60"), D("207.96"), D("5.00"))
    assert (high.base_amount, high.einheitssatz_amount, high.erv_amount) == (D("381.70"), D("190.85"), D("2.60"))
    assert note.fees_net == D("1134.71")
    assert note.ust_amount == D("226.94")
    assert note.total == D("1361.65")


def test_cost_note_hearing_letters_and_meetings(engine: FeeEngine) -> None:
    note = engine.cost_note([
        ratg("TP3A:2h", "20000", erv="further", description="Tagsatzung"),  # ERV ignored for hearings
        ratg("TP6", "20000", erv="further", description="Aufforderungsschreiben"),  # no ES, no ERV
        ratg("TP8:45min", "20000", description="Vergleichsgespräch"),  # no ES
        ratg("TP3A", "20000", es=False, description="Klage ohne ES"),
    ])
    hearing, letter, meeting, plain = note.lines
    assert (hearing.base_amount, hearing.einheitssatz_amount, hearing.erv_amount) == (
        D("888.50"), D("444.25"), D("0.00"))
    assert hearing.post == "TP3A:2h"
    # TP 5 at 20 000: 10.80 + 12 started steps of 1 450 above 2 910 * 3.30 = 50.40; TP 6 doubles it
    assert (letter.base_amount, letter.einheitssatz_amount, letter.erv_amount) == (D("100.80"), D("0"), D("0"))
    # TP 8 at 20 000: 52.50 + 13 * 11.10 = 196.80 per half hour
    assert (meeting.base_amount, meeting.einheitssatz_amount) == (D("393.60"), D("0"))
    assert (plain.base_amount, plain.einheitssatz_amount) == (D("592.30"), D("0"))
    assert note.fees_net == D("2419.45")  # 1332.75 + 100.80 + 393.60 + 592.30
    assert note.ust_amount == D("483.89")


def test_cost_note_date_selection_across_valorisations(engine: FeeEngine) -> None:
    items = [
        CostItemRequest(kind="ggg", post="TP1", service_date=day, bemessungsgrundlage=D("40000"), description="TP 1")
        for day in (date(2025, 3, 31), date(2025, 4, 1), date(2026, 8, 1), date(2026, 10, 1))
    ]
    note = engine.cost_note(items)
    assert [line.net_amount for line in note.lines] == [D("1556.00"), D("1914.00"), D("2023.00"), D("2023.00")]
    assert [line.table_id for line in note.lines] == [
        "ggg_2022-05-01", "ggg_2025-04-01", "ggg_2026-08-01", "ggg_2026-10-01"]
    assert note.fees_net == D("0.00")
    assert note.ust_amount == D("0.00")
    assert note.ust_rate == D("0.20")
    assert note.total == D("7516.00")


def test_cost_note_raises_for_date_without_table(engine: FeeEngine) -> None:
    with pytest.raises(FeeTableError):
        engine.cost_note([ratg("TP3A", "1000", on=date(2022, 12, 31))])


def test_cost_item_validation() -> None:
    with pytest.raises(ValidationError):
        CostItemRequest(kind="barauslage", post="Porto", service_date=RATG_DAY,
                        bemessungsgrundlage=D("0"), description="Porto")
    with pytest.raises(ValidationError):
        CostItemRequest(kind="barauslage", post="Porto", service_date=RATG_DAY,
                        bemessungsgrundlage=D("0"), description="Porto", amount=D("-1"))
    with pytest.raises(ValidationError):
        ratg("TP1", "-5")
    with pytest.raises(ValidationError):
        ratg("TP1", "5", factor=5)
    item = CostItemRequest(kind="ratg", post="TP2", service_date="2025-06-01", bemessungsgrundlage=7864.8,
                           description="float input")
    assert item.bemessungsgrundlage == D("7864.8")


# ─── Custom table directories ────────────────────────────────────────────────


def test_overlapping_tables_are_rejected(tmp_path: Path) -> None:
    source = DEFAULT_TABLES_DIR / "ggg_2025-04-01.json"
    shutil.copy(source, tmp_path / "ggg_2025-04-01.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    data["valid_from"] = "2026-01-01"
    data["valid_to"] = None
    (tmp_path / "ggg_2026-01-01.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FeeTableError, match="overlap"):
        FeeEngine(tmp_path)


def test_malformed_tables_are_rejected(tmp_path: Path) -> None:
    data = json.loads((DEFAULT_TABLES_DIR / "ratg_2023-05-01.json").read_text(encoding="utf-8"))
    data["posts"]["TP1"]["bands"][1]["up_to"] = "10"  # not ascending
    (tmp_path / "ratg_2023-05-01.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FeeTableError, match="ascending"):
        FeeEngine(tmp_path)


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FeeTableError):
        FeeEngine(tmp_path / "missing")
