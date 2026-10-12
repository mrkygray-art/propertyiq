"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import LineChart from "@/components/LineChart";
import Shell from "@/components/Shell";
import { api, ApiError, type PropertyDetail } from "@/lib/api";
import { ASSET_LABELS, pct, RULE_LABELS, size, usd } from "@/lib/format";

const STATUS: Record<string, { cls: string; text: string }> = {
  triggered: { cls: "rule", text: "▲ Triggered" },
  not_triggered: { cls: "ok", text: "✓ Not triggered" },
  not_evaluated: { cls: "neutral", text: "? Not evaluated" },
};

function Detail() {
  const { id } = useParams<{ id: string }>();
  const period = useSearchParams().get("period");
  const [data, setData] = useState<PropertyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api.property(id, period).then(setData).catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."));
  }, [id, period]);

  if (error) {
    return (
      <>
        <div className="breadcrumb"><Link href="/">← Portfolio overview</Link></div>
        <div className="error" role="alert">{error === "Property not found" ? "Property not found, or you don't have access to it." : error}</div>
      </>
    );
  }
  if (!data) return <div className="empty">Loading…</div>;

  const p = data.property;
  const m = p.metrics;
  const calc = p.calculation;
  const occRule = p.rules.find((r) => r.rule_id === "occupancy_below_min");
  const labels = data.monthly.map((x) => x.month_end);
  const ttmStart = labels.indexOf(calc.current_ttm.start);
  const name = p.name.split(" ")[0];

  return (
    <>
      <div className="breadcrumb"><Link href={`/?period=${data.period}`}>← Portfolio overview</Link></div>
      <div className="page-head">
        <div>
          <h1>{p.name}</h1>
          <div className="sub">
            {p.property_id} · {ASSET_LABELS[p.asset_class]} · {size(p.rentable_size, p.size_unit)} · {p.market} · {p.portfolio_name}
          </div>
        </div>
        {p.flagged
          ? <span className="chip rule" style={{ fontSize: 13, padding: "4px 12px" }}>▲ Flagged · priority {p.priority_rank}</span>
          : <span className="chip ok" style={{ fontSize: 13, padding: "4px 12px" }}>✓ Not flagged</span>}
      </div>

      <div className="grid-main">
        <div className="stack">
          <div className="kpis">
            <div className="kpi"><div className="label">Physical occupancy</div><div className="value">{pct(m.occupancy_pct)}</div><div className="note">At {data.period}</div></div>
            <div className="kpi"><div className="label">TTM NOI</div><div className="value">{usd(m.noi_actual)}</div><div className="note">Budget {usd(m.noi_budget)} · {pct(m.noi_to_budget_pct)} of budget</div></div>
            <div className="kpi"><div className="label">Budget shortfall</div><div className="value">{usd(m.budget_shortfall)}</div><div className="note">Variance {usd(m.budget_variance)}</div></div>
            <div className="kpi"><div className="label">NOI year over year</div><div className="value">{pct(m.noi_yoy_pct)}</div><div className="note">Prior TTM {usd(m.noi_prior)}</div></div>
          </div>

          <section className="card">
            <div className="card-head"><h2>Rule checks</h2><span className="sub">Rule set {calc.rule_set_version} · TTM {calc.current_ttm.start} to {calc.current_ttm.end}</span></div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Rule</th><th className="num">Value</th><th>Condition</th><th>Result</th><th>Explanation</th></tr></thead>
                <tbody>
                  {p.rules.map((r) => (
                    <tr key={r.rule_id}>
                      <td>{RULE_LABELS[r.rule_id] ?? r.rule_id}<div className="muted mono">{r.rule_id}</div></td>
                      <td className="num">{pct(r.value, 2)}</td>
                      <td className="mono">{r.comparator} {r.threshold}%</td>
                      <td><span className={`chip ${STATUS[r.status].cls}`}>{STATUS[r.status].text}</span></td>
                      <td className="sub">{r.explanation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {m.data_issues.length > 0 && <div className="error" style={{ marginTop: 10 }}>Data issues: {m.data_issues.join("; ")}</div>}
          </section>

          <div className="grid-2">
            <section className="card">
              <div className="card-head"><h2>Monthly NOI vs budget</h2></div>
              <LineChart
                ariaLabel={`Monthly NOI and budget NOI for ${p.name}, ${labels[0]} to ${labels[labels.length - 1]}`}
                labels={labels}
                series={[
                  { key: "noi", label: "Actual", color: "var(--series-1)", values: data.monthly.map((x) => x.noi) },
                  { key: "budget", label: "Budget", color: "var(--series-2)", dashed: true, values: data.monthly.map((x) => x.budget_noi) },
                ]}
                format={(v) => usd(v)}
                axisFormat={(v) => usd(v, true)}
                bandFrom={ttmStart >= 0 ? ttmStart : undefined}
                bandLabel="Current TTM"
              />
            </section>
            <section className="card">
              <div className="card-head"><h2>Physical occupancy</h2></div>
              <LineChart
                ariaLabel={`Monthly physical occupancy for ${p.name}`}
                labels={labels}
                series={[{ key: "occ", label: "Occupancy", color: "var(--series-1)", values: data.monthly.map((x) => x.occupancy_pct) }]}
                format={(v) => pct(v, 2)}
                axisFormat={(v) => `${v}%`}
                threshold={occRule ? { value: occRule.threshold, label: `Flag below ${occRule.threshold}%` } : undefined}
                bandFrom={ttmStart >= 0 ? ttmStart : undefined}
              />
            </section>
          </div>

          <section className="card" id="calculation">
            <div className="card-head"><h2>Calculation trail</h2><span className="sub">Computed in code from the records below. The model never computes these.</span></div>
            <div className="grid-2">
              <div>
                <h3 style={{ marginBottom: 8 }}>Inputs</h3>
                <dl className="kv">
                  <dt>Current TTM</dt><dd>{calc.current_ttm.start} to {calc.current_ttm.end}</dd>
                  <dt>Prior TTM</dt><dd>{calc.prior_ttm.start} to {calc.prior_ttm.end}</dd>
                  {Object.entries(calc.inputs).map(([k, v]) => (
                    <Fragment key={k}><dt>{k.replaceAll("_", " ")}</dt><dd>{typeof v === "number" ? (k.includes("months") || k.includes("size") ? v.toLocaleString("en-US") : usd(v)) : String(v)}</dd></Fragment>
                  ))}
                </dl>
              </div>
              <div>
                <h3 style={{ marginBottom: 8 }}>Formulas</h3>
                <dl className="kv">
                  {Object.entries(calc.formulas).map(([k, v]) => (<Fragment key={k}><dt>{k}</dt><dd className="mono">{v}</dd></Fragment>))}
                </dl>
              </div>
            </div>
            <h3 style={{ margin: "14px 0 8px" }}>Source records</h3>
            <dl className="kv">
              {Object.entries(calc.source_records).map(([k, ids]) => (
                <Fragment key={k}><dt className="mono">{k}</dt><dd className="mono">{ids.length ? `${ids.join(", ")}` : "none"}</dd></Fragment>
              ))}
            </dl>
          </section>

          <section className="card">
            <details>
              <summary><b>Monthly data (table view)</b></summary>
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table>
                  <thead><tr><th>Month</th><th className="num">Record</th><th className="num">Revenue</th><th className="num">Expenses</th><th className="num">NOI</th><th className="num">Budget NOI</th><th className="num">Occupancy</th></tr></thead>
                  <tbody>
                    {[...data.monthly].reverse().map((x) => (
                      <tr key={x.month_end}>
                        <td>{x.month_end}</td><td className="num mono">{x.record_id}</td>
                        <td className="num">{usd(x.operating_revenue)}</td><td className="num">{usd(x.operating_expenses)}</td>
                        <td className="num">{usd(x.noi)}</td><td className="num">{usd(x.budget_noi)}</td><td className="num">{pct(x.occupancy_pct, 2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </section>
        </div>
        <ChatPanel
          period={data.period}
          initialFocus={p.property_id}
          suggestions={[`Why was ${name} flagged?`, "Show the underlying NOI budget variance.", `Tell me about ${name}.`]}
        />
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Shell>{() => <Detail />}</Shell>
    </Suspense>
  );
}
