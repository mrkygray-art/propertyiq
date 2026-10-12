"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import LineChart from "@/components/LineChart";
import MaturityWall from "@/components/debt/MaturityWall";
import RiskScatter from "@/components/debt/RiskScatter";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import { covenantThreshold, covenantValue, debtApi, ratio, STATUS_META, type DebtSummary, type LoanRow } from "@/lib/debt";
import { pct, usd } from "@/lib/format";

type Filter = "all" | "breach" | "watch" | "fixed" | "floating";

function StatusChip({ status }: { status: LoanRow["status"] }) {
  const m = STATUS_META[status];
  return <span className={`chip ${m.chip}`}>{m.icon} {m.label}</span>;
}

function Debt() {
  const params = useSearchParams();
  const router = useRouter();
  const [data, setData] = useState<DebtSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const period = params.get("period");

  useEffect(() => {
    setError(null);
    debtApi.summary(period).then(setData).catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."));
  }, [period]);

  const rows = useMemo(() => (data?.loans ?? []).filter((l) =>
    filter === "all" ? true : filter === "breach" || filter === "watch" ? l.status === filter : l.rate_type === filter), [data, filter]);

  if (error) return <div className="error" role="alert">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;
  const t = data.totals;
  const watchlist = data.loans.filter((l) => l.status === "breach" || l.status === "watch");
  const link = (id: string) => `/debt/${id}?period=${data.period}`;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Debt and covenants</h1>
          <div className="sub">
            {data.scope.loan_count} synthetic loans in {data.scope.funds.join(" and ")} · trailing 12 months to {data.period}
          </div>
        </div>
        <label className="filters">
          Reporting period
          <select value={data.period} onChange={(e) => router.replace(`/debt?period=${e.target.value}`)}>
            {data.periods.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      </div>

      <div className="stack">
        <div className="kpis">
          <div className="kpi"><div className="label">Outstanding debt</div><div className="value">{usd(t.balance, true)}</div>
            <div className="note">{pct(t.floating_share_pct, 0)} floating · {pct(t.weighted_rate_pct, 2)} weighted rate</div></div>
          <div className="kpi"><div className="label">Portfolio DSCR</div><div className="value">{ratio(t.aggregate_dscr)}</div>
            <div className="note">Total NOI ÷ total debt service</div></div>
          <div className="kpi"><div className="label">Portfolio LTV</div><div className="value">{pct(t.aggregate_ltv_pct)}</div>
            <div className="note">Total balance ÷ total value</div></div>
          <div className="kpi"><div className="label">Covenant breaches</div>
            <div className="value" style={{ color: t.breach_count ? "var(--critical)" : undefined }}>{t.breach_count}</div>
            <div className="note">{t.watch_count} more on watch (within 5%)</div></div>
        </div>

        <div className="grid-2">
          <section className="card" aria-labelledby="wall-h">
            <div className="card-head"><h2 id="wall-h">Maturity wall</h2>
              <span className="muted" style={{ fontSize: 12 }}>{usd(t.maturing_12m_balance, true)} due within 12 months</span></div>
            <MaturityWall data={data.maturity_wall} />
          </section>
          <section className="card" aria-labelledby="risk-h">
            <div className="card-head"><h2 id="risk-h">DSCR × LTV risk map</h2>
              <span className="muted" style={{ fontSize: 12 }}>Click a loan to open it</span></div>
            <RiskScatter loans={data.loans} period={data.period} />
          </section>
        </div>

        <section className="card" aria-labelledby="watch-h">
          <div className="card-head"><h2 id="watch-h">Covenant watchlist</h2>
            <span className="muted" style={{ fontSize: 12 }}>Breaches first, then loans within 5% of a threshold</span></div>
          {watchlist.length === 0 ? <div className="empty">No breaches or watch items.</div> : (
            <div className="table-wrap">
              <table className="wide">
                <thead><tr><th>Loan</th><th>Status</th><th>Covenant tests</th><th className="num">Balance</th><th>Matures</th></tr></thead>
                <tbody>
                  {watchlist.map((l) => (
                    <tr key={l.loan_id} className="clickable" onClick={() => router.push(link(l.loan_id))}>
                      <td><Link href={link(l.loan_id)} onClick={(e) => e.stopPropagation()}>{l.name}</Link>
                        <div className="muted" style={{ fontSize: 12 }}>{l.loan_id} · {l.fund_id} · {l.lender}</div></td>
                      <td><StatusChip status={l.status} /></td>
                      <td>
                        {l.covenants.map((c) => (
                          <div key={c.kind} style={{ fontSize: 13, color: c.status === "breach" ? "var(--critical)" : undefined }}>
                            {c.label}: <b>{covenantValue(c)}</b> <span className="muted">(needs {covenantThreshold(c)})</span>
                          </div>
                        ))}
                      </td>
                      <td className="num">{usd(l.balance, true)}</td>
                      <td>{l.maturity_date}<div className="muted" style={{ fontSize: 12 }}>{l.months_to_maturity} months</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card" aria-labelledby="trend-h">
          <div className="card-head"><h2 id="trend-h">Portfolio DSCR by quarter</h2>
            <span className="muted" style={{ fontSize: 12 }}>Same loans, trailing 12 months at each quarter-end</span></div>
          <LineChart labels={data.trend.map((p) => p.period)} ariaLabel="Portfolio DSCR by quarter"
                     series={[{ key: "dscr", label: "Portfolio DSCR", color: "var(--series-1)", values: data.trend.map((p) => p.aggregate_dscr) }]}
                     format={(v) => `${v.toFixed(2)}×`} threshold={{ value: 1.25, label: "1.25× common covenant" }} height={200} />
        </section>

        <section className="card" aria-labelledby="loans-h">
          <div className="card-head">
            <h2 id="loans-h">All loans</h2>
            <div className="filters" role="group" aria-label="Filter loans">
              {(["all", "breach", "watch", "fixed", "floating"] as Filter[]).map((f) => (
                <button key={f} className={`btn ${filter === f ? "" : "secondary"}`} style={{ padding: "4px 10px", fontSize: 12 }}
                        aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {f === "all" ? `All (${data.loans.length})` : f[0].toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <div className="table-wrap">
            <table className="wide">
              <thead>
                <tr><th>Loan</th><th>Lender</th><th>Rate</th><th className="num">Balance</th><th className="num">DSCR</th>
                  <th className="num">LTV</th><th className="num">Debt yield</th><th>Matures</th><th>Covenants</th></tr>
              </thead>
              <tbody>
                {rows.map((l) => (
                  <tr key={l.loan_id} className="clickable" onClick={() => router.push(link(l.loan_id))}>
                    <td><Link href={link(l.loan_id)} onClick={(e) => e.stopPropagation()}>{l.name}</Link>
                      <div className="muted" style={{ fontSize: 12 }}>{l.loan_id} · {l.fund_id}</div></td>
                    <td style={{ fontSize: 13 }}>{l.lender}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{pct(l.rate_pct, 2)} <span className="muted" style={{ fontSize: 12 }}>{l.rate_type}</span></td>
                    <td className="num">{usd(l.balance, true)}</td>
                    <td className="num">{ratio(l.dscr)}</td>
                    <td className="num">{pct(l.ltv_pct)}</td>
                    <td className="num">{pct(l.debt_yield_pct)}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{l.maturity_date}</td>
                    <td><StatusChip status={l.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Synthetic loans and lenders. DSCR = {data.definitions.dscr}; NOI = {data.definitions.noi.toLowerCase()}.
            Floating rates use {data.definitions.floating_rate}. Payload SHA-256 <code>{data.payload_sha256.slice(0, 16)}…</code>
          </p>
        </section>
      </div>
    </>
  );
}

export default function DebtPage() {
  return <Shell>{() => <Suspense fallback={<div className="empty">Loading…</div>}><Debt /></Suspense>}</Shell>;
}
