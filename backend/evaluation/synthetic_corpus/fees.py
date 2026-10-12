"""Fee engine for Austrian lawyer cost notes (RATG) and court fees (GGG).

Cost notes in generated case files must never contain invented amounts. This
module computes them from date-effective tariff tables in
``data/fee_tables/*.json``, transcribed from the official consolidated texts in
RIS and the Federal Law Gazette (BGBl). Each table names its legal basis in a
``source`` block and states whether every amount was checked (``verified``).

RATG post syntax (case and blanks are ignored):
    ``TP1``, ``TP2``, ``TP3A``, ``TP3B``, ``TP3C``, ``TP5``, ``TP6``, ``TP8``.
    Time-billed posts take an optional duration suffix: ``TP3A:2h`` is a
    hearing of two started hours (TP 3A Abschnitt II), ``TP3B:90min`` an
    appeal hearing, ``TP8:45min`` a meeting billed per started half hour.
    Without a suffix, TP 3A/3B/3C are the Schriftsatz amount (Abschnitt I) and
    TP 8 is one half hour.

GGG post syntax:
    ``TP1`` to ``TP4``, ``TP4_II`` and ``TP4_III`` (Exekution appeals, 150 % and
    200 %), and ``TP1_ARB`` to ``TP4_ARB`` for labour-law matters, which are
    fee-free up to the exemption limit and otherwise priced like the base post.

Rounding:
    * RATG tariff amounts: nearest 10 cent, half up (§ 1 Abs. 1 letzter Satz
      RATG). Einheitssatz, USt and sums: nearest cent, half up.
    * GGG: a Bemessungsgrundlage that is not a full euro amount and the
      percentage part of a fee are rounded up to the next full euro
      (§ 6 Abs. 2 GGG).

USt (20 %) is charged on the RATG lines only (fee + Einheitssatz + ERV).
Court fees and Barauslagen are passed through without USt.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_TABLES_DIR = Path(__file__).resolve().parent / "data" / "fee_tables"
"""Directory holding the shipped tariff tables."""

DEFAULT_UST_RATE = Decimal("0.20")
"""USt rate reported when a cost note has no RATG line (§ 10 Abs. 1 UStG 1994)."""

_LAWS = ("RATG", "GGG")
_CENT = Decimal("0.01")
_TEN_CENT = Decimal("0.1")
_EURO = Decimal("1")
_ZERO = Decimal("0.00")
_RATG_POST_RE = re.compile(r"^(TP\d+[A-C]?)(?::(\d+(?:\.\d+)?)(H|MIN))?$")
_GGG_POST_RE = re.compile(r"^TP\d(?:_[A-Z]+)?$")


class FeeTableError(Exception):
    """Raised for missing, malformed or non-applicable tariff tables and posts."""


# ─── Public models ────────────────────────────────────────────────────────────


class CostItemRequest(BaseModel):
    """One requested cost item; amounts are computed by :class:`FeeEngine`.

    Attributes:
        kind: ``ratg`` (lawyer fee), ``ggg`` (court fee) or ``barauslage``
            (cash outlay with a given amount).
        post: Tariff post, see the module docstring; free text for Barauslagen.
        service_date: Date of the service (RATG) or of the event that triggers
            the court fee (GGG, usually the filing date). Selects the table.
        bemessungsgrundlage: Streitwert or other basis in EUR.
        description: Text shown on the cost note line.
        einheitssatz: RATG only. Add the Einheitssatz (§ 23 RATG). Ignored for
            posts that do not carry one (TP 5, 6, 8).
        einheitssatz_factor: RATG only. Multiplier for the Einheitssatz:
            2 = doubled (§ 23 Abs. 5, 6, 8 RATG, e.g. Mahnklage, Klage with
            ordered Klagebeantwortung, Exekutionsantrag), 3 = tripled and
            4 = quadrupled (§ 23 Abs. 9 RATG, appeal without evidence taking).
            Extension of the agreed interface; defaults to 1.
        erv: RATG only. ERV surcharge (§ 23a RATG) for a pleading filed
            electronically: ``initial`` for the pleading that opens the
            proceeding, ``further`` for later ones. Ignored for hearings and
            for posts that are not court filings (TP 5, 6, 8).
        amount: Barauslage only. Ignored for ``ratg`` and ``ggg``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ratg", "ggg", "barauslage"]
    post: str = Field(min_length=1)
    service_date: date
    bemessungsgrundlage: Decimal = Field(ge=0)
    description: str
    einheitssatz: bool = True
    einheitssatz_factor: int = Field(default=1, ge=1, le=4)
    erv: Literal["none", "initial", "further"] = "none"
    amount: Decimal | None = None

    @model_validator(mode="after")
    def _check_barauslage_amount(self) -> CostItemRequest:
        """Require a non-negative amount for Barauslagen."""
        if self.kind == "barauslage":
            if self.amount is None:
                raise ValueError("a barauslage needs an amount")
            if self.amount < 0:
                raise ValueError("a barauslage amount must not be negative")
        return self


class CostLine(BaseModel):
    """One computed line of a cost note.

    ``table_id`` is the file stem of the tariff table used, or an empty string
    for Barauslagen.
    """

    description: str
    kind: str
    post: str
    service_date: date
    base_amount: Decimal
    einheitssatz_amount: Decimal
    erv_amount: Decimal
    net_amount: Decimal
    table_id: str


class CostNote(BaseModel):
    """A computed cost note (Kostennote / Kostenverzeichnis).

    ``fees_net`` is the sum of the RATG lines (fee, Einheitssatz and ERV);
    USt is charged on that sum only. ``total`` adds USt, court fees and
    Barauslagen.
    """

    lines: list[CostLine]
    fees_net: Decimal
    ust_rate: Decimal
    ust_amount: Decimal
    court_fees: Decimal
    barauslagen: Decimal
    total: Decimal


# ─── Internal table representation ───────────────────────────────────────────


@dataclass(frozen=True)
class _Table:
    """A loaded tariff table."""

    table_id: str
    law: str
    valid_from: date
    valid_to: date | None
    data: dict[str, Any]

    def covers(self, on: date) -> bool:
        """Return whether the table applies on ``on``."""
        return self.valid_from <= on and (self.valid_to is None or on <= self.valid_to)


def _dec(value: Any, where: str) -> Decimal:
    """Parse a table value as Decimal."""
    if value is None or isinstance(value, bool):
        raise FeeTableError(f"{where}: missing or invalid number {value!r}")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise FeeTableError(f"{where}: not a number: {value!r}") from exc


def _opt_dec(value: Any, where: str) -> Decimal | None:
    """Parse an optional table value as Decimal."""
    return None if value is None else _dec(value, where)


def _as_decimal(value: Decimal | int | float | str, name: str) -> Decimal:
    """Convert a caller-supplied amount to Decimal (floats via ``str``) and reject negatives."""
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise FeeTableError(f"{name} is not a number: {value!r}") from exc
    if not result.is_finite() or result < 0:
        raise FeeTableError(f"{name} must be a finite, non-negative amount, got {value!r}")
    return result


def _round_cents(value: Decimal) -> Decimal:
    """Round half up to cents."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _round_ten_cents(value: Decimal) -> Decimal:
    """Round half up to 10 cent (§ 1 Abs. 1 RATG) and express in cents."""
    return value.quantize(_TEN_CENT, rounding=ROUND_HALF_UP).quantize(_CENT)


def _ceil_euro(value: Decimal) -> Decimal:
    """Round up to the next full euro (§ 6 Abs. 2 GGG)."""
    return value.quantize(_EURO, rounding=ROUND_CEILING)


def _ceil_int(value: Decimal) -> int:
    """Smallest integer not below ``value``."""
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _parse_date(value: Any, where: str) -> date | None:
    """Parse an ISO date or ``None``."""
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise FeeTableError(f"{where}: invalid date {value!r}") from exc


def _normalise_post(post: str) -> str:
    """Upper-case a post code and drop blanks (``tp 3a`` becomes ``TP3A``)."""
    return re.sub(r"\s+", "", post).upper()


def _band_amount(post_data: dict[str, Any], basis: Decimal, where: str) -> Decimal | None:
    """Return the amount of the band containing ``basis`` or ``None`` above the top band."""
    bands = post_data.get("bands")
    if not bands:
        raise FeeTableError(f"{where}: no bands")
    previous = Decimal("-1")
    for index, band in enumerate(bands):
        up_to = _dec(band.get("up_to"), f"{where}.bands[{index}].up_to")
        if up_to <= previous:
            raise FeeTableError(f"{where}: bands must be strictly ascending")
        previous = up_to
        if basis <= up_to:
            return _dec(band.get("amount"), f"{where}.bands[{index}].amount")
    return None


def _scale_amount(post_data: dict[str, Any], basis: Decimal, where: str) -> Decimal:
    """Compute an unrounded RATG scale amount (bands, increments, cap)."""
    amount = _band_amount(post_data, basis, where)
    if amount is None:
        bands = post_data["bands"]
        amount = _dec(bands[-1]["amount"], f"{where}.bands[-1].amount")
        covered_to: Decimal | None = _dec(bands[-1]["up_to"], f"{where}.bands[-1].up_to")
        for index, seg in enumerate(post_data.get("segments") or []):
            seg_where = f"{where}.segments[{index}]"
            start = _dec(seg.get("from"), f"{seg_where}.from")
            end = _opt_dec(seg.get("to"), f"{seg_where}.to")
            if covered_to is None or start != covered_to:
                raise FeeTableError(f"{seg_where}: segments must be contiguous")
            covered_to = end
            if basis <= start:
                break
            portion = (basis if end is None else min(basis, end)) - start
            kind = seg.get("kind")
            if kind == "step":
                step = _dec(seg.get("step"), f"{seg_where}.step")
                amount += _ceil_int(portion / step) * _dec(seg.get("amount"), f"{seg_where}.amount")
            elif kind == "flat":
                amount += _dec(seg.get("amount"), f"{seg_where}.amount")
            elif kind == "per_mille":
                amount += portion * _dec(seg.get("rate"), f"{seg_where}.rate") / Decimal(1000)
            else:
                raise FeeTableError(f"{seg_where}: unknown segment kind {kind!r}")
        if covered_to is not None and basis > covered_to:
            raise FeeTableError(f"{where}: Bemessungsgrundlage {basis} is above the table range")
    cap = _opt_dec(post_data.get("cap"), f"{where}.cap")
    return amount if cap is None else min(amount, cap)


# ─── Engine ──────────────────────────────────────────────────────────────────


class FeeEngine:
    """Computes RATG lawyer fees, GGG court fees and complete cost notes.

    Args:
        tables_dir: Directory with ``*.json`` tariff tables. Defaults to the
            tables shipped with this package.

    Raises:
        FeeTableError: If a table is malformed or two tables of the same law
            overlap in time.
    """

    def __init__(self, tables_dir: Path | None = None) -> None:
        self._tables_dir = Path(tables_dir) if tables_dir is not None else DEFAULT_TABLES_DIR
        self._tables: dict[str, list[_Table]] = {law: [] for law in _LAWS}
        self._load()

    # ── loading ──

    def _load(self) -> None:
        """Load and validate every table file."""
        if not self._tables_dir.is_dir():
            raise FeeTableError(f"fee table directory not found: {self._tables_dir}")
        for path in sorted(self._tables_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FeeTableError(f"{path.name}: cannot read table: {exc}") from exc
            if not isinstance(data, dict):
                raise FeeTableError(f"{path.name}: table must be a JSON object")
            law = str(data.get("law", "")).upper()
            if law not in _LAWS:
                raise FeeTableError(f"{path.name}: unknown law {data.get('law')!r}")
            valid_from = _parse_date(data.get("valid_from"), f"{path.name}.valid_from")
            if valid_from is None:
                raise FeeTableError(f"{path.name}: valid_from is required")
            valid_to = _parse_date(data.get("valid_to"), f"{path.name}.valid_to")
            if valid_to is not None and valid_to < valid_from:
                raise FeeTableError(f"{path.name}: valid_to is before valid_from")
            if not isinstance(data.get("posts"), dict) or not data["posts"]:
                raise FeeTableError(f"{path.name}: posts are missing")
            table = _Table(path.stem, law, valid_from, valid_to, data)
            self._validate(table)
            self._tables[law].append(table)
        for law, tables in self._tables.items():
            tables.sort(key=lambda t: t.valid_from)
            for earlier, later in zip(tables, tables[1:]):
                if earlier.valid_to is None or earlier.valid_to >= later.valid_from:
                    raise FeeTableError(
                        f"{law} tables {earlier.table_id} and {later.table_id} overlap in time"
                    )

    def _validate(self, table: _Table) -> None:
        """Evaluate every post once so malformed tables fail at load time."""
        probes = (Decimal("0"), Decimal("5000"), Decimal("1000000"))
        if table.law == "RATG":
            for key in ("einheitssatz", "erv", "ust_rate"):
                if key not in table.data:
                    raise FeeTableError(f"{table.table_id}: {key} is missing")
        for post, data in table.data["posts"].items():
            if not isinstance(data, dict):
                raise FeeTableError(f"{table.table_id}.posts.{post}: must be an object")
            for probe in probes:
                if table.law == "GGG":
                    self._ggg_amount(table, post, probe)
                    continue
                self._ratg_amount(table, post, None, probe)
                if data.get("duration"):
                    for minutes in (Decimal(5), Decimal(150)):
                        self._ratg_amount(table, post, minutes, probe)

    # ── table lookup ──

    def _table(self, law: str, on: date) -> _Table:
        """Return the table of ``law`` covering ``on``."""
        key = law.upper()
        if key not in self._tables:
            raise FeeTableError(f"unknown law {law!r}; expected one of {', '.join(_LAWS)}")
        for table in self._tables[key]:
            if table.covers(on):
                return table
        raise FeeTableError(f"no {key} table covers {on.isoformat()}")

    def table_for(self, law: str, on: date) -> dict:
        """Return a copy of the table of ``law`` that applies on ``on``.

        Args:
            law: ``RATG`` or ``GGG`` (case-insensitive).
            on: Service or fee date.

        Returns:
            The table data with an added ``table_id`` key (file stem).

        Raises:
            FeeTableError: If no table covers the date.
        """
        table = self._table(law, on)
        data = copy.deepcopy(table.data)
        data["table_id"] = table.table_id
        return data

    # ── RATG ──

    @staticmethod
    def _ratg_section_one(table: _Table, post: str) -> dict[str, Any]:
        """Return the post data of an RATG post code (without duration)."""
        data = table.data["posts"].get(post)
        if not isinstance(data, dict):
            raise FeeTableError(f"unknown RATG post {post!r} in table {table.table_id}")
        return data

    def _ratg_base(self, table: _Table, post: str, basis: Decimal) -> Decimal:
        """Return the unrounded Abschnitt-I amount of an RATG post."""
        data = self._ratg_section_one(table, post)
        where = f"{table.table_id}.posts.{post}"
        derived = data.get("derived")
        if derived:
            source = str(derived.get("from", ""))
            if source == post or "derived" in self._ratg_section_one(table, source):
                raise FeeTableError(f"{where}: derived posts must point to a scale post")
            amount = self._ratg_base(table, source, basis) * _dec(derived.get("factor"), f"{where}.derived.factor")
            cap = _opt_dec(data.get("cap"), f"{where}.cap")
            return amount if cap is None else min(amount, cap)
        return _scale_amount(data, basis, where)

    @staticmethod
    def _parse_ratg_post(post: str) -> tuple[str, Decimal | None]:
        """Split an RATG post into its code and an optional duration in minutes."""
        match = _RATG_POST_RE.match(_normalise_post(post))
        if not match:
            raise FeeTableError(f"invalid RATG post {post!r}; expected e.g. 'TP3A' or 'TP3A:2h'")
        code, number, unit = match.groups()
        if number is None:
            return code, None
        minutes = Decimal(number) * (Decimal(60) if unit == "H" else Decimal(1))
        if minutes <= 0:
            raise FeeTableError(f"duration in RATG post {post!r} must be positive")
        return code, minutes

    @staticmethod
    def _canonical_ratg_post(post: str) -> str:
        """Return the canonical spelling of an RATG post, e.g. ``TP3A:2h``."""
        normalised = _normalise_post(post)
        return normalised.replace("MIN", "min").replace("H", "h") if ":" in normalised else normalised

    def ratg_fee(self, post: str, bemessungsgrundlage: Decimal, on: date) -> Decimal:
        """Compute an RATG fee (without Einheitssatz, ERV and USt).

        Args:
            post: RATG post, optionally with a duration suffix (``TP3A:2h``).
            bemessungsgrundlage: Basis in EUR.
            on: Date of the service.

        Returns:
            The fee in EUR, rounded to 10 cent.

        Raises:
            FeeTableError: For unknown posts, a duration on a post without
                time billing, a negative basis or a date without table.
        """
        basis = _as_decimal(bemessungsgrundlage, "Bemessungsgrundlage")
        table = self._table("RATG", on)
        code, minutes = self._parse_ratg_post(post)
        return self._ratg_amount(table, code, minutes, basis)

    def _ratg_amount(self, table: _Table, code: str, minutes: Decimal | None, basis: Decimal) -> Decimal:
        """Compute an RATG amount from a given table, with optional time billing."""
        data = self._ratg_section_one(table, code)
        base = _round_ten_cents(self._ratg_base(table, code, basis))
        if minutes is None:
            return base
        rule = data.get("duration")
        if not rule:
            raise FeeTableError(f"RATG post {code} is not billed by time; drop the duration suffix")
        where = f"{table.table_id}.posts.{code}.duration"
        short_below = rule.get("short_below_minutes")
        if short_below is not None and minutes < Decimal(short_below):
            short = base * _dec(rule.get("short_factor"), f"{where}.short_factor")
            short_cap = _opt_dec(rule.get("short_cap"), f"{where}.short_cap")
            return _round_ten_cents(short if short_cap is None else min(short, short_cap))
        unit_minutes = _dec(rule.get("unit_minutes"), f"{where}.unit_minutes")
        if unit_minutes <= 0:
            raise FeeTableError(f"{where}.unit_minutes must be positive")
        units = _ceil_int(minutes / unit_minutes)
        first_cap = _opt_dec(rule.get("first_unit_cap"), f"{where}.first_unit_cap")
        first = base if first_cap is None else min(base, first_cap)
        further = _round_ten_cents(base * _dec(rule.get("further_unit_factor"), f"{where}.further_unit_factor"))
        further_cap = _opt_dec(rule.get("further_unit_cap"), f"{where}.further_unit_cap")
        if further_cap is not None:
            further = min(further, further_cap)
        return _round_cents(first + (units - 1) * further)

    def einheitssatz_rate(self, bemessungsgrundlage: Decimal, on: date) -> Decimal:
        """Return the Einheitssatz rate under § 23 Abs. 3 RATG.

        Args:
            bemessungsgrundlage: Streitwert in EUR.
            on: Date of the service.

        Returns:
            For example ``Decimal("0.60")`` up to and including the threshold,
            ``Decimal("0.50")`` above it.
        """
        basis = _as_decimal(bemessungsgrundlage, "Bemessungsgrundlage")
        table = self._table("RATG", on)
        rule = table.data["einheitssatz"]
        where = f"{table.table_id}.einheitssatz"
        threshold = _dec(rule.get("threshold"), f"{where}.threshold")
        if basis <= threshold:
            return _dec(rule.get("rate_up_to_threshold"), f"{where}.rate_up_to_threshold")
        return _dec(rule.get("rate_above_threshold"), f"{where}.rate_above_threshold")

    # ── GGG ──

    def _ggg_amount(self, table: _Table, post: str, streitwert: Decimal) -> Decimal:
        """Compute a GGG fee from a table without date lookup."""
        data = table.data["posts"].get(post)
        if not isinstance(data, dict):
            raise FeeTableError(f"unknown GGG post {post!r} in table {table.table_id}")
        where = f"{table.table_id}.posts.{post}"
        basis = _ceil_euro(streitwert)
        derived = data.get("derived")
        if derived:
            source = str(derived.get("from", ""))
            source_data = table.data["posts"].get(source)
            if source == post or not isinstance(source_data, dict) or "derived" in source_data:
                raise FeeTableError(f"{where}: derived posts must point to a scale post")
            exempt_up_to = _opt_dec(data.get("exempt_up_to"), f"{where}.exempt_up_to")
            if exempt_up_to is not None and basis <= exempt_up_to:
                return _ZERO
            factor = _dec(derived.get("factor"), f"{where}.derived.factor")
            return _ceil_euro(self._ggg_amount(table, source, basis) * factor)
        amount = _band_amount(data, basis, where)
        if amount is not None:
            return amount
        above = data.get("above")
        if not above:
            raise FeeTableError(f"{where}: Streitwert {basis} is above the table range")
        start = _dec(above.get("from"), f"{where}.above.from")
        rate = _dec(above.get("rate"), f"{where}.above.rate")
        unit = above.get("rate_unit")
        if unit == "percent":
            rate /= Decimal(100)
        elif unit == "per_mille":
            rate /= Decimal(1000)
        else:
            raise FeeTableError(f"{where}.above.rate_unit: unknown unit {unit!r}")
        of = above.get("of")
        if of not in ("total", "excess"):
            raise FeeTableError(f"{where}.above.of: expected 'total' or 'excess'")
        portion = basis if of == "total" else basis - start
        return _ceil_euro(portion * rate) + _dec(above.get("plus"), f"{where}.above.plus")

    def ggg_fee(self, post: str, streitwert: Decimal, on: date) -> Decimal:
        """Compute a GGG Pauschalgebühr.

        Args:
            post: GGG post (``TP1`` to ``TP4``, ``TP4_II``, ``TP4_III``,
                ``TP1_ARB`` to ``TP4_ARB``).
            streitwert: Wert des Streitgegenstandes, Rechtsmittelinteresse or
                durchzusetzender Anspruch in EUR.
            on: Date on which the fee obligation arises (usually filing).

        Returns:
            The fee in EUR (full euros, expressed with two decimals).

        Raises:
            FeeTableError: For unknown posts, a negative value or a date
                without table.
        """
        value = _as_decimal(streitwert, "Streitwert")
        code = _normalise_post(post)
        if not _GGG_POST_RE.match(code):
            raise FeeTableError(f"invalid GGG post {post!r}; expected e.g. 'TP1' or 'TP4_II'")
        table = self._table("GGG", on)
        return self._ggg_amount(table, code, value).quantize(_CENT)

    # ── cost notes ──

    def _ratg_line(self, item: CostItemRequest) -> tuple[CostLine, Decimal]:
        """Compute one RATG line and return it with the USt rate of its table."""
        table = self._table("RATG", item.service_date)
        code, minutes = self._parse_ratg_post(item.post)
        data = self._ratg_section_one(table, code)
        base = self.ratg_fee(item.post, item.bemessungsgrundlage, item.service_date)
        einheitssatz = _ZERO
        if item.einheitssatz and data.get("einheitssatz_eligible", False):
            rate = self.einheitssatz_rate(item.bemessungsgrundlage, item.service_date)
            einheitssatz = _round_cents(base * rate * item.einheitssatz_factor)
        erv = _ZERO
        if item.erv != "none" and minutes is None and data.get("erv_eligible", False):
            erv = _dec(table.data["erv"].get(item.erv), f"{table.table_id}.erv.{item.erv}").quantize(_CENT)
        line = CostLine(
            description=item.description,
            kind=item.kind,
            post=self._canonical_ratg_post(item.post),
            service_date=item.service_date,
            base_amount=base,
            einheitssatz_amount=einheitssatz,
            erv_amount=erv,
            net_amount=base + einheitssatz + erv,
            table_id=table.table_id,
        )
        return line, _dec(table.data["ust_rate"], f"{table.table_id}.ust_rate")

    def cost_note(self, items: list[CostItemRequest]) -> CostNote:
        """Compute a complete cost note.

        Args:
            items: Requested items in display order.

        Returns:
            The cost note with one line per item and the totals.

        Raises:
            FeeTableError: If a post or date cannot be priced, or RATG lines
                come from tables with different USt rates.
        """
        lines: list[CostLine] = []
        ust_rates: set[Decimal] = set()
        fees_net = court_fees = barauslagen = _ZERO
        for item in items:
            if item.kind == "ratg":
                line, ust_rate = self._ratg_line(item)
                ust_rates.add(ust_rate)
                fees_net += line.net_amount
            elif item.kind == "ggg":
                fee = self.ggg_fee(item.post, item.bemessungsgrundlage, item.service_date)
                line = CostLine(
                    description=item.description,
                    kind=item.kind,
                    post=_normalise_post(item.post),
                    service_date=item.service_date,
                    base_amount=fee,
                    einheitssatz_amount=_ZERO,
                    erv_amount=_ZERO,
                    net_amount=fee,
                    table_id=self._table("GGG", item.service_date).table_id,
                )
                court_fees += fee
            else:
                amount = _round_cents(Decimal(item.amount or 0))
                line = CostLine(
                    description=item.description,
                    kind=item.kind,
                    post=item.post.strip(),
                    service_date=item.service_date,
                    base_amount=amount,
                    einheitssatz_amount=_ZERO,
                    erv_amount=_ZERO,
                    net_amount=amount,
                    table_id="",
                )
                barauslagen += amount
            lines.append(line)
        if len(ust_rates) > 1:
            raise FeeTableError(f"RATG lines use different USt rates: {sorted(ust_rates)}")
        ust_rate = ust_rates.pop() if ust_rates else DEFAULT_UST_RATE
        ust_amount = _round_cents(fees_net * ust_rate)
        return CostNote(
            lines=lines,
            fees_net=fees_net,
            ust_rate=ust_rate,
            ust_amount=ust_amount,
            court_fees=court_fees,
            barauslagen=barauslagen,
            total=fees_net + ust_amount + court_fees + barauslagen,
        )


__all__ = [
    "DEFAULT_TABLES_DIR",
    "DEFAULT_UST_RATE",
    "CostItemRequest",
    "CostLine",
    "CostNote",
    "FeeEngine",
    "FeeTableError",
]
