"""Anomaly checks A1-A13: deterministic rules over approved rent roll and T-12 data.

Each flag is a suggestion for the reviewer, with the evidence numbers, where they came from, and
a suggested action. Nothing here changes the underwriting numbers; adjustments are proposed and
approved separately.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from propertyiq.screening.intake.calcs import RentRollMetrics, T12Metrics, line_median, utilities_total
from propertyiq.screening.intake.schema import CAPEX_WORDS, CATEGORY_SECTION, RentRollUnit, T12Line

ZERO = Decimal(0)
HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Tolerances:
    """Defaults from the Tier 2 spec where it gives one; others are conservative starting points."""
    vacancy_points: Decimal = Decimal("0.03")      # A1: 3 percentage points
    income_pct: Decimal = Decimal("0.05")          # A2: 5%
    spike_multiple: Decimal = Decimal(3)           # A3/A7: a month 3x the line's usual month
    spike_min: Decimal = Decimal(5000)             # ...and at least $5,000 above it
    trend_income_pct: Decimal = Decimal("0.05")    # A9
    trend_expense_pct: Decimal = Decimal("0.10")   # A8
    concession_pct: Decimal = Decimal("0.02")      # A10: of gross potential rent
    rent_outlier_pct: Decimal = Decimal("0.25")    # A11: 25% from the unit-type average


@dataclass
class Flag:
    rule_id: str
    key: str                       # distinguishes several flags of one rule (e.g. A4 insurance vs repairs)
    severity: str                  # high | medium | low
    title: str
    explanation: str
    evidence: list[dict] = field(default_factory=list)   # [{"label": ..., "value": ...}]
    sources: list[str] = field(default_factory=list)
    suggested_action: str = ""

    @property
    def fingerprint(self) -> str:
        return f"{self.rule_id}:{self.key}"


# Example benchmarks (same as the seeded table) so the checks can run in tests without a database.
EXAMPLE_BENCHMARKS = [
    {"property_type": "multifamily", "category": "insurance", "basis": "per_unit", "low": 450, "typical": 700, "high": 1200},
    {"property_type": "multifamily", "category": "repairs_maintenance", "basis": "per_unit", "low": 600, "typical": 1000, "high": 1800},
    {"property_type": "multifamily", "category": "utilities", "basis": "per_unit", "low": 700, "typical": 1300, "high": 2200},
    {"property_type": "multifamily", "category": "administrative", "basis": "per_unit", "low": 100, "typical": 250, "high": 500},
    {"property_type": "multifamily", "category": "management_fee", "basis": "pct_egi", "low": 0.03, "typical": 0.04, "high": 0.06},
]
for _b in EXAMPLE_BENCHMARKS:
    _b.update(source="PropertyIQ placeholder: example only, set your own", last_reviewed="2026-10-11")


def usd(v) -> str:
    return "n/a" if v is None else (f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}")


def pct(v, places=1) -> str:
    return "n/a" if v is None else f"{v * HUNDRED:.{places}f}%"


def _src(lines: list[T12Line]) -> list[str]:
    return [f"{ln.source.text()} ('{ln.original_label}')" for ln in lines]


def check(units: list[RentRollUnit] | None, rr: RentRollMetrics | None, rr_source: str | None,
          t12_lines: list[T12Line] | None, t12: T12Metrics | None, t12_info: dict | None,
          facts: dict, benchmarks: list[dict] | None = None, tol: Tolerances = Tolerances()) -> list[Flag]:
    """t12_info: {"missing_months": [...], "duplicate_months": [...], "reconciliation": [...], "source": "..."}"""
    flags: list[Flag] = []
    benchmarks = EXAMPLE_BENCHMARKS if benchmarks is None else benchmarks
    lines = t12_lines or []
    ptype = facts.get("property_type", "multifamily")
    n_units = facts.get("units") or (rr.unit_count if rr else None)
    sqft = Decimal(str(facts["sqft"])) if facts.get("sqft") else None

    def by_cat(*cats: str) -> list[T12Line]:
        return [ln for ln in lines if ln.category in cats]

    # A1 vacancy mismatch -----------------------------------------------------------
    if rr and t12 and rr.physical_occupancy is not None and t12.reported_vacancy_pct is not None:
        rr_vac = 1 - rr.physical_occupancy
        gap = rr_vac - t12.reported_vacancy_pct
        if abs(gap) > tol.vacancy_points:
            flags.append(Flag("A1", "vacancy", "high", "Vacancy doesn't match between the rent roll and the T-12",
                f"The rent roll shows {pct(rr_vac)} of units empty, but the T-12 vacancy line is "
                f"{pct(t12.reported_vacancy_pct)} of gross potential rent: a gap of {abs(gap) * HUNDRED:.1f} points "
                f"(tolerance {tol.vacancy_points * HUNDRED:.0f}).",
                [{"label": "Rent roll physical vacancy", "value": pct(rr_vac)},
                 {"label": "Units empty / total", "value": f"{rr.unit_count - rr.occupied_count} / {rr.unit_count}"},
                 {"label": "T-12 reported vacancy", "value": pct(t12.reported_vacancy_pct)}],
                [rr_source or "rent roll"] + _src(by_cat("vacancy_loss")),
                "Underwrite vacancy at no less than the current rent roll vacancy, and ask why the T-12 differs."))

    # A2 income vs rent roll ------------------------------------------------------------
    if rr and t12 and rr.in_place_annual > 0:
        net_rent = t12.annual_by_category.get("rental_income", ZERO) + t12.annual_by_category.get("vacancy_loss", ZERO)
        diff = (net_rent - rr.in_place_annual) / rr.in_place_annual
        if abs(diff) > tol.income_pct:
            flags.append(Flag("A2", "rent", "high" if abs(diff) > 2 * tol.income_pct else "medium",
                "T-12 rent doesn't match the rent roll",
                f"Rent on the T-12 (rental income less vacancy, annualized) is {usd(net_rent)}, but the rent roll's "
                f"in-place rent annualizes to {usd(rr.in_place_annual)}: {diff * HUNDRED:+.1f}% "
                f"(tolerance {tol.income_pct * HUNDRED:.0f}%).",
                [{"label": "T-12 rent less vacancy (annualized)", "value": usd(net_rent)},
                 {"label": "Rent roll in-place rent x 12", "value": usd(rr.in_place_annual)},
                 {"label": "Difference", "value": f"{diff * HUNDRED:+.1f}%"}],
                [rr_source or "rent roll"] + _src(by_cat("rental_income", "vacancy_loss")),
                "Underwrite rent from the current rent roll, not the T-12, unless the difference is explained."))

    # A3 one-time income ----------------------------------------------------------------
    for ln in by_cat("non_recurring_income"):
        if ln.total() != 0:
            months = [m for m, v in ln.months.items() if v]
            flags.append(Flag("A3", f"label:{ln.original_label}", "high", "One-time income in the T-12",
                f"'{ln.original_label}' looks like money that won't repeat. It adds {usd(ln.total())} "
                f"({', '.join(months)}).",
                [{"label": ln.original_label, "value": usd(ln.total())},
                 {"label": "Month(s)", "value": ", ".join(months)}], _src([ln]),
                "Remove it from underwritten income (reason ONE_TIME_ITEM_REMOVED)."))
    for ln in lines:
        if CATEGORY_SECTION.get(ln.category) != "income" or ln.category in ("non_recurring_income", "vacancy_loss",
                                                                             "concessions", "bad_debt"):
            continue
        base = line_median(ln)
        for m, v in ln.months.items():
            if v is not None and v - base > tol.spike_min and (base <= 0 or v > base * tol.spike_multiple):
                flags.append(Flag("A3", f"spike:{ln.original_label}:{m}", "high", "Income spike in one month",
                    f"'{ln.original_label}' is {usd(v)} in {m} versus a usual {usd(base)}. The extra {usd(v - base)} "
                    "may be a one-time item.",
                    [{"label": f"{ln.original_label}, {m}", "value": usd(v)}, {"label": "Usual month (median)", "value": usd(base)},
                     {"label": "Excess", "value": usd(v - base)}], _src([ln]),
                    "Ask what the spike was; remove it from underwritten income if it won't repeat."))

    # A4 expense below benchmark, A5 management fee -------------------------------------
    egi = t12.recurring_egi if t12 else None
    for b in [b for b in benchmarks if b["property_type"] == ptype] if t12 else []:
        cat, basis = b["category"], b["basis"]
        low, typical = (Decimal(str(b["low"])) if b.get("low") is not None else None,
                        Decimal(str(b["typical"])) if b.get("typical") is not None else None)
        amount = utilities_total(t12.annual_by_category) if cat == "utilities" else t12.annual_by_category.get(cat, ZERO)
        divisor = {"per_unit": Decimal(n_units) if n_units else None, "per_sf": sqft, "pct_egi": egi}[basis]
        if not divisor or divisor <= 0 or low is None:
            continue
        actual = amount / divisor
        shown = (lambda v: pct(v)) if basis == "pct_egi" else (lambda v: f"{usd(v)} per {'unit' if basis == 'per_unit' else 'sq ft'}")
        bench_src = f"Benchmark: {b.get('source')} (reviewed {b.get('last_reviewed')})"
        if cat == "management_fee":
            if amount == 0 or actual < low:
                flags.append(Flag("A5", "management_fee", "medium",
                    "No management fee" if amount == 0 else "Management fee below benchmark",
                    ("The T-12 shows no management fee. " if amount == 0 else
                     f"The management fee is {shown(actual)} of income, below the {shown(low)} low end. ")
                    + "That's common when the owner manages the building; a new owner or a lender taking it back "
                      "would pay a manager.",
                    [{"label": "Management fee (annualized)", "value": usd(amount)},
                     {"label": "As % of recurring income", "value": shown(actual)},
                     {"label": "Benchmark low / typical", "value": f"{shown(low)} / {shown(typical)}"}],
                    _src(by_cat("management_fee")) + [bench_src],
                    f"Underwrite a market management fee (for example {shown(typical)} of income; reason MANAGEMENT_FEE)."))
            continue
        if amount == 0:
            continue  # missing categories are A6
        if actual < low:
            cat_lines = by_cat(*(("utilities_water_sewer", "utilities_electric", "utilities_gas", "utilities_trash")
                                 if cat == "utilities" else (cat,)))
            name = {"repairs_maintenance": "repairs and maintenance", "administrative": "administrative costs"}.get(
                cat, cat.replace("_", " "))
            flags.append(Flag("A4", cat, "medium", f"{name.capitalize()} below benchmark",
                f"{name.capitalize()} is {shown(actual)} ({usd(amount)} a year), below the low end of {shown(low)}. "
                "Low expenses make NOI look better than a new owner may achieve.",
                [{"label": f"{name.capitalize()} (annualized)", "value": usd(amount)},
                 {"label": "Per unit" if basis == "per_unit" else "Per sq ft" if basis == "per_sf" else "Of income", "value": shown(actual)},
                 {"label": "Benchmark low / typical", "value": f"{shown(low)} / {shown(typical)}"}],
                _src(cat_lines) + [bench_src],
                f"Consider underwriting at the typical {shown(typical)} (reason EXPENSE_BELOW_BENCHMARK)."))

    # A6 missing expense category --------------------------------------------------------
    if t12:
        for cat, name in (("real_estate_taxes", "Property taxes"), ("insurance", "Insurance"),
                          ("repairs_maintenance", "Repairs and maintenance"), ("utilities", "Utilities")):
            amount = utilities_total(t12.annual_by_category) if cat == "utilities" else t12.annual_by_category.get(cat, ZERO)
            if amount == 0:
                flags.append(Flag("A6", cat, "high", f"{name} missing from the T-12",
                    f"{name} is absent or $0. Every property has this cost, so the T-12 overstates NOI.",
                    [{"label": name, "value": usd(amount)}], [t12_info.get("source", "T-12") if t12_info else "T-12"],
                    f"Get the actual {name.lower()} figure and underwrite it."))

    # A7 capex hiding in repairs -----------------------------------------------------------
    for ln in by_cat("repairs_maintenance", "other_expense", "contract_services"):
        label = ln.original_label.lower()
        if any(w in label for w in CAPEX_WORDS) and ln.total() > 0:
            flags.append(Flag("A7", f"label:{ln.original_label}", "medium", "Possible capital item in repairs",
                f"'{ln.original_label}' ({usd(ln.total())}) sounds like a capital replacement, not a routine repair.",
                [{"label": ln.original_label, "value": usd(ln.total())}], _src([ln]),
                "Confirm what it was. Capital items belong below NOI; if it recurs, consider a reserve."))
    for ln in by_cat("repairs_maintenance"):
        base = line_median(ln)
        for m, v in ln.months.items():
            if v is not None and v - base > tol.spike_min and v > base * tol.spike_multiple:
                flags.append(Flag("A7", f"spike:{ln.original_label}:{m}", "medium", "Large one-off repair charge",
                    f"'{ln.original_label}' is {usd(v)} in {m} versus a usual {usd(base)}. Large one-off repairs are often "
                    "capital items such as a roof or HVAC.",
                    [{"label": f"{ln.original_label}, {m}", "value": usd(v)}, {"label": "Usual month (median)", "value": usd(base)}],
                    _src([ln]), "Ask what the charge was; it may need to move to capex and be covered by reserves."))

    # A8 / A9 trends ---------------------------------------------------------------------------
    if t12 and t12.months_present >= 6:
        t12_income = sum(t12.monthly_income.values(), ZERO) * t12.annualize
        t12_exp = sum(t12.monthly_expenses.values(), ZERO) * t12.annualize
        for rule, name, base, t3, t6, limit in (
                ("A9", "income", t12_income, t12.t3_income, t12.t6_income, tol.trend_income_pct),
                ("A8", "expenses", t12_exp, t12.t3_expenses, t12.t6_expenses, tol.trend_expense_pct)):
            if not base:
                continue
            for label, recent in (("T-3", t3), ("T-6", t6)):
                if recent is None:
                    continue
                change = (recent - base) / base
                if abs(change) > limit:
                    direction = "higher" if change > 0 else "lower"
                    flags.append(Flag(rule, f"{label}", "medium", f"Recent {name} {direction} than the T-12",
                        f"{label} annualized {name} is {usd(recent)}, {abs(change) * HUNDRED:.1f}% {direction} than the "
                        f"T-12's {usd(base)} (tolerance {limit * HUNDRED:.0f}%).",
                        [{"label": f"{label} annualized {name}", "value": usd(recent)},
                         {"label": f"T-12 annualized {name}", "value": usd(base)}],
                        [t12_info.get("source", "T-12") if t12_info else "T-12"],
                        "Find out why recent months differ before relying on the full-year figure."))

    # A10 concessions and bad debt --------------------------------------------------------------
    if t12 and t12.gross_potential_rent:
        for cat, name in (("concessions", "Concessions"), ("bad_debt", "Bad debt")):
            amount = abs(t12.annual_by_category.get(cat, ZERO))
            share = amount / t12.gross_potential_rent
            ls = by_cat(cat)
            series = [abs(sum(((ln.months.get(m) or ZERO) for ln in ls), ZERO)) for m in t12.months]
            rising = (len(series) >= 6 and sum(series[-3:]) / 3 > Decimal("1.5") * (sum(series[:-3]) / (len(series) - 3))
                      and sum(series[-3:]) / 3 > 500)
            if share > tol.concession_pct or rising:
                flags.append(Flag("A10", cat, "medium", f"{name} {'rising' if rising else 'unusually large'}",
                    f"{name} total {usd(amount)} a year, {pct(share)} of gross potential rent"
                    + (", and the last 3 months run well above the earlier average." if rising else "."),
                    [{"label": f"{name} (annualized)", "value": usd(amount)}, {"label": "Of gross potential rent", "value": pct(share)}],
                    _src(ls), "Underwrite the recent level and ask whether the property is struggling to lease or collect."))

    # A11 rent outliers, A13 occupied without rent -----------------------------------------------
    if units:
        for t in {u.unit_type for u in units}:
            occ = [u for u in units if u.unit_type == t and u.status == "occupied" and u.current_rent]
            if len(occ) < 3:
                continue
            avg = sum((u.current_rent for u in occ), ZERO) / len(occ)
            for u in occ:
                off = (u.current_rent - avg) / avg
                if abs(off) > tol.rent_outlier_pct:
                    flags.append(Flag("A11", f"unit:{u.unit_id}", "low", f"Unit {u.unit_id} rent is unusual",
                        f"Unit {u.unit_id} pays {usd(u.current_rent)}, {off * HUNDRED:+.0f}% versus the {t} average of {usd(avg)}.",
                        [{"label": f"Unit {u.unit_id} rent", "value": usd(u.current_rent)}, {"label": f"{t} average", "value": usd(avg)}],
                        [u.source.text()], "Check the lease; it may be a data entry error, a special deal, or a related party."))
        for u in units:
            if u.status == "occupied" and not u.current_rent:
                flags.append(Flag("A13", f"unit:{u.unit_id}", "medium", f"Unit {u.unit_id} occupied with no rent",
                    f"Unit {u.unit_id} is marked occupied but shows {usd(u.current_rent or ZERO)} rent. It may be an employee "
                    "or model unit, or an error.",
                    [{"label": f"Unit {u.unit_id} rent", "value": usd(u.current_rent or ZERO)}], [u.source.text()],
                    "Confirm the unit's status and rent."))

    # A12 data integrity --------------------------------------------------------------------------
    evidence, srcs = [], []
    if units:
        ids = [u.unit_id for u in units]
        dups = sorted({i for i in ids if ids.count(i) > 1})
        if dups:
            evidence.append({"label": "Duplicate unit numbers", "value": ", ".join(dups)})
        if facts.get("units") and len(units) != int(facts["units"]):
            evidence.append({"label": "Units on rent roll vs deal form", "value": f"{len(units)} vs {facts['units']}"})
        srcs.append(rr_source or "rent roll")
    if t12_info:
        if t12_info.get("missing_months"):
            evidence.append({"label": "Missing T-12 months", "value": ", ".join(t12_info["missing_months"])})
        if t12_info.get("duplicate_months"):
            evidence.append({"label": "Duplicated T-12 months", "value": ", ".join(t12_info["duplicate_months"])})
        for r in t12_info.get("reconciliation", []):
            if r["kind"] != "subtotal_month":
                evidence.append({"label": f"Doesn't add up: {r['label']}", "value": r["detail"]})
        srcs.append(t12_info.get("source", "T-12"))
    if t12_info and t12_info.get("rent_roll_totals_mismatch"):
        evidence.append({"label": "Rent roll totals row", "value": t12_info["rent_roll_totals_mismatch"]})
    if evidence:
        flags.append(Flag("A12", "integrity", "high", "Data integrity problems",
            "The documents have gaps or don't add up, so totals built from them may be wrong.",
            evidence, srcs, "Ask for corrected documents, or adjust and note each fix."))
    return flags


def rent_roll_totals_check(units: list[RentRollUnit], reported: dict[str, Decimal]) -> str | None:
    """Compare a rent roll's own Totals row to the sum of its units."""
    problems = []
    for f, label in (("current_rent", "rent"), ("market_rent", "market rent"), ("sqft", "square feet")):
        if f in reported:
            calc = sum((getattr(u, f) or ZERO for u in units), ZERO)
            if abs(calc - reported[f]) > 1:
                problems.append(f"{label}: units add to {calc:,.0f} but the Totals row shows {reported[f]:,.0f}")
    return "; ".join(problems) or None


def plain(flag: Flag) -> str:
    return re.sub(r"\s+", " ", f"{flag.rule_id} {flag.title}: {flag.explanation}")
