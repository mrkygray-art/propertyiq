"""California red flags R1-R5. Screening flags only: never a legal determination.

Rules come from the editable screening_rules table, each with its source URL and last-verified
date (or marked UNVERIFIED). Wording is deliberately cautious: "may apply, verify".

  R1 Prop 13 reassessment     a purchase resets the tax bill; compare with the T-12 tax line
  R2 Rent control             LA RSO, Santa Monica, statewide AB 1482, or "check local rules"
  R3 Seismic                  soft-story, non-ductile concrete, unreinforced masonry, mapped zones
  R4 Environmental            lead paint, asbestos, site history, hazard map lookups
  R5 Deferred maintenance     condition checklist against a typical-life table; capex estimate
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from propertyiq.screening.intake.anomalies import Flag, pct, usd

ZERO = Decimal(0)
SITE_HISTORY = {"gas_station": "a gas station", "dry_cleaner": "a dry cleaner", "auto_repair": "auto repair",
                "industrial": "industrial use"}


def _source(rule: dict) -> str:
    if rule.get("verified") and rule.get("source_url"):
        return f"{rule['jurisdiction']}: {rule['source_url']} (verified {rule['last_verified']})"
    return (f"UNVERIFIED, confirm before relying on it. {rule['jurisdiction']}: {rule.get('source_url') or 'no source'}"
            + (f". {rule['notes']}" if rule.get("notes") else ""))


def _city(facts: dict) -> str:
    return re.sub(r"\s+", " ", str(facts.get("city") or "")).strip().lower()


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def evaluate(facts: dict, rules: list[dict], t12_annual: dict[str, Decimal] | None, noi: Decimal | None,
             condition: list[dict], life_table: list[dict], repairs_per_unit_low: Decimal | None,
             example_tax_rate: Decimal, as_of: date | None = None) -> list[Flag]:
    as_of = as_of or date.today()
    R = {r["rule_key"]: r for r in rules}
    flags: list[Flag] = []
    city = _city(facts)
    year = _int(facts.get("year_built"))
    units = _int(facts.get("units"))
    stories = _int(facts.get("stories"))
    construction = facts.get("construction") or ""
    parking = facts.get("parking") or ""
    age = as_of.year - year if year else None

    # R1 Prop 13 ---------------------------------------------------------------------------------
    rule = R.get("ca_prop13_reassessment")
    price = Decimal(str(facts["purchase_price"])) if facts.get("purchase_price") not in (None, "") else None
    if rule and facts.get("transaction") == "purchase" and price:
        estimate = price * example_tax_rate
        t12_tax = (t12_annual or {}).get("real_estate_taxes")
        ev = [{"label": "Purchase price", "value": usd(price)},
              {"label": "Example tax rate (confirm the county rate)", "value": pct(example_tax_rate, 2)},
              {"label": "Estimated tax after purchase", "value": usd(estimate)},
              {"label": "After that", "value": "assessed value can rise at most 2% a year"}]
        if t12_tax is not None:
            gap = estimate - t12_tax
            ev.insert(3, {"label": "T-12 property tax (annualized)", "value": usd(t12_tax)})
            if gap > estimate * Decimal("0.05"):
                ev += [{"label": "Gap (tax increase)", "value": usd(gap)},
                       {"label": "NOI impact", "value": f"-{usd(gap)}" + (f" ({gap / noi * 100:.1f}% of NOI)" if noi else "")}]
                flags.append(Flag("R1", "prop13", "high" if (noi and gap > noi * Decimal("0.05")) or gap > 10_000 else "medium",
                    "Property tax may reset higher after purchase (Prop 13)",
                    f"A purchase at {usd(price)} would likely reset the tax bill to about {usd(estimate)} a year at an example "
                    f"{pct(example_tax_rate, 2)} rate, versus {usd(t12_tax)} on the T-12: about {usd(gap)} more. "
                    "May apply; verify the county's rate for this parcel.",
                    ev, [_source(rule)],
                    f"Underwrite property tax at about {usd(estimate)} (reason TAX_REASSESSMENT), after confirming the rate."))
        else:
            flags.append(Flag("R1", "prop13", "medium", "Property tax may reset after purchase (Prop 13)",
                f"A purchase at {usd(price)} would likely reset the tax bill to about {usd(estimate)} a year at an example "
                f"{pct(example_tax_rate, 2)} rate. No T-12 tax line is approved to compare against.",
                ev, [_source(rule)], "Underwrite property tax at the post-purchase estimate once the rate is confirmed."))

    # R2 rent control ----------------------------------------------------------------------------
    growth = ("Don't assume rents can rise faster than the allowed increase, or that below-market rents can be "
              "raised to market quickly. Use a conservative rent-growth assumption (reason RENT_CONTROL).")
    local_hit = False
    for key, before, inclusive in (("la_rso", "1978-10-01", True), ("santa_monica_rent_control", "1979-04-10", False)):
        rule = R.get(key)
        if not rule or city not in rule["trigger"].get("cities", []):
            continue
        cutoff = date.fromisoformat(rule["trigger"].get("built_on_or_before") or rule["trigger"].get("built_before") or before)
        min_units = rule["trigger"].get("min_units", 1)
        if year is None:
            fires, why = True, "the year built isn't entered"
        else:
            fires = year < cutoff.year or (year == cutoff.year)
            why = f"built in {year}" + (" (same year as the cutoff: check the certificate of occupancy date)"
                                        if year == cutoff.year else "")
        if fires and (units is None or units >= min_units):
            local_hit = True
            flags.append(Flag("R2", key, "medium", f"{rule['name']} may apply",
                f"{rule['description']} This property is in {facts.get('city')} and was {why}, so it may be covered. "
                "This is a screening flag, not a legal determination of coverage.",
                [{"label": "City", "value": str(facts.get("city"))}, {"label": "Year built", "value": str(year or "not entered")},
                 {"label": "Rule cutoff", "value": f"{'on or before' if inclusive else 'before'} {cutoff.isoformat()}"}],
                [_source(rule)], growth))
    rule = R.get("ca_ab1482")
    if rule and not local_hit and (age is None or age > rule["trigger"].get("older_than_years", 15)) \
            and as_of < date.fromisoformat(rule["trigger"].get("until", "2030-01-01")):
        flags.append(Flag("R2", "ca_ab1482", "low", "Statewide rent cap (AB 1482) may apply",
            f"{rule['description']} The building is {f'{age} years old' if age is not None else 'of unknown age'}. "
            "This is a screening flag, not a legal determination.",
            [{"label": "Building age", "value": f"{age} years" if age is not None else "not entered"}],
            [_source(rule)], growth))
    rule = R.get("local_rent_rules")
    if rule and city and city not in ("los angeles", "santa monica"):
        flags.append(Flag("R2", "local_rent_rules", "low", f"Check {facts.get('city')}'s local rent rules",
            rule["description"], [{"label": "City", "value": str(facts.get("city"))}], [_source(rule)],
            "Confirm whether a local rent control or tenant protection law applies."))

    # R3 seismic -----------------------------------------------------------------------------------
    old_wood_tuck = construction == "wood_frame" and year is not None and year < 1978 and \
        (stories or 0) >= 2 and parking in ("tuck_under", "open_ground_floor")
    rule = R.get("la_soft_story")
    if rule and city in rule["trigger"].get("cities", []) and old_wood_tuck and (units is None or units >= 4):
        flags.append(Flag("R3", "la_soft_story", "high", "Los Angeles soft-story retrofit may apply",
            f"{rule['description']} This building is wood frame, {stories} stories, built {year}, with "
            f"{parking.replace('_', '-')} parking: a soft-story indicator.",
            [{"label": "Construction", "value": "wood frame"}, {"label": "Year built", "value": str(year)},
             {"label": "Stories", "value": str(stories)}, {"label": "Parking", "value": parking.replace("_", "-")}],
            [_source(rule)], "Request proof of retrofit (or the city's compliance status), or a seismic risk report."))
    elif old_wood_tuck:
        flags.append(Flag("R3", "soft_story_indicator", "medium", "Soft-story indicator",
            "Older wood-frame buildings with open or tuck-under parking at ground level can be badly damaged in an "
            "earthquake. Some cities require retrofits; check the city's program.",
            [{"label": "Year built", "value": str(year)}, {"label": "Parking", "value": parking.replace("_", "-")}],
            ["Screening indicator (no specific rule for this city in PropertyIQ)"], "Request a seismic risk report."))
    rule = R.get("la_non_ductile_concrete")
    if rule and construction == "concrete" and city in rule["trigger"].get("cities", []) and year is not None and year <= 1977:
        flags.append(Flag("R3", "la_non_ductile_concrete", "high", "Los Angeles non-ductile concrete retrofit may apply",
            rule["description"], [{"label": "Construction", "value": "concrete"}, {"label": "Year built", "value": str(year)}],
            [_source(rule)], "Request the building's retrofit status and a structural evaluation."))
    rule = R.get("unreinforced_masonry")
    if rule and construction == "masonry":
        flags.append(Flag("R3", "unreinforced_masonry", "high", "Masonry building: check for unreinforced masonry",
            rule["description"], [{"label": "Construction", "value": "masonry"}, {"label": "Year built", "value": str(year or "not entered")}],
            [_source(rule)], "Confirm whether it is reinforced or retrofitted; request a seismic risk report."))
    rule = R.get("earthquake_zone_lookup")
    if rule:
        flags.append(Flag("R3", "earthquake_zone_lookup", "low", "Earthquake zone lookup not available",
            rule["description"], [{"label": "Check", "value": rule["source_url"]}], [_source(rule)],
            "Look up the address on EQ Zapp and note the result."))

    # R4 environmental -----------------------------------------------------------------------------
    rule = R.get("lead_paint_pre1978")
    if rule and year is not None and year < 1978:
        flags.append(Flag("R4", "lead_paint", "medium", "Lead-based paint possible (built before 1978)",
            rule["description"], [{"label": "Year built", "value": str(year)}], [_source(rule)],
            "Confirm lead disclosures are on file; consider testing before renovation."))
    rule = R.get("asbestos_pre1981")
    if rule and year is not None and year <= 1980:
        flags.append(Flag("R4", "asbestos", "medium", "Asbestos possible (built in 1980 or earlier)",
            rule["description"], [{"label": "Year built", "value": str(year)}], [_source(rule)],
            "Ask for an asbestos survey before any renovation that disturbs insulation, ceilings or flooring."))
    history = [k for k in SITE_HISTORY if facts.get("site_history", {}).get(k)]
    if history:
        flags.append(Flag("R4", "site_history", "high", "Past or neighbouring use may have left contamination",
            "The site or a neighbour was used for " + ", ".join(SITE_HISTORY[k] for k in history) + ". Contamination "
            "from these uses can become the owner's (and lender's) problem.",
            [{"label": "Reported use", "value": ", ".join(SITE_HISTORY[k] for k in history)}],
            [_source(R["environmental_lookup"])] if "environmental_lookup" in R else ["Reviewer input"],
            "Confirm a Phase I environmental site assessment is ordered or on file."))
    lookups = [R[k] for k in ("environmental_lookup", "fire_hazard_lookup", "flood_lookup") if k in R]
    if "la_methane_zone" in R and city in R["la_methane_zone"]["trigger"].get("cities", []):
        lookups.append(R["la_methane_zone"])
    if lookups:
        flags.append(Flag("R4", "hazard_lookups", "low", "Hazard and cleanup-site lookups to do",
            "PropertyIQ doesn't query these databases automatically. Check each by address and note the result.",
            [{"label": r["name"], "value": r["source_url"] or "no link"} for r in lookups] +
            [{"label": "Also", "value": "https://www.envirostor.dtsc.ca.gov/public/"}],
            [_source(r) for r in lookups], "Record what each lookup shows; flag anything found for a Phase I."))

    # R5 deferred maintenance ----------------------------------------------------------------------
    life = {r["item_key"]: r for r in life_table}
    total_cost = ZERO
    for item in condition:
        spec = life.get(item["item_key"])
        d = item.get("details") or {}
        cost = Decimal(str(item["estimated_cost"])) if item.get("estimated_cost") not in (None, "") else None
        if cost:
            total_cost += cost
        if not spec:
            continue
        reasons = []
        item_age = _int(d.get("age_years"))
        if item_age is None and _int(d.get("year_installed")):
            item_age = as_of.year - _int(d["year_installed"])
        if spec.get("typical_life") and item_age is not None and item_age >= spec["typical_life"]:
            reasons.append(f"{item_age} years old versus a typical life of {spec['typical_life']} years")
        text = " ".join(str(v) for k, v in d.items() if k != "age_years").lower()
        if spec.get("flag_if") and re.search(spec["flag_if"], text):
            reasons.append(f"noted as '{text}'")
        if reasons:
            sev = "high" if item["item_key"] in ("electrical_panel", "aluminum_wiring") else "medium"
            flags.append(Flag("R5", item["item_key"], sev, f"{spec['label']}: likely replacement needed",
                f"{spec['label']} is " + "; ".join(reasons) + ". That's a cost the next owner is likely to face.",
                [{"label": "Details", "value": ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in d.items() if v not in (None, ''))}]
                + ([{"label": "Estimated cost (your input)", "value": usd(cost)}] if cost else []),
                [f"Typical life table: {spec['source']} (reviewed {spec['last_reviewed']})"],
                "Get a contractor estimate; include it in the deferred maintenance total."))
    repairs = (t12_annual or {}).get("repairs_maintenance")
    if repairs is not None and units and repairs_per_unit_low and age is not None and age > 30:
        per_unit = repairs / units
        if per_unit < repairs_per_unit_low:
            flags.append(Flag("R5", "low_repairs_old_building", "medium", "Low repairs on an older building",
                f"Repairs and maintenance run {usd(per_unit)} per unit a year on a {age}-year-old building, below the "
                f"{usd(repairs_per_unit_low)} low benchmark. Repairs may have been put off.",
                [{"label": "Repairs per unit", "value": usd(per_unit)}, {"label": "Building age", "value": f"{age} years"}],
                ["T-12 repairs line", "Benchmark: example only, set your own"],
                "Order a property condition assessment; consider a repair reserve or holdback."))
    if total_cost > 0:
        flags.append(Flag("R5", "capex_total", "medium", "Deferred maintenance estimate",
            f"The condition checklist estimates {usd(total_cost)} of deferred maintenance.",
            [{"label": "Total of your estimates", "value": usd(total_cost)}] +
            [{"label": life.get(i["item_key"], {}).get("label", i["item_key"]), "value": usd(Decimal(str(i["estimated_cost"])))}
             for i in condition if i.get("estimated_cost") not in (None, "", 0)],
            ["Condition checklist (reviewer input)"],
            "Apply it as a reserve or a holdback in the underwriting (reason DEFERRED_MAINTENANCE)."))
    return flags
