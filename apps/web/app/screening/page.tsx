"use client";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";
import Help from "@/components/Help";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import {
  badNumbers, EMPTY_DEAL, EXAMPLE_DEAL, fmt, fmtRule, PROPERTY_TYPES, screening,
  type DealForm, type GuidelineSet, type ScreenResult, type Status,
} from "@/lib/screening";

const cls = (s: Status | undefined) => (s === "NOT CHECKED" || !s ? "NOT" : s);

const HELP = {
  value: "The value the loan is measured against: the purchase price or an appraised value. PropertyIQ doesn't estimate it.",
  pgi: "Potential gross income: the yearly rent if every unit or square foot were leased.",
  other_income: "Yearly income besides rent, such as parking, laundry or fees.",
  vacancy: "The share of income lost to empty space and tenants who don't pay.",
  opex: "Yearly running costs: taxes, insurance, utilities, repairs, management and payroll. Not loan payments.",
  reserves: "Money set aside each year for big future repairs like roofs or boilers.",
  loan: "How much the borrower wants to borrow.",
  rate: "The yearly interest rate on the loan.",
  amort: "How many years the payments are spread over to pay the loan off completely. 30 is common.",
  io: "Years at the start when the borrower pays only interest, not principal. 0 if none.",
  term: "When the loan comes due. Informational only here.",
  egi: "Effective gross income: income actually collected after vacancy and non-payment.",
  noi: "Net operating income: what the property earns after running costs, before loan payments.",
  payment: "The standard monthly payment that pays the loan off over the amortization period.",
  ads: "All the loan payments in one year (12 monthly payments).",
  io_ds: "Yearly payments during the interest-only period: interest only, no principal.",
  dscr: "Debt service coverage ratio: how many times NOI covers the loan payments. 1.00 means just enough; lenders want a cushion.",
  dscr_io: "DSCR during the interest-only period, when payments are lower.",
  ltv: "Loan-to-value: the share of the property's value that is borrowed.",
  dy: "Debt yield: NOI as a percentage of the loan. The lender's return if it had to take the property back.",
  cap: "Implied cap rate: NOI as a percentage of the value. The property's yield at that price.",
  be: "Break-even occupancy: how full the property must be to pay all costs and the loan. Over 100% means it can't.",
  dc: "Debt constant: one year of loan payments as a percentage of the loan.",
};

function Field({ label, help, value, onChange, prefix, suffix, wide, placeholder }: {
  label: string; help?: string; value: string; onChange: (v: string) => void;
  prefix?: string; suffix?: string; wide?: boolean; placeholder?: string;
}) {
  const id = label.toLowerCase().replace(/[^a-z]+/g, "-");
  const input = <input id={id} type="text" inputMode={wide ? "text" : "decimal"} value={value} placeholder={placeholder}
    onChange={(e) => onChange(e.target.value)} autoComplete="off" />;
  return (
    <div className={`scr-field${wide ? " wide" : ""}`}>
      <label htmlFor={id}>{label}{help && <Help text={help} />}</label>
      {prefix ? <div className="scr-prefix"><span>{prefix}</span>{input}</div>
        : suffix ? <div className="scr-suffix">{input}<span>{suffix}</span></div> : input}
    </div>
  );
}

function Metric({ label, help, value, flag }: { label: string; help: string; value: string; flag?: boolean }) {
  return (
    <div className={`scr-metric${flag ? " flag" : ""}`}>
      <div className="label">{label}<Help text={help} /></div>
      <div className="value">{value}</div>
    </div>
  );
}

function Screening() {
  const [form, setForm] = useState<DealForm>(EXAMPLE_DEAL);
  const [sets, setSets] = useState<GuidelineSet[]>([]);
  const [setId, setSetId] = useState<number | null>(null);
  const [result, setResult] = useState<ScreenResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "pdf" | "xlsx">("");
  const seq = useRef(0);

  useEffect(() => {
    screening.guidelines().then((s) => { setSets(s); setSetId((cur) => cur ?? s[0]?.set_id ?? null); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."));
  }, []);

  const localErrors = badNumbers(form);

  // Recalculate shortly after typing stops. Every number comes from the API's calculation engine.
  useEffect(() => {
    if (setId === null || localErrors.length) return;
    const mine = ++seq.current;
    const t = setTimeout(() => {
      screening.evaluate(form, setId)
        .then((r) => { if (mine === seq.current) { setResult(r); setError(null); } })
        .catch((e) => { if (mine === seq.current) setError(e instanceof ApiError ? e.message : "The API is unreachable."); });
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form, setId, localErrors.length]);

  const set = (k: keyof DealForm) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const m = result?.valid ? result.metrics : undefined;
  const errors = localErrors.length ? localErrors : result && !result.valid ? result.errors : [];

  async function exportFile(kind: "pdf" | "xlsx") {
    setBusy(kind);
    try { await screening.download(kind, form, setId); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Export failed."); }
    finally { setBusy(""); }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Loan screening</h1>
          <div className="sub">Pre-screen a loan request against your own guidelines. Screening aid only: not an appraisal or a credit approval.</div>
        </div>
        <div className="filters">
          <label className="filters">
            Guidelines
            <select value={setId ?? ""} onChange={(e) => setSetId(Number(e.target.value))} aria-label="Guideline set">
              {sets.map((s) => <option key={s.set_id} value={s.set_id}>{s.name}</option>)}
            </select>
          </label>
          <Link href="/screening/guidelines">Edit guidelines</Link>
        </div>
      </div>

      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="scr-layout">
        <form className="card scr-form" onSubmit={(e) => e.preventDefault()} aria-label="Deal inputs">
          <div className="scr-actions">
            <button type="button" className="btn secondary" onClick={() => setForm(EXAMPLE_DEAL)}>Load example</button>
            <button type="button" className="btn secondary" onClick={() => { setForm(EMPTY_DEAL); setResult(null); }}>Clear</button>
          </div>
          <fieldset>
            <legend>Property</legend>
            <Field label="Name or address" value={form.property_name} onChange={set("property_name")} wide />
            <div className="scr-field">
              <label htmlFor="ptype">Property type</label>
              <select id="ptype" value={form.property_type} onChange={(e) => set("property_type")(e.target.value)}>
                {PROPERTY_TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="scr-field">
              <label htmlFor="size">Size (optional)</label>
              <div style={{ display: "flex", gap: 6 }}>
                <input id="size" type="text" inputMode="decimal" value={form.size} onChange={(e) => set("size")(e.target.value)} />
                <select aria-label="Size unit" value={form.size_unit} onChange={(e) => set("size_unit")(e.target.value)} style={{ width: 70 }}>
                  <option value="units">units</option><option value="sf">sq ft</option>
                </select>
              </div>
            </div>
            <Field label="Value used for screening" help={HELP.value} value={form.value} onChange={set("value")} prefix="$" />
          </fieldset>
          <fieldset>
            <legend>Income and expenses (per year)</legend>
            <Field label="Potential gross income" help={HELP.pgi} value={form.pgi} onChange={set("pgi")} prefix="$" />
            <Field label="Other income" help={HELP.other_income} value={form.other_income} onChange={set("other_income")} prefix="$" />
            <Field label="Vacancy and credit loss" help={HELP.vacancy} value={form.vacancy_pct} onChange={set("vacancy_pct")} suffix="%" />
            <Field label="Operating expenses" help={HELP.opex} value={form.operating_expenses} onChange={set("operating_expenses")} prefix="$" />
            <Field label="Replacement reserves" help={HELP.reserves} value={form.reserves} onChange={set("reserves")} prefix="$" />
          </fieldset>
          <fieldset>
            <legend>Loan request</legend>
            <Field label="Loan amount" help={HELP.loan} value={form.loan_amount} onChange={set("loan_amount")} prefix="$" />
            <Field label="Interest rate" help={HELP.rate} value={form.interest_rate_pct} onChange={set("interest_rate_pct")} suffix="%" />
            <Field label="Amortization" help={HELP.amort} value={form.amortization_years} onChange={set("amortization_years")} suffix="yrs" />
            <Field label="Interest-only period" help={HELP.io} value={form.interest_only_years} onChange={set("interest_only_years")} suffix="yrs" />
            <Field label="Loan term" help={HELP.term} value={form.term_years} onChange={set("term_years")} suffix="yrs" />
          </fieldset>
          <div className="scr-actions">
            <button type="button" className="btn" disabled={!m || !!busy} onClick={() => exportFile("pdf")}>{busy === "pdf" ? "Preparing…" : "Export PDF"}</button>
            <button type="button" className="btn" disabled={!m || !!busy} onClick={() => exportFile("xlsx")}>{busy === "xlsx" ? "Preparing…" : "Export Excel"}</button>
          </div>
        </form>

        <div className="stack" aria-live="polite">
          {errors.length > 0 && (
            <section className="card">
              <h2 style={{ marginBottom: 8 }}>Fix these inputs</h2>
              <ul style={{ margin: 0, paddingLeft: 18 }}>{errors.map((e) => <li key={e}>{e}</li>)}</ul>
            </section>
          )}

          {m && result && (
            <>
              <div className={`scr-overall ${cls(result.overall)}`}>
                <span className="big">{result.overall}</span>
                <span className="sub">
                  {result.overall === "PASS" ? "Meets every guideline that was checked" : result.overall === "FAIL"
                    ? `Misses: ${result.rules?.filter((r) => r.status === "FAIL").map((r) => r.label).join(", ")}`
                    : "Nothing could be checked yet"}
                  {" · "}{result.guideline_set.name}
                </span>
              </div>

              {result.warnings && result.warnings.length > 0 && (
                <div className="scr-warnings">{result.warnings.map((w) => <div key={w} className="scr-warning">⚠ {w}</div>)}</div>
              )}

              <section className="card">
                <div className="card-head"><h2>Underwriting numbers</h2><span className="sub">Calculated by PropertyIQ&apos;s engine; rounded for display only</span></div>
                <div className="scr-metrics">
                  <Metric label="Effective gross income" help={HELP.egi} value={fmt.usd(m.egi)} />
                  <Metric label="Net operating income" help={HELP.noi} value={fmt.usd(m.noi)} flag={m.noi <= 0} />
                  <Metric label="Monthly payment" help={HELP.payment} value={fmt.usd(m.monthly_payment)} />
                  <Metric label="Annual debt service" help={HELP.ads} value={fmt.usd(m.annual_debt_service)} />
                  {m.interest_only_debt_service !== null && (
                    <Metric label="Interest-only debt service" help={HELP.io_ds} value={fmt.usd(m.interest_only_debt_service)} />
                  )}
                  <Metric label="DSCR" help={HELP.dscr} value={fmt.ratio(m.dscr)} flag={m.dscr !== null && m.dscr < 1} />
                  {m.dscr_interest_only !== null && <Metric label="DSCR (interest-only)" help={HELP.dscr_io} value={fmt.ratio(m.dscr_interest_only)} />}
                  <Metric label="Loan-to-value (LTV)" help={HELP.ltv} value={fmt.pct(m.ltv)} />
                  <Metric label="Debt yield" help={HELP.dy} value={fmt.pct(m.debt_yield)} flag={m.debt_yield !== null && m.debt_yield <= 0} />
                  <Metric label="Cap rate (implied)" help={HELP.cap} value={fmt.pct(m.cap_rate, 2)} />
                  <Metric label="Break-even occupancy" help={HELP.be} value={fmt.pct(m.break_even_occupancy)}
                    flag={m.break_even_occupancy !== null && m.break_even_occupancy > 1} />
                  <Metric label="Debt constant" help={HELP.dc} value={fmt.pct(m.debt_constant, 2)} />
                </div>
              </section>

              <section className="card">
                <div className="card-head">
                  <h2>Guideline checks</h2>
                  <span className="sub">{result.guideline_set.name} · DSCR basis: {result.guideline_set.dscr_basis.replace("_", "-")}</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Guideline</th><th className="num">Actual</th><th className="num">Limit</th><th>Result</th></tr></thead>
                    <tbody>
                      {result.rules?.map((r) => (
                        <tr key={r.rule_id}>
                          <td>{r.label}</td>
                          <td className="num">{fmtRule(r.actual, r.kind)}</td>
                          <td className="num">{fmtRule(r.threshold, r.kind)}</td>
                          <td><span className={`scr-status ${cls(r.status)}`}>{r.status === "PASS" ? "✓ " : r.status === "FAIL" ? "✗ " : ""}{r.status}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="card">
                <div className="card-head"><h2>Stress tests</h2><span className="sub">The same deal under worse conditions</span></div>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Scenario</th><th className="num">NOI</th><th className="num">Debt service</th><th className="num">DSCR</th>
                      <th className="num">LTV</th><th className="num">Debt yield</th><th>Result</th></tr></thead>
                    <tbody>
                      {result.stress?.map((s) => (
                        <tr key={s.key}>
                          <td>{s.label}{s.failed.length > 0 && <div className="muted" style={{ fontSize: 12 }}>Misses: {s.failed.join(", ")}</div>}</td>
                          <td className="num">{fmt.usd(s.noi)}</td>
                          <td className="num">{fmt.usd(s.annual_debt_service)}</td>
                          <td className="num">{fmt.ratio(s.dscr)}</td>
                          <td className="num">{fmt.pct(s.ltv)}</td>
                          <td className="num">{fmt.pct(s.debt_yield)}</td>
                          <td><span className={`scr-status ${cls(s.overall)}`}>{s.overall}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {result.grid && (
                <section className="card">
                  <div className="card-head">
                    <h2>DSCR by interest rate and vacancy</h2>
                    <span className="sub">{result.grid.min_dscr !== null ? `Red cells are below the ${fmt.ratio(result.grid.min_dscr)} minimum. ` : ""}Outlined cell is the deal as entered. Hover a cell for the exact value.</span>
                  </div>
                  <div className="table-wrap">
                    <table className="scr-grid">
                      <thead>
                        <tr><th>Rate ↓ · Vacancy →</th>{result.grid.vacancies.map((v) => (
                          <th key={v} className={`num${Math.abs(v - result.grid!.base_vacancy) < 1e-9 ? " base" : ""}`}>{fmt.pct(v)}</th>))}</tr>
                      </thead>
                      <tbody>
                        {result.grid.rates.map((rate, i) => (
                          <tr key={rate}>
                            <th>{fmt.pct(rate, 2)}</th>
                            {result.grid!.dscr[i].map((v, j) => {
                              const below = v !== null && result.grid!.min_dscr !== null && v < result.grid!.min_dscr;
                              const base = Math.abs(rate - result.grid!.base_rate) < 1e-9 && Math.abs(result.grid!.vacancies[j] - result.grid!.base_vacancy) < 1e-9;
                              // Hover shows the unrounded value: 1.2496 displays as 1.25 but is still below a 1.25 minimum.
                              return <td key={j} className={`num${below ? " below" : ""}${base ? " base" : ""}`}
                                title={v === null ? undefined : `Exact DSCR ${v.toFixed(4)}${below ? " (below the minimum)" : ""}`}>{fmt.ratio(v)}</td>;
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </>
          )}

          {result && (
            <section className="card">
              <div className="card-head"><h2>Assumptions used</h2><span className="sub">Every input behind these results</span></div>
              <div className="scr-assumptions">
                {result.assumptions.map((a) => <div key={a.label}><span>{a.label}</span><span>{a.value}</span></div>)}
              </div>
            </section>
          )}

          <div className="scr-disclaimer">{result?.disclaimer ?? "Screening aid only. Not an appraisal, valuation opinion, or credit approval."}</div>
        </div>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Shell>{() => <Screening />}</Shell>
    </Suspense>
  );
}
