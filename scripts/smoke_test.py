"""Smoke test a running API (default http://127.0.0.1:8000). Exits non-zero on any failure.

Usage: python scripts/smoke_test.py [base_url]
"""
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))
from propertyiq.config import load_dotenv  # noqa: E402


def main() -> None:
    load_dotenv()
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    failures = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    with httpx.Client(base_url=base, timeout=30, trust_env=False) as c:
        health = c.get("/health").json()
        check("health: database", health["database"])
        check("health: local model", health["local_model"])

        def login(user):
            r = c.post("/api/auth/login", json={"username": user, "password": os.environ["PIQ_DEMO_PASSWORD"]})
            return {"Authorization": f"Bearer {r.json()['token']}"}

        a = c.get("/api/properties/underperforming", headers=login("analyst")).json()
        check("analyst: 4 of 55 flagged", (a["flagged_count"], a["scope"]["properties_evaluated"]) == (4, 55),
              f"{a['flagged_count']} of {a['scope']['properties_evaluated']}")
        check("analyst: $420,000 shortfall", a["total_budget_shortfall_flagged"] == 420000.0,
              f"${a['total_budget_shortfall_flagged']:,.2f}")
        for f in a["flagged"]:
            print(f"      #{f['priority_rank']} {f['property_id']} {f['name']}: occupancy {f['metrics']['occupancy_pct']}%, "
                  f"YoY {f['metrics']['noi_yoy_pct']}%, shortfall ${f['metrics']['budget_shortfall']:,.0f}, "
                  f"rules {', '.join(f['rules_triggered'])}")

        rh = login("restricted")
        r = c.get("/api/properties/underperforming", headers=rh).json()
        check("restricted: 2 of 27 flagged", (r["flagged_count"], r["scope"]["properties_evaluated"]) == (2, 27))
        check("restricted: Westlake blocked (404)", c.get("/api/properties/PIQ-001", headers=rh).status_code == 404)
        check("no token: 401", c.get("/api/properties/underperforming").status_code == 401)

    print("\nALL PASSED" if not failures else f"\n{len(failures)} FAILED: {', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
