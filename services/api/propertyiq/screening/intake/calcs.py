"""Rent roll and T-12 calculations. Plain code on Decimal values; the AI never touches these.

Plain-language terms:
  Physical occupancy   units with someone living in them / total units.
  In-place rent        what current tenants actually pay.
  Gross potential rent in-place rent on occupied units + market rent on vacant units: the rent if
                       every unit were leased, at today's rents.
  Loss to lease        market rent minus in-place rent on occupied units: how far below market the
                       current tenants are paying.
  Annualized           a partial-period figure scaled to 12 months (e.g. 3 months x 4).
  T-3 / T-6            the last 3 or 6 months of the T-12, annualized, to show recent direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from statistics import median

from propertyiq.screening.intake.schema import (
    CATEGORY_SECTION, PHYSICALLY_OCCUPIED, UTILITY_CATEGORIES, RentRollUnit, T12Line,
)

ZERO = Decimal(0)
TWELVE = Decimal(12)


def _add_months(d: date, n: int) -> date:
    idx = d.year * 12 + d.month - 1 + n
    y, m = divmod(idx, 12)
    return date(y, m + 1, min(d.day, 28))


# ---------------------------------------------------------------- rent roll

@dataclass
class TypeSummary:
    unit_type: str
    units: int
    occupied: int
    avg_rent: Decimal | None        # occupied units only
    avg_market: Decimal | None
    avg_sqft: Decimal | None
    rent_per_sqft: Decimal | None   # monthly, occupied units with both rent and size


@dataclass
class RentRollMetrics:
    unit_count: int
    occupied_count: int
    vacant_count: int
    non_revenue_count: int          # model or down units
    physical_occupancy: Decimal | None
    in_place_monthly: Decimal
    in_place_annual: Decimal
    gross_potential_monthly: Decimal
    gross_potential_annual: Decimal
    loss_to_lease_monthly: Decimal
    loss_to_lease_annual: Decimal
    by_type: list[TypeSummary]
    expirations: dict[str, int]     # expired, 0-3, 3-6, 6-12, 12+, month_to_month, no_lease_date
    warnings: list[str] = field(default_factory=list)


def rent_roll_metrics(units: list[RentRollUnit], as_of: date) -> RentRollMetrics:
    warnings: list[str] = []
    occupied = [u for u in units if u.status in PHYSICALLY_OCCUPIED]
    empty = [u for u in units if u.status in ("vacant", "model", "down")]
    unknown = [u for u in units if u.status is None]
    if unknown:
        warnings.append(f"{len(unknown)} unit(s) have no recognized status and are left out of occupancy.")

    in_place = sum((u.current_rent or ZERO for u in occupied), ZERO)
    missing_market = [u.unit_id for u in empty if u.market_rent is None]
    if missing_market:
        warnings.append(f"No market rent for empty unit(s) {', '.join(missing_market)}; they add $0 to gross potential rent.")
    gpr = in_place + sum((u.market_rent or ZERO for u in empty), ZERO)
    ltl = sum((u.market_rent - u.current_rent for u in occupied
               if u.market_rent is not None and u.current_rent is not None), ZERO)

    by_type = []
    for t in sorted({u.unit_type or "(no type)" for u in units}):
        group = [u for u in units if (u.unit_type or "(no type)") == t]
        occ = [u for u in group if u.status in PHYSICALLY_OCCUPIED and u.current_rent is not None]
        mk = [u.market_rent for u in group if u.market_rent is not None]
        sf = [u.sqft for u in group if u.sqft]
        per_sf = [u.current_rent / u.sqft for u in occ if u.sqft]
        by_type.append(TypeSummary(
            t, len(group), len([u for u in group if u.status in PHYSICALLY_OCCUPIED]),
            sum((u.current_rent for u in occ), ZERO) / len(occ) if occ else None,
            sum(mk, ZERO) / len(mk) if mk else None,
            sum(sf, ZERO) / len(sf) if sf else None,
            sum(per_sf, ZERO) / len(per_sf) if per_sf else None,
        ))

    exp = {"expired": 0, "0-3 months": 0, "3-6 months": 0, "6-12 months": 0, "12+ months": 0,
           "month_to_month": 0, "no_lease_date": 0}
    for u in occupied:
        if u.month_to_month:
            exp["month_to_month"] += 1
        elif u.lease_end is None:
            exp["no_lease_date"] += 1
        elif u.lease_end < as_of:
            exp["expired"] += 1
        elif u.lease_end <= _add_months(as_of, 3):
            exp["0-3 months"] += 1
        elif u.lease_end <= _add_months(as_of, 6):
            exp["3-6 months"] += 1
        elif u.lease_end <= _add_months(as_of, 12):
            exp["6-12 months"] += 1
        else:
            exp["12+ months"] += 1

    n = len(units)
    known = n - len(unknown)
    return RentRollMetrics(
        unit_count=n, occupied_count=len(occupied), vacant_count=len([u for u in units if u.status == "vacant"]),
        non_revenue_count=len([u for u in units if u.status in ("model", "down")]),
        physical_occupancy=Decimal(len(occupied)) / Decimal(known) if known else None,
        in_place_monthly=in_place, in_place_annual=in_place * TWELVE,
        gross_potential_monthly=gpr, gross_potential_annual=gpr * TWELVE,
        loss_to_lease_monthly=ltl, loss_to_lease_annual=ltl * TWELVE,
        by_type=by_type, expirations=exp, warnings=warnings,
    )


# ---------------------------------------------------------------- T-12

@dataclass
class T12Metrics:
    months: list[str]                       # months present, in order
    months_present: int
    annualize: Decimal                      # 12 / months present
    by_category: dict[str, Decimal]         # totals over the months present
    annual_by_category: dict[str, Decimal]  # annualized
    gross_potential_rent: Decimal           # annualized rental_income line
    effective_gross_income: Decimal         # annualized, all income lines including one-time items
    recurring_egi: Decimal                  # annualized, excluding non-recurring income
    non_recurring_income: Decimal           # total (not annualized): one-time money doesn't repeat
    operating_expenses: Decimal             # annualized
    noi: Decimal                            # recurring EGI - operating expenses, annualized
    reported_vacancy_pct: Decimal | None    # |vacancy & loss to lease| / gross potential rent
    expense_ratio: Decimal | None           # operating expenses / recurring EGI
    expense_per_unit: Decimal | None
    expense_per_sf: Decimal | None
    monthly_income: dict[str, Decimal]      # recurring income per month
    monthly_expenses: dict[str, Decimal]
    t3_income: Decimal | None               # last 3 months x 4
    t6_income: Decimal | None               # last 6 months x 2
    t3_expenses: Decimal | None
    t6_expenses: Decimal | None


def _section(ln: T12Line) -> str:
    return CATEGORY_SECTION.get(ln.category, "unknown")


def t12_metrics(lines: list[T12Line], months: list[str], units: int | None = None,
                sqft: Decimal | None = None) -> T12Metrics:
    months = sorted(months)
    n = len(months)
    factor = TWELVE / n if n else ZERO
    by_cat: dict[str, Decimal] = {}
    for ln in lines:
        by_cat[ln.category] = by_cat.get(ln.category, ZERO) + ln.total()
    annual = {k: v * factor for k, v in by_cat.items()}

    monthly_income = {m: sum(((ln.months.get(m) or ZERO) for ln in lines
                              if _section(ln) == "income" and ln.category != "non_recurring_income"), ZERO)
                      for m in months}
    monthly_exp = {m: sum(((ln.months.get(m) or ZERO) for ln in lines if _section(ln) == "expense"), ZERO)
                   for m in months}
    egi_all = sum((v for k, v in annual.items() if CATEGORY_SECTION.get(k) == "income"), ZERO)
    non_rec = by_cat.get("non_recurring_income", ZERO)
    recurring_egi = egi_all - annual.get("non_recurring_income", ZERO)
    opex = sum((v for k, v in annual.items() if CATEGORY_SECTION.get(k) == "expense"), ZERO)
    gpr = annual.get("rental_income", ZERO)

    def trailing(series: dict[str, Decimal], k: int) -> Decimal | None:
        if n < k:
            return None
        return sum((series[m] for m in months[-k:]), ZERO) * (TWELVE / k)

    return T12Metrics(
        months=months, months_present=n, annualize=factor, by_category=by_cat, annual_by_category=annual,
        gross_potential_rent=gpr, effective_gross_income=egi_all, recurring_egi=recurring_egi,
        non_recurring_income=non_rec, operating_expenses=opex, noi=recurring_egi - opex,
        reported_vacancy_pct=abs(annual.get("vacancy_loss", ZERO)) / gpr if gpr else None,
        expense_ratio=opex / recurring_egi if recurring_egi > 0 else None,
        expense_per_unit=opex / units if units else None,
        expense_per_sf=opex / sqft if sqft else None,
        monthly_income=monthly_income, monthly_expenses=monthly_exp,
        t3_income=trailing(monthly_income, 3), t6_income=trailing(monthly_income, 6),
        t3_expenses=trailing(monthly_exp, 3), t6_expenses=trailing(monthly_exp, 6),
    )


def utilities_total(annual_by_category: dict[str, Decimal]) -> Decimal:
    return sum((annual_by_category.get(k, ZERO) for k in UTILITY_CATEGORIES), ZERO)


def line_median(ln: T12Line) -> Decimal:
    vals = [v for v in ln.months.values() if v is not None]
    return Decimal(str(median(vals))) if vals else ZERO
