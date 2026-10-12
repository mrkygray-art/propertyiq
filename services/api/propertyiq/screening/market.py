"""Market and comp support: a small provider interface, a synthetic offline provider, and caching.

Comps support the lender's judgment; they are not an appraisal. Every result carries the provider,
retrieval date, search area, comp count and each comp's source ID. If a provider has nothing for an
area, the answer is "No comps available", never an estimate. Cap rates are shown only when the
source reports one.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Protocol

from propertyiq import db
from propertyiq.screening.store import _writer

ZERO = Decimal(0)


@dataclass
class CompQuery:
    kind: str                  # "rent" | "sale"
    address: str
    city: str
    radius_miles: float = 1.0
    max_age_months: int = 12
    unit_types: list[str] = field(default_factory=list)   # rent comps: e.g. ["1BR/1BA", "2BR/1BA"]
    property_type: str = "multifamily"

    def key(self) -> str:
        norm = json.dumps({**asdict(self), "address": self.address.strip().lower(), "city": self.city.strip().lower(),
                           "unit_types": sorted(self.unit_types)}, sort_keys=True)
        return hashlib.sha256(norm.encode()).hexdigest()[:32]


@dataclass
class CompResult:
    provider: str
    provider_label: str
    synthetic: bool
    retrieved_at: str
    query: dict
    area: str
    comps: list[dict]
    notes: list[str] = field(default_factory=list)


class MarketProvider(Protocol):
    name: str
    label: str
    synthetic: bool

    def configured(self) -> bool: ...
    def fetch(self, q: CompQuery) -> CompResult: ...


# ---------------------------------------------------------------- synthetic provider (offline)

# Fictional market levels for a handful of Southern California cities. Not real data.
SYNTHETIC_MARKETS = {
    "los angeles":   {"rent": {0: 1750, 1: 2150, 2: 2750, 3: 3500}, "price_per_unit": 310_000, "cap": 0.052},
    "santa monica":  {"rent": {0: 2300, 1: 2900, 2: 3800, 3: 4900}, "price_per_unit": 520_000, "cap": 0.045},
    "long beach":    {"rent": {0: 1550, 1: 1900, 2: 2450, 3: 3100}, "price_per_unit": 265_000, "cap": 0.056},
    "torrance":      {"rent": {0: 1650, 1: 2050, 2: 2600, 3: 3250}, "price_per_unit": 290_000, "cap": 0.053},
    "pasadena":      {"rent": {0: 1800, 1: 2250, 2: 2900, 3: 3600}, "price_per_unit": 340_000, "cap": 0.050},
}
STREETS = ["Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Ginkgo", "Hawthorn", "Juniper", "Laurel", "Magnolia",
           "Oak", "Pine", "Sycamore", "Walnut", "Willow"]


def _beds(unit_type: str) -> int:
    t = unit_type.lower()
    if "studio" in t or t.startswith("0"):
        return 0
    for ch in t:
        if ch.isdigit():
            return int(ch)
    return 1


class SyntheticProvider:
    name = "synthetic"
    label = "PropertyIQ synthetic comps (demo data, not real listings)"
    synthetic = True

    def configured(self) -> bool:
        return True

    def fetch(self, q: CompQuery) -> CompResult:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        area = f"{q.radius_miles:g} mile radius of {q.address}, {q.city}; last {q.max_age_months} months"
        market = SYNTHETIC_MARKETS.get(q.city.strip().lower())
        if market is None:
            return CompResult(self.name, self.label, True, now, asdict(q), area, [],
                              [f"The synthetic provider has no data for {q.city}. No comps available."])
        rng = random.Random(q.key())
        comps = []
        if q.kind == "rent":
            types = q.unit_types or ["1BR/1BA", "2BR/1BA"]
            for t in types:
                base = market["rent"].get(_beds(t), market["rent"][1])
                for i in range(rng.randint(4, 7)):
                    dist = round(rng.uniform(0.1, q.radius_miles), 2)
                    sqft = int(rng.uniform(560, 720) + 220 * max(0, _beds(t) - 1))
                    rent = int(round(base * rng.uniform(0.9, 1.12), -1))
                    comps.append({
                        "source_id": f"SYN-R-{rng.randrange(10_000, 99_999)}", "address": f"{rng.randrange(100, 9999)} "
                        f"{rng.choice(STREETS)} {rng.choice(['St', 'Ave', 'Blvd', 'Dr'])}, {q.city}", "unit_type": t,
                        "beds": _beds(t), "baths": 1 if _beds(t) < 3 else 2, "sqft": sqft, "rent": rent,
                        "rent_per_sqft": round(rent / sqft, 2), "distance_miles": dist,
                        "listed": (date.today() - timedelta(days=rng.randint(5, 30 * q.max_age_months))).isoformat(),
                        "url": None,
                    })
        else:
            for i in range(rng.randint(5, 8)):
                units = rng.randint(8, 40)
                ppu = int(round(market["price_per_unit"] * rng.uniform(0.82, 1.18), -3))
                bsf = units * rng.randint(700, 900)
                comps.append({
                    "source_id": f"SYN-S-{rng.randrange(10_000, 99_999)}", "address": f"{rng.randrange(100, 9999)} "
                    f"{rng.choice(STREETS)} {rng.choice(['St', 'Ave', 'Blvd', 'Dr'])}, {q.city}",
                    "sale_date": (date.today() - timedelta(days=rng.randint(20, 30 * q.max_age_months))).isoformat(),
                    "price": ppu * units, "units": units, "price_per_unit": ppu, "building_sqft": bsf,
                    "price_per_sqft": round(ppu * units / bsf, 2), "year_built": rng.randint(1955, 2015),
                    # Synthetic "source" reports a cap rate for only some sales, like real public data.
                    "cap_rate": round(market["cap"] * rng.uniform(0.85, 1.15), 4) if rng.random() < 0.6 else None,
                    "distance_miles": round(rng.uniform(0.2, max(q.radius_miles, 0.5)), 2), "url": None,
                })
        return CompResult(self.name, self.label, True, now, asdict(q), area, comps,
                          ["Synthetic demo comps: fictional addresses and numbers generated offline."])


class RentcastProvider:
    """Placeholder for a real provider. Off unless an API key is configured; would be the only part of
    PropertyIQ that calls the internet, sending only the address and search radius (never documents
    or tenant data). Not implemented in this prototype."""
    name = "rentcast"
    label = "Rentcast (not configured)"
    synthetic = False

    def configured(self) -> bool:
        return bool(os.environ.get("PIQ_RENTCAST_API_KEY"))

    def fetch(self, q: CompQuery) -> CompResult:
        raise NotImplementedError("The Rentcast provider isn't implemented in this offline prototype.")


PROVIDERS: dict[str, MarketProvider] = {p.name: p for p in (SyntheticProvider(), RentcastProvider())}


def providers() -> list[dict]:
    return [{"name": p.name, "label": p.label, "synthetic": p.synthetic, "configured": p.configured()}
            for p in PROVIDERS.values()]


# ---------------------------------------------------------------- cache

def get_comps(q: CompQuery, provider_name: str = "synthetic", refresh: bool = False, stale_days: int = 30) -> dict:
    provider = PROVIDERS.get(provider_name)
    if provider is None or not provider.configured():
        return {"error": f"Market provider '{provider_name}' isn't available.", "comps": []}
    key = q.key()
    row = None
    if not refresh:
        with db.connection() as conn:
            row = conn.execute("SELECT results, retrieved_at FROM screening_market_cache WHERE provider = %s AND "
                               "query_key = %s", (provider_name, key)).fetchone()
    if row is None:
        result = asdict(provider.fetch(q))
        with _writer() as conn:
            conn.execute("INSERT INTO screening_market_cache (provider, query_key, query, results, retrieved_at) "
                         "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (provider, query_key) DO UPDATE SET "
                         "results = EXCLUDED.results, retrieved_at = now()",
                         (provider_name, key, json.dumps(asdict(q)), json.dumps(result)))
        retrieved = datetime.now(timezone.utc)
    else:
        result, retrieved = row["results"], row["retrieved_at"]
    age = (datetime.now(timezone.utc) - retrieved).days
    result.update(cached=row is not None, retrieved_at=retrieved.isoformat(timespec="seconds"), age_days=age,
                  stale=age > stale_days, stale_days=stale_days)
    return result


# ---------------------------------------------------------------- summaries and comparisons

def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "median": None, "low": None, "high": None}
    return {"n": len(values), "median": median(values), "low": min(values), "high": max(values)}


def rent_summary(comps: list[dict], subject_by_type: dict[str, dict]) -> list[dict]:
    """Per unit type: comp median/low/high and the subject's in-place and market rent versus the median."""
    out = []
    for t in sorted({c["unit_type"] for c in comps} | set(subject_by_type)):
        s = _stats([c["rent"] for c in comps if c["unit_type"] == t])
        subj = subject_by_type.get(t, {})
        row = {"unit_type": t, **s, "subject_in_place": subj.get("avg_rent"), "subject_market": subj.get("avg_market")}
        for k in ("subject_in_place", "subject_market"):
            v = row[k]
            row[f"{k}_vs_median_pct"] = (float(v) - s["median"]) / s["median"] if v is not None and s["median"] else None
        out.append(row)
    return out


def sale_summary(comps: list[dict]) -> dict:
    caps = [c["cap_rate"] for c in comps if c.get("cap_rate") is not None]
    dates = sorted(c["sale_date"] for c in comps)
    return {
        "n": len(comps),
        "price_per_unit": _stats([c["price_per_unit"] for c in comps]),
        "price_per_sqft": _stats([c["price_per_sqft"] for c in comps]),
        "cap_rate": {**_stats(caps), "note": f"reported by source for {len(caps)} of {len(comps)} sales"
                     if comps else "not available"},
        "date_range": [dates[0], dates[-1]] if dates else None,
    }


def m1_flags(summary: dict, price: Decimal | None, units: int | None, noi: Decimal | None,
             price_gap: Decimal, cap_gap: Decimal, provider_label: str, retrieved_at: str) -> list:
    """M1: the deal's price per unit or implied cap rate is far from the sale comps."""
    from propertyiq.screening.intake.anomalies import Flag, pct, usd
    flags = []
    src = [f"Sale comps: {provider_label}, retrieved {retrieved_at[:10]}"]
    med = summary["price_per_unit"]["median"]
    if price and units and med:
        ppu = price / units
        gap = (ppu - Decimal(str(med))) / Decimal(str(med))
        if gap > price_gap:
            flags.append(Flag("M1", "price_per_unit", "medium", "Price per unit well above sale comps",
                f"The deal's price works out to {usd(ppu)} per unit, {gap * 100:.0f}% above the comp median of "
                f"{usd(Decimal(str(med)))} ({summary['n']} sales).",
                [{"label": "Deal price per unit", "value": usd(ppu)},
                 {"label": "Comp median price per unit", "value": usd(Decimal(str(med)))},
                 {"label": "Comp range", "value": f"{usd(Decimal(str(summary['price_per_unit']['low'])))} to "
                                                  f"{usd(Decimal(str(summary['price_per_unit']['high'])))}"}],
                src, "Use the lower of price and a supportable value for LTV; comps are support, not an appraisal."))
    cap_med = summary["cap_rate"]["median"]
    if price and noi and cap_med is not None:
        implied = noi / price
        if Decimal(str(cap_med)) - implied > cap_gap:
            flags.append(Flag("M1", "cap_rate", "medium", "Implied cap rate well below sale comps",
                f"NOI of {usd(noi)} on a {usd(price)} price is a {pct(implied, 2)} cap rate, below the comp median of "
                f"{pct(Decimal(str(cap_med)), 2)} (cap rates reported by source for {summary['cap_rate']['n']} sales).",
                [{"label": "Implied cap rate", "value": pct(implied, 2)},
                 {"label": "Comp median cap rate (reported by source)", "value": pct(Decimal(str(cap_med)), 2)}],
                src, "A low cap rate means a high price for the income; check the price against the income."))
    return flags


def distance_ok(c: dict, radius: float) -> bool:
    return c.get("distance_miles") is None or c["distance_miles"] <= radius + 1e-9 or math.isclose(c["distance_miles"], radius)
