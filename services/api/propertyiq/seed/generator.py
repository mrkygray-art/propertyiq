"""Deterministic synthetic portfolio generator.

Same seed -> byte-identical dataset (checked by content_sha256). No real names, tenants,
investors or addresses. Produces 55 properties with 36 months (Oct 2023 - Sep 2026) of
monthly actuals and budgets, so every quarter end from 2025-09-30 has a full current and
prior trailing-12-month window.

The four required properties come from db/seeds/required_properties.json. The other 51
are generated with safe margins so the default rules never flag them at 2026-09-30:
occupancy >= 89%, NOI YoY >= -6%, actual NOI >= 95.5% of budget.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from propertyiq.finance import add_months

GENERATOR_VERSION = "propertyiq-seed-1"
DEFAULT_SEED = 20260930
REPORT_PERIOD = date(2026, 9, 30)
HISTORY_MONTHS = 36
SPEC_PATH = Path(__file__).resolve().parents[4] / "db" / "seeds" / "required_properties.json"

ASSET_COUNTS = {"office": 14, "retail": 14, "industrial": 14, "multifamily": 13}  # 55 total

PORTFOLIOS = [("FUND-I", "Synthetic Fund I"), ("FUND-II", "Synthetic Fund II")]
# Roles (see propertyiq/access.py): viewer, analyst, credit_officer, admin. The funds list is what each
# user can see; it's separate from the role.
DEMO_USERS = [
    ("analyst", "Demo Analyst (all funds)", "analyst", ["FUND-I", "FUND-II"]),
    ("restricted", "Demo Restricted Analyst (Fund II only)", "analyst", ["FUND-II"]),
    # Guest sign-in for showing the demo to someone else; full access (owner decision); PIQ_GUEST_PASSWORD.
    ("guest", "Guest (full access)", "credit_officer", ["FUND-I", "FUND-II"]),
    # Test admin: security checklist and network lockdown; its own password (PIQ_ADMIN_PASSWORD).
    ("admin", "Admin (test)", "admin", ["FUND-I", "FUND-II"]),
    # Read-only test account; PIQ_VIEWER_PASSWORD.
    ("viewer", "Demo Viewer (all funds, read only)", "viewer", ["FUND-I", "FUND-II"]),
    # Credit officer test accounts with different fund grants; all four share PIQ_CREDIT_PASSWORD.
    ("credit1", "Credit Officer 1 (all funds)", "credit_officer", ["FUND-I", "FUND-II"]),
    ("credit2", "Credit Officer 2 (Fund I only)", "credit_officer", ["FUND-I"]),
    ("credit3", "Credit Officer 3 (Fund II only)", "credit_officer", ["FUND-II"]),
    ("credit4", "Credit Officer 4 (all funds)", "credit_officer", ["FUND-I", "FUND-II"]),
]
# Which .env setting holds each user's password. Users not listed use PIQ_DEMO_PASSWORD.
USER_PASSWORD_ENV = {"guest": "PIQ_GUEST_PASSWORD", "admin": "PIQ_ADMIN_PASSWORD", "viewer": "PIQ_VIEWER_PASSWORD",
                     **{f"credit{i}": "PIQ_CREDIT_PASSWORD" for i in range(1, 5)}}
# Names for setting one shared password for several users at once (scripts/set_demo_password.py --user credit).
PASSWORD_GROUPS = {"credit": "PIQ_CREDIT_PASSWORD"}
MARKETS = ["Metro North", "Metro South", "Lakeshore", "Inland Valley", "Coastal Plain"]

PREFIXES = [
    "Alder Creek", "Ashford Glen", "Birchwood", "Bramblewood", "Cedar Point", "Copper Canyon",
    "Dovetail", "Driftwood", "Eastbrook", "Elm Ridge", "Fairhaven", "Foxhollow", "Glenmoor",
    "Granitefield", "Hollow Creek", "Hearthstone", "Ironwood", "Ivy Gate", "Juniper Hills",
    "Kestrel Ridge", "Kingsbury", "Larkspur", "Lantern Bay", "Maple Run", "Meadowlark",
    "Northgate", "Nettlefield", "Oakmont", "Orchard Row", "Pinecrest", "Prairie Wind",
    "Quarry Hill", "Quillmont", "Redfern", "Ridgecrest", "Stonebridge", "Sparrow Point",
    "Thornbury", "Timberline", "Upland Meadows", "Unity Grove", "Vale Crossing", "Verdant Mill",
    "Willowbrook", "Wrenfield", "Yarrowdale", "Zephyr Hill", "Amber Fields", "Bluestem",
    "Clearwater Bend", "Dunmore Ridge", "Evergreen Row", "Fernhill", "Goldleaf", "Highmeadow",
]
SUFFIXES = {
    "office": ["Office Center", "Corporate Park", "Tower", "Office Plaza", "Executive Center"],
    "retail": ["Retail Plaza", "Marketplace", "Shopping Center", "Town Center", "Retail Commons"],
    "industrial": ["Industrial Park", "Logistics Center", "Distribution Hub", "Business Park"],
    "multifamily": ["Apartments", "Residences", "Lofts", "Flats"],
}
# (min size, max size, size step, min annual NOI per size unit, max)
SIZING = {
    "office": (80_000, 400_000, 1_000, 14, 24),
    "retail": (60_000, 300_000, 1_000, 12, 20),
    "industrial": (150_000, 900_000, 5_000, 4, 8),
    "multifamily": (120, 450, 1, 7_000, 11_000),
}
EXPENSE_RATIO = {"office": 0.42, "retail": 0.33, "industrial": 0.22, "multifamily": 0.45}
# Monthly NOI weights, October first (fiscal months of a TTM ending September).
SEASONALITY = {
    "office": [100, 99, 101, 98, 97, 100, 100, 101, 100, 102, 101, 101],
    "retail": [98, 104, 118, 92, 90, 96, 97, 99, 98, 100, 101, 100],
    "industrial": [100, 100, 101, 99, 99, 100, 100, 100, 101, 100, 100, 100],
    "multifamily": [99, 98, 98, 97, 97, 99, 100, 101, 102, 103, 103, 102],
}


def cents(value: Decimal | float | str) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def dollars(c: int) -> Decimal:
    return (Decimal(c) / 100).quantize(Decimal("0.01"))


def split_cents(total: int, weights: list[int]) -> list[int]:
    """Split total cents across weights exactly (largest remainder, ties by position)."""
    if total < 0:
        return [-x for x in split_cents(-total, weights)]
    s = sum(weights)
    parts = [total * w // s for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % s), i))
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts


@dataclass
class Dataset:
    seed: int
    period: date
    months: list[date]
    portfolios: list[tuple[str, str]]
    properties: list[dict]
    financials: list[tuple]   # (property_id, month_end, revenue, expenses, occupied, rentable)
    budgets: list[tuple]      # (property_id, month_end, budget_revenue, budget_expenses)
    users: list[tuple]
    required: list[dict]

    def content_sha256(self) -> str:
        payload = json.dumps(
            {"properties": self.properties, "financials": self.financials, "budgets": self.budgets},
            default=str, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _occupancy_path(rng: random.Random, rentable: int, start: float, end_occupied: int,
                    floor: float, n: int) -> list[int]:
    end = end_occupied / rentable
    path = []
    for i in range(n - 1):
        pct = start + (end - start) * i / (n - 1) + rng.uniform(-0.004, 0.004)
        pct = min(max(pct, floor), 0.99)
        path.append(min(rentable, round(rentable * pct)))
    path.append(end_occupied)
    return path


def _smooth_trend(totals: list[int]) -> list[float]:
    """Monthly level that passes through each year's average at the year's midpoint, linear between
    midpoints and extrapolated at the ends. Used only as relative weights within a year."""
    anchors = [(y * 12 + 5.5, t / 12) for y, t in enumerate(totals)]
    out = []
    for i in range(12 * len(totals)):
        seg = 0 if i < anchors[1][0] else min(len(anchors) - 2, int((i - 5.5) // 12))
        (x0, y0), (x1, y1) = anchors[seg], anchors[seg + 1]
        out.append(max(1.0, y0 + (y1 - y0) * (i - x0) / (x1 - x0)))
    return out


def _monthly_rows(rng: random.Random, prop: dict, months: list[date], noi_by_year: list[int],
                  budget_by_year: list[int], occupied: list[int]) -> tuple[list[tuple], list[tuple]]:
    cls = prop["asset_class"]
    season = [w * (1000 + rng.randint(-25, 25)) for w in SEASONALITY[cls]]
    exp_ratio = EXPENSE_RATIO[cls] + rng.uniform(-0.03, 0.03)
    k = exp_ratio / (1 - exp_ratio)
    trend = _smooth_trend(noi_by_year)
    fin, bud = [], []
    for year in range(len(noi_by_year)):
        # Actuals follow a smooth monthly trend so there is no step at the fiscal-year boundary;
        # the split still hits each year's total to the cent. Budgets are set per year, so they keep
        # their annual step.
        weights = [max(1, round(trend[year * 12 + j] * season[j])) for j in range(12)]
        noi_m = split_cents(noi_by_year[year], weights)
        bud_m = split_cents(budget_by_year[year], season)
        for j in range(12):
            i = year * 12 + j
            exp = round(noi_m[j] * k * (1 + rng.uniform(-0.04, 0.04)))
            bexp = round(bud_m[j] * k)
            fin.append((prop["property_id"], months[i], dollars(noi_m[j] + exp), dollars(exp),
                        occupied[i], prop["rentable_size"]))
            bud.append((prop["property_id"], months[i], dollars(bud_m[j] + bexp), dollars(bexp)))
    return fin, bud


def generate(seed: int = DEFAULT_SEED, spec_path: Path = SPEC_PATH) -> Dataset:
    rng = random.Random(seed)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    period = date.fromisoformat(spec["report_period"])
    assert period == REPORT_PERIOD
    months = [add_months(period, i - (HISTORY_MONTHS - 1)) for i in range(HISTORY_MONTHS)]

    properties: list[dict] = []
    financials: list[tuple] = []
    budgets: list[tuple] = []

    # Required properties: back-solve prior years so computed YoY matches the seeded fact.
    for p in spec["properties"]:
        prop = {k: p[k] for k in ("property_id", "name", "asset_class", "portfolio_id", "market", "rentable_size")}
        prop["size_unit"] = "units" if p["asset_class"] == "multifamily" else "sf"
        a3 = cents(p["noi_actual_ttm"])
        a2 = cents(Decimal(a3) / 100 / (1 + Decimal(p["noi_yoy_pct"]) / 100))
        a1 = cents(Decimal(a2) / 100 / (1 + Decimal(p["prior_noi_yoy_pct"]) / 100))
        b3 = cents(p["noi_budget_ttm"])
        b2 = cents(Decimal(a2) / 100 / Decimal(p["prior_noi_to_budget"]))
        b1 = a1
        end_occupied = int(Decimal(p["rentable_size"]) * Decimal(p["occupancy_pct"]) / 100)
        occupied = _occupancy_path(rng, p["rentable_size"], float(Decimal(p["occupancy_start_pct"]) / 100),
                                   end_occupied, 0.5, HISTORY_MONTHS)
        f, b = _monthly_rows(rng, prop, months, [a1, a2, a3], [b1, b2, b3], occupied)
        properties.append(prop)
        financials += f
        budgets += b

    # The other 51 properties.
    prefixes = PREFIXES[:]
    rng.shuffle(prefixes)
    next_id = len(properties) + 1
    normal_index = 0
    for cls, count in ASSET_COUNTS.items():
        required_in_class = sum(1 for p in spec["properties"] if p["asset_class"] == cls)
        for _ in range(count - required_in_class):
            lo, hi, step, rate_lo, rate_hi = SIZING[cls]
            size = rng.randrange(lo, hi + 1, step)
            prop = {
                "property_id": f"PIQ-{next_id:03d}",
                "name": f"{prefixes.pop()} {rng.choice(SUFFIXES[cls])}",
                "asset_class": cls,
                "portfolio_id": "FUND-I" if normal_index % 2 == 0 else "FUND-II",
                "market": rng.choice(MARKETS),
                "rentable_size": size,
                "size_unit": "units" if cls == "multifamily" else "sf",
            }
            next_id += 1
            normal_index += 1

            b3 = cents(round(size * rng.uniform(rate_lo, rate_hi), -3))
            a3 = round(b3 * rng.uniform(0.955, 1.05))
            a2 = round(a3 / (1 + rng.uniform(-0.06, 0.07)))
            a1 = round(a2 / (1 + rng.uniform(-0.05, 0.07)))
            b2 = round(a2 / rng.uniform(0.955, 1.05))
            b1 = round(a1 / rng.uniform(0.955, 1.05))
            end_pct = rng.uniform(0.895, 0.975)
            end_occupied = round(size * end_pct)
            occupied = _occupancy_path(rng, size, rng.uniform(0.895, 0.975), end_occupied, 0.89, HISTORY_MONTHS)
            f, b = _monthly_rows(rng, prop, months, [a1, a2, a3], [b1, b2, b3], occupied)
            properties.append(prop)
            financials += f
            budgets += b

    return Dataset(
        seed=seed, period=period, months=months, portfolios=PORTFOLIOS, properties=properties,
        financials=financials, budgets=budgets, users=DEMO_USERS, required=spec["properties"],
    )
