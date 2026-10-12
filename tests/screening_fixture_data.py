"""Synthetic fixture data for Tier 2 intake tests. No real people or properties.

PROBLEM set: a 20-unit, 1962 wood-frame building in Los Angeles with planted problems:
  1. rent roll: 2 of 20 units vacant (10%); T-12 vacancy line is 3% of gross potential rent  -> A1
  2. one T-12 month (Mar-26) has an $18,000 insurance claim settlement                      -> A3
  3. management fee is $0                                                                    -> A5
  4. insurance $4,000/yr and repairs $6,000/yr for 20 units (below example benchmarks)      -> A4 x2
  5. property tax $21,500/yr on a $6,000,000 purchase                                         -> R1 (Milestone 7)
  6. built 1962, wood frame, tuck-under parking, Los Angeles                                  -> R2, R3 (Milestone 7)
  7. the Jun-26 column is missing from the T-12                                               -> A12
  8. the "Total Operating Expenses" Total column is $1,000 more than its line items           -> reconciliation
CLEAN set: same building, nothing wrong. It must produce no high-severity flags.

Tests compute expected values from these plain lists, independently of the module's code.
"""
from datetime import date

PROPERTY = "Juniper Court Apartments (synthetic fixture)"
MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
          "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
MONTH_LABELS = {m: date(int(m[:4]), int(m[5:]), 1).strftime("%b-%y") for m in MONTHS}
PROBLEM_MISSING_MONTH = "2026-06"

DEAL_FACTS = {"units": 20, "purchase_price": 6_000_000, "city": "Los Angeles", "year_built": 1962,
              "construction": "wood_frame", "stories": 2, "parking": "tuck_under", "transaction": "purchase"}

# Fake residents. Names and phone numbers exist only to prove they're stripped.
TENANTS = [f"{first} Placeholder" for first in (
    "Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Gray", "Harper", "Indigo", "Jules",
    "Kai", "Logan", "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sage", "Tatum")]
PHONES = [f"555-01{i:02d}" for i in range(20)]


def _unit(uid, utype, bb, sf, status, rent, market, start, end, conc=0, dep=None):
    return {"unit": uid, "type": utype, "bdba": bb, "sqft": sf, "status": status, "rent": rent, "market": market,
            "start": start, "end": end, "concession": conc, "deposit": dep}


def _rent_roll(vacant: set[str], rents_1br: list[int], rents_2br: list[int]) -> list[dict]:
    units, i1, i2 = [], 0, 0
    for n, uid in enumerate([f"{f}{u:02d}" for f in (1, 2) for u in range(1, 11)]):
        two_bed = uid[-2:] in ("07", "08", "09", "10")
        utype, bb, sf, market = ("2BR/1BA", "2/1", 850, 2600) if two_bed else ("1BR/1BA", "1/1", 650, 2100)
        if uid in vacant:
            units.append(_unit(uid, utype, bb, sf, "Vacant", None, market, None, None))
            continue
        rent = (rents_2br[i2] if two_bed else rents_1br[i1])
        i2, i1 = (i2 + 1, i1) if two_bed else (i2, i1 + 1)
        start = date(2025, 1 + n % 12, 1)
        end = None if n % 7 == 3 else date(2026 + (n % 3 == 0), 1 + (n * 5) % 12, 28)  # some month-to-month
        units.append(_unit(uid, utype, bb, sf, "Occupied", rent, market, start, end, dep=rent))
    return units


# 12 one-bed units (01-06 per floor) and 8 two-bed units (07-10 per floor).
PROBLEM_UNITS = _rent_roll({"105", "208"},
                           rents_1br=[1950, 2000, 2025, 1975, 2000, 2050, 1990, 2010, 2000, 1985, 2015],
                           rents_2br=[2450, 2500, 2525, 2480, 2510, 2495, 2540])
CLEAN_UNITS = _rent_roll({"110"},
                         rents_1br=[2050] * 12,
                         rents_2br=[2550, 2550, 2550, 2550, 2550, 2550, 2550])

# T-12 lines: (original label, section header it sits under, monthly amount or {month: amount})
EXPENSE_COMMON = [
    ("Water & Sewer", 1100), ("Electric - Common Area", 300), ("Gas", 250), ("Trash Removal", 280),
    ("Landscaping", 250), ("Pest control svc", 90), ("Fire extinguisher inspection", 40), ("On-site Payroll", 1500),
    ("Advertising", 100),
]

PROBLEM_T12 = {
    "INCOME": [("Gross Potential Rent", 46000), ("Vacancy Loss", -1380), ("Concessions", -200), ("Bad Debt", -150),
               ("Laundry Income", 400), ("Parking", 600), ("Insurance claim settlement", {"2026-03": 18000})],
    "OPERATING EXPENSES": [("RE Taxes", 1791.67), ("Insurance", 333.33), ("R&M", 500)] + EXPENSE_COMMON
                          + [("Management Fee", 0), ("Admin / Office", 200)],
    "BELOW THE LINE": [("Mortgage Payment", 18000), ("Capital Improvements", 0)],
}
CLEAN_T12 = {
    "INCOME": [("Gross Potential Rent", 46000), ("Vacancy Loss", -2300), ("Concessions", -100), ("Bad Debt", -100),
               ("Laundry Income", 400), ("Parking", 600), ("Other Income", 150)],
    "OPERATING EXPENSES": [("RE Taxes", 6000), ("Insurance", 1166.67), ("R&M", 1666.67),
                           ("Water & Sewer", 1100), ("Electric - Common Area", 300), ("Gas", 250),
                           ("Trash Removal", 280), ("Landscaping", 250), ("Pest control svc", 90),
                           ("Fire extinguisher inspection", 40), ("On-site Payroll", 2500),
                           ("Management Fee", 1786), ("Admin / Office", 300), ("Advertising", 150)],
    "BELOW THE LINE": [("Mortgage Payment", 18000), ("Capital Improvements", 2500)],
}
TOTAL_EXPENSES_PLANTED_ERROR = 1000  # problem set only: reported Total column overstated by $1,000


def monthly(amount, month):
    """Amount for one month: a constant, or a {month: amount} dict (0 for other months)."""
    if isinstance(amount, dict):
        return amount.get(month, 0)
    return amount


def months_for(problem: bool) -> list[str]:
    return [m for m in MONTHS if not (problem and m == PROBLEM_MISSING_MONTH)]
