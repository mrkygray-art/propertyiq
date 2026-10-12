"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import LineChart from "@/components/LineChart";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import { covenantThreshold, covenantValue, debtApi, ratio, STATUS_META, type LoanDetail } from "@/lib/debt";
import { ASSET_LABELS, pct, usd } from "@/lib/format";

function Detail() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const period = params.get("period");
  const [d, setD] = useState<LoanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    debtApi.loan(id, period).then(setD).catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."));
  }, [id, period]);

  if (error) return (<><div className="breadcrumb"><Link href="/debt">← Debt and covenants</Link></div>
    <div className="error" role="alert">{error}</div></>);
  if (!d) return <div className="empty">Loading…</div>;

  const L = d.loan, m = d.metrics;
  const status = STATUS_META[d.status];
  const dscrCov = d.covenants.find((c) => c.kind === "min_dscr");
  const ltvCov = d.covenants.find((c) => c.kind === "max_ltv");
  const labels = d.history.map((h) => h.period);
  const rateText = L.rate_type === "fixed"
    ? `${pct(L.fixed_rate_pct, 2)} fixed`
    : `${L.index_name} + ${L.spread_bps} bps (now ${pct(m.rate_pct, 2)})`;
  const amort = L.amortization_months === 0 ? "Interest only to maturity"
    : `${L.amortization_months / 12}-year amortization${L.io_months ? ` after ${L.io_months} months interest only` : ""}`;

  return (
    <>
      <div className="breadcrumb"><Link href={`/debt?period=${d.period}`}>← Debt and covenants</Link></div>
      <div className="page-head">
        <div>
          <h1>{L.name}</h1>
          <div className="sub">{L.loan_id} · {L.fund_name} · {L.lender} · trailing 12 months to {d.period}</div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className={`chip ${status.chip}`} style={{ fontSize: 13, padding: "4px 10px" }}>{status.icon} Covenants: {status.label}</span>
          <label className="filters">Period
            <select value={d.period} onChange={(e) => router.replace(`/debt/${L.loan_id}?period=${e.target.value}`)}>
              {d.periods.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="stack">
        <div className="kpis">
          <div className="kpi"><div className="label">DSCR</div><div className="value">{ratio(m.dscr)}</div>
            <div className="note">{usd(m.noi, true)} NOI ÷ {usd(m.debt_service, true)} debt service</div></div>
          <div className="kpi"><div className="label">LTV</div><div className="value">{pct(m.ltv_pct)}</div>
            <div className="note">{usd(m.balance, true)} ÷ {usd(m.value, true)} value</div></div>
          <div className="kpi"><div className="label">Debt yield</div><div className="value">{pct(m.debt_yield_pct)}</div>
            <div className="note">NOI ÷ balance</div></div>
          <div className="kpi"><div className="label">Balance</div><div className="value">{usd(m.balance, true)}</div>
            <div className="note">{usd(L.balloon, true)} due at maturity {L.maturity_date}</div></div>
        </div>

        <div className="grid-2">
          <section className="card" aria-labelledby="cov-h">
            <div className="card-head"><h2 id="cov-h">Covenant tests</h2></div>
            <table>
              <thead><tr><th>Covenant</th><th className="num">Required</th><th className="num">Actual</th><th>Result</th></tr></thead>
              <tbody>
                {d.covenants.map((c) => (
                  <tr key={c.kind}>
                    <td>{c.label}</td><td className="num">{covenantThreshold(c)}</td><td className="num">{covenantValue(c)}</td>
                    <td><span className={`chip ${STATUS_META[c.status].chip}`}>{STATUS_META[c.status].icon} {STATUS_META[c.status].label}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="hint">Watch: {d.definitions.watch}</p>
          </section>
          <section className="card" aria-labelledby="terms-h">
            <div className="card-head"><h2 id="terms-h">Loan terms</h2></div>
            <dl className="kv">
              <dt>Rate</dt><dd>{rateText}</dd>
              {L.cap && <><dt>Rate cap</dt><dd>{L.index_name} capped at {L.cap.strike_pct}% until {L.cap.expiry_date}
                {L.cap.active ? "" : <b style={{ color: "var(--critical)" }}> · expired</b>}</dd></>}
              <dt>Repayment</dt><dd>{amort}</dd>
              <dt>Original principal</dt><dd>{usd(L.original_principal)}</dd>
              <dt>Originated</dt><dd>{L.origination_date}</dd>
              <dt>Matures</dt><dd>{L.maturity_date} · {usd(L.balloon)} due</dd>
              <dt>Debt service</dt><dd>{usd(m.interest)} interest + {usd(m.principal)} principal (12 months)</dd>
            </dl>
          </section>
        </div>

        <div className="grid-2">
          <section className="card" aria-labelledby="dscr-h">
            <div className="card-head"><h2 id="dscr-h">DSCR by quarter</h2></div>
            <LineChart labels={labels} ariaLabel="DSCR by quarter against the covenant" height={210}
                       series={[{ key: "dscr", label: "DSCR", color: "var(--series-1)", values: d.history.map((h) => h.dscr) }]}
                       format={(v) => `${v.toFixed(2)}×`}
                       threshold={dscrCov ? { value: dscrCov.threshold, label: `Covenant ${dscrCov.threshold.toFixed(2)}×` } : undefined} />
          </section>
          <section className="card" aria-labelledby="ltv-h">
            <div className="card-head"><h2 id="ltv-h">LTV by quarter</h2></div>
            <LineChart labels={labels} ariaLabel="LTV by quarter against the covenant" height={210}
                       series={[{ key: "ltv", label: "LTV", color: "var(--series-1)", values: d.history.map((h) => h.ltv_pct) }]}
                       format={(v) => `${v.toFixed(1)}%`}
                       threshold={ltvCov ? { value: ltvCov.threshold, label: `Covenant ${ltvCov.threshold}%` } : undefined} />
          </section>
        </div>

        <section className="card" aria-labelledby="col-h">
          <div className="card-head"><h2 id="col-h">Collateral</h2>
            <span className="muted" style={{ fontSize: 12 }}>{d.collateral.length} propert{d.collateral.length === 1 ? "y" : "ies"}</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Property</th><th className="num">Pledged</th><th className="num">TTM NOI</th><th className="num">Counted NOI</th>
                <th className="num">Value</th><th className="num">Counted value</th><th>Valued</th></tr></thead>
              <tbody>
                {d.collateral.map((c) => (
                  <tr key={c.property_id}>
                    <td><Link href={`/properties/${c.property_id}`}>{c.name}</Link>
                      <div className="muted" style={{ fontSize: 12 }}>{c.property_id} · {ASSET_LABELS[c.asset_class] ?? c.asset_class} · {c.market}</div></td>
                    <td className="num">{c.allocation_pct}%</td><td className="num">{usd(c.ttm_noi)}</td><td className="num">{usd(c.allocated_noi)}</td>
                    <td className="num">{usd(c.value)}</td><td className="num">{usd(c.allocated_value)}</td><td>{c.valuation_date ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card" aria-labelledby="calc-h">
          <div className="card-head"><h2 id="calc-h">How these numbers were calculated</h2></div>
          <dl className="kv">
            <dt>NOI</dt><dd>{usd(m.noi)}: {d.definitions.noi}</dd>
            <dt>Debt service</dt><dd>{usd(m.debt_service)}: {d.definitions.debt_service}</dd>
            <dt>DSCR</dt><dd>{usd(m.noi)} ÷ {usd(m.debt_service)} = <b>{ratio(m.dscr)}</b></dd>
            <dt>LTV</dt><dd>{usd(m.balance)} ÷ {usd(m.value)} = <b>{pct(m.ltv_pct, 2)}</b> ({d.definitions.ltv})</dd>
            <dt>Debt yield</dt><dd>{usd(m.noi)} ÷ {usd(m.balance)} = <b>{pct(m.debt_yield_pct, 2)}</b></dd>
            <dt>Valuation</dt><dd>{d.definitions.valuation}</dd>
            {L.rate_type === "floating" && <><dt>Floating rate</dt><dd>{d.definitions.floating_rate}</dd></>}
            {m.notes.length > 0 && <><dt>Notes</dt><dd>{m.notes.join("; ")}</dd></>}
          </dl>
          <details className="hint" style={{ marginTop: 8 }}>
            <summary>Source records</summary>
            {Object.entries(d.sources).map(([k, ids]) => (
              <div key={k} style={{ marginTop: 4 }}><code>{k}</code> ({ids.length}): {ids.join(", ") || "none"}</div>
            ))}
            <div style={{ marginTop: 6 }}>Payload SHA-256 <code>{d.payload_sha256}</code></div>
          </details>
        </section>
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
