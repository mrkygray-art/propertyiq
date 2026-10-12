"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import Shell from "@/components/Shell";
import { api, ApiError, type Periods, type Summary, type Underperforming, type User } from "@/lib/api";
import { ASSET_LABELS, pct, RULE_LABELS, size, usd } from "@/lib/format";

function Dashboard({ user }: { user: User }) {
  const router = useRouter();
  const params = useSearchParams();
  const [periods, setPeriods] = useState<Periods | null>(null);
  const period = params.get("period") ?? periods?.default_period ?? null;
  const [summary, setSummary] = useState<Summary | null>(null);
  const [under, setUnder] = useState<Underperforming | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [assetFilter, setAssetFilter] = useState("all");
  const [ruleFilter, setRuleFilter] = useState("all");

  useEffect(() => { api.periods().then(setPeriods).catch((e) => setError(String(e.message ?? e))); }, []);
  useEffect(() => {
    if (!period) return;
    setError(null);
    Promise.all([api.summary(period), api.underperforming(period)])
      .then(([s, u]) => { setSummary(s); setUnder(u); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."));
  }, [period]);

  const rows = useMemo(() => (under?.flagged ?? []).filter((f) =>
    (assetFilter === "all" || f.asset_class === assetFilter) &&
    (ruleFilter === "all" || f.rules_triggered.includes(ruleFilter))), [under, assetFilter, ruleFilter]);

  const portfolioNames = user.portfolios.map((p) => p.replace("FUND-", "Synthetic Fund ")).join(", ");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Portfolio overview</h1>
          <div className="sub">{portfolioNames} · trailing 12 months ending the selected quarter</div>
        </div>
        <label className="filters">
          Reporting period
          <select value={period ?? ""} onChange={(e) => router.replace(`/?period=${e.target.value}`)} disabled={!periods}>
            {periods?.periods.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      </div>

      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="grid-main">
        <div className="stack">
          {summary && under && (
            <div className="kpis">
              <div className="kpi">
                <div className="label">Properties</div>
                <div className="value">{summary.property_count}</div>
                <div className="note">{summary.by_asset_class.map((c) => `${c.property_count} ${ASSET_LABELS[c.asset_class].toLowerCase()}`).join(" · ")}</div>
              </div>
              <div className="kpi">
                <div className="label">Flagged as underperforming</div>
                <div className="value">{under.flagged_count} <span className="muted" style={{ fontSize: 15 }}>of {under.scope.properties_evaluated}</span></div>
                <div className="note">Rule set {under.rule_set.version}</div>
              </div>
              <div className="kpi">
                <div className="label">NOI budget shortfall (flagged)</div>
                <div className="value">{usd(under.total_budget_shortfall_flagged)}</div>
                <div className="note">Below budget, not a forecast loss</div>
              </div>
              <div className="kpi">
                <div className="label">TTM NOI vs budget</div>
                <div className="value">{usd(summary.noi_actual, true)}</div>
                <div className="note">
                  {summary.budget_variance >= 0 ? "+" : ""}{usd(summary.budget_variance, true)} {summary.budget_variance >= 0 ? "over" : "under"}{" "}
                  {usd(summary.noi_budget, true)} budget · YoY {summary.noi_yoy_pct !== null && summary.noi_yoy_pct > 0 ? "+" : ""}{pct(summary.noi_yoy_pct)}
                </div>
              </div>
            </div>
          )}

          <section className="card">
            <div className="card-head">
              <h2>Underperforming assets</h2>
              <div className="filters">
                <select aria-label="Filter by asset class" value={assetFilter} onChange={(e) => setAssetFilter(e.target.value)}>
                  <option value="all">All asset classes</option>
                  {Object.entries(ASSET_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
                <select aria-label="Filter by rule" value={ruleFilter} onChange={(e) => setRuleFilter(e.target.value)}>
                  <option value="all">Any rule</option>
                  {Object.entries(RULE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
            </div>
            {!under ? <div className="empty">Loading…</div> : rows.length === 0 ? (
              <div className="empty">No flagged properties match.</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th><th>Property</th><th className="num">Occupancy</th><th className="num">NOI YoY</th>
                      <th className="num">TTM NOI</th><th className="num">Budget</th><th className="num">Shortfall</th><th>Rules triggered</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((f) => (
                      <tr key={f.property_id} className="clickable" onClick={() => router.push(`/properties/${f.property_id}?period=${under.period}`)}>
                        <td><span className="rank">{f.priority_rank}</span></td>
                        <td>
                          <Link href={`/properties/${f.property_id}?period=${under.period}`} onClick={(e) => e.stopPropagation()}>{f.name}</Link>
                          <div className="muted" style={{ fontSize: 12 }}>{f.property_id} · {ASSET_LABELS[f.asset_class]} · {size(f.rentable_size, f.size_unit)}</div>
                        </td>
                        <td className="num">{pct(f.metrics.occupancy_pct)}</td>
                        <td className="num">{pct(f.metrics.noi_yoy_pct)}</td>
                        <td className="num">{usd(f.metrics.noi_actual)}</td>
                        <td className="num">{usd(f.metrics.noi_budget)}</td>
                        <td className="num"><b>{usd(f.metrics.budget_shortfall)}</b></td>
                        <td><div className="chips">{f.rules_triggered.map((r) => <span key={r} className="chip rule">▲ {RULE_LABELS[r]}</span>)}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {under && (
              <div className="sub" style={{ marginTop: 12 }}>
                {under.rule_set.logic} Rules: {under.rule_set.rules.map((r) => r.condition).join("; ")}. {under.rule_set.priority_rule}{" "}
                <Link href={`/verified?period=${under.period}`}>Verified calculations →</Link>
              </div>
            )}
          </section>

          {summary && (
            <section className="card">
              <div className="card-head"><h2>By asset class</h2><span className="sub">Occupancy is size-weighted within each class</span></div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Asset class</th><th className="num">Properties</th><th className="num">Occupancy</th><th className="num">TTM NOI</th><th className="num">Budget</th><th className="num">Variance</th><th className="num">Flagged</th></tr>
                  </thead>
                  <tbody>
                    {summary.by_asset_class.map((c) => (
                      <tr key={c.asset_class}>
                        <td>{ASSET_LABELS[c.asset_class]} <span className="muted">({c.size_unit})</span></td>
                        <td className="num">{c.property_count}</td>
                        <td className="num">{pct(c.occupancy_pct)}</td>
                        <td className="num">{usd(c.noi_actual)}</td>
                        <td className="num">{usd(c.noi_budget)}</td>
                        <td className="num">{usd(c.budget_variance)}</td>
                        <td className="num">{c.flagged_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
        <ChatPanel
          period={period}
          suggestions={[
            "Summarize our underperforming assets.",
            ...(under?.flagged[0] ? [`Why was ${under.flagged[0].name.split(" ")[0]} flagged?`] : []),
            "Show the underlying NOI budget variance.",
            "Give me a portfolio overview.",
          ]}
        />
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Shell>{(user) => <Dashboard user={user} />}</Shell>
    </Suspense>
  );
}
