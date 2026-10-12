"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useCallback, useEffect, useState } from "react";
import Help from "@/components/Help";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import {
  compareApi, marketApi, monthLabel, pctf, usd, ws, type Adjustment, type Analysis, type CaliforniaData, type CaseReport,
  type Category, type Comparison, type ConditionItem, type Deal, type Doc, type Flag, type Item, type MarketResult,
} from "@/lib/workspace";
import { screening } from "@/lib/screening";

const STEPS: { key: string; label: string; later?: string }[] = [
  { key: "upload", label: "Upload" }, { key: "review", label: "Review" }, { key: "flags", label: "Flags" },
  { key: "market", label: "Market" }, { key: "california", label: "California" },
  { key: "compare", label: "Compare" }, { key: "export", label: "Export" },
];

const GLOSSARY = {
  rentRoll: "A list of every unit with its status (occupied or vacant), the rent, and lease dates.",
  t12: "Trailing 12 months: the property's income and expenses for the last 12 months, month by month.",
  occupancy: "Physical occupancy: units that are actually occupied divided by total units.",
  ltl: "Loss to lease: how far below market current tenants' rents are.",
  gpr: "Gross potential rent: current rent on occupied units plus market rent on vacant ones.",
  nonRecurring: "Money that won't repeat, such as an insurance payout. It's left out of recurring income.",
  t3: "The last 3 months (T-3) or 6 months (T-6), annualized, to show which way things are heading.",
  confidence: "How sure the reader is. Low means check it: OCR misreads, unrecognized labels, or AI-suggested categories.",
};

function errText(e: unknown) { return e instanceof ApiError ? e.message : "The API is unreachable."; }

// ---------------------------------------------------------------- Upload

function UploadStep({ deal, onChange, openReview }: { deal: Deal; onChange: () => void; openReview: (id: number) => void }) {
  const [kind, setKind] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true); setError(null);
    try {
      let last: Doc | null = null;
      for (const f of Array.from(files)) last = await ws.upload(deal.deal_id, f, kind);
      onChange();
      if (last && last.status !== "failed") openReview(last.document_id);
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  async function remove(id: number, name: string) {
    if (!window.confirm(`Delete "${name}" and everything read from it?`)) return;
    try { await ws.deleteDocument(id); onChange(); } catch (e) { setError(errText(e)); }
  }

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Upload documents</h2><span className="sub">Excel (.xlsx, .xls), CSV, or PDF (text or scanned) up to 10 MB</span></div>
        <div className="scr-actions" style={{ alignItems: "center" }}>
          <label className="filters">This file is
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="auto">detect automatically</option><option value="rent_roll">a rent roll</option><option value="t12">a T-12</option>
            </select>
          </label>
          <label className="btn" style={{ cursor: busy ? "wait" : "pointer" }}>
            {busy ? "Reading…" : "Choose files"}
            <input type="file" multiple hidden disabled={busy} accept=".xlsx,.xls,.xlsm,.csv,.pdf"
              onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
          </label>
          <Help text={`${GLOSSARY.rentRoll} ${GLOSSARY.t12}`} />
        </div>
        <p className="hint">Tenant names and contact details are dropped as the file is read. They're never stored, shown, exported, or sent to the AI. Scanned PDFs take about 10 seconds a page.</p>
        {error && <div className="error" role="alert">{error}</div>}
      </section>
      <section className="card">
        <div className="card-head"><h2>Documents</h2></div>
        {deal.documents.length === 0 ? <div className="empty">Nothing uploaded yet.</div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>File</th><th>Type</th><th>Read with</th><th>Status</th><th /></tr></thead>
            <tbody>{deal.documents.map((d) => (
              <tr key={d.document_id}>
                <td>{d.filename}{d.error && <div className="scr-warning" style={{ marginTop: 4 }}>{d.error}</div>}</td>
                <td>{d.status === "failed" ? "—" : d.kind === "rent_roll" ? "Rent roll" : "T-12"}</td>
                <td>{({ excel: "Excel", csv: "CSV", pdf_text: "PDF text", pdf_ocr: "OCR (scanned)" } as Record<string, string>)[d.extraction_method ?? ""] ?? "—"}</td>
                <td><span className={`scr-status ${d.status === "approved" ? "PASS" : d.status === "failed" ? "FAIL" : "NOT"}`}>
                  {d.status === "approved" ? "✓ Approved" : d.status === "failed" ? "Couldn't read" : "Needs review"}</span></td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {d.status !== "failed" && <button className="linkbtn" onClick={() => openReview(d.document_id)}>Review</button>}{" "}
                  <button className="linkbtn" onClick={() => remove(d.document_id, d.filename)}>Delete</button>
                </td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- Review

function ConfBadge({ item }: { item: Item }) {
  const note = [item.confidence_note, item.mapped_by === "ai" ? "Category suggested by the local AI." : ""].filter(Boolean).join(" ");
  return <span className={`conf conf-${item.confidence}`} title={note || undefined}>{item.confidence}{item.edited ? " · edited" : ""}</span>;
}

function Money({ value, onSave, disabled }: { value: unknown; onSave: (v: string) => void; disabled: boolean }) {
  const [v, setV] = useState(value === null || value === undefined ? "" : String(value));
  useEffect(() => { setV(value === null || value === undefined ? "" : String(value)); }, [value]);
  return <input className="cell-input" type="text" inputMode="decimal" value={v} disabled={disabled}
    onChange={(e) => setV(e.target.value)} onBlur={() => { if (v !== String(value ?? "")) onSave(v.replace(/[$,\s]/g, "")); }} />;
}

function ReviewStep({ docId, categories, onChange }: { docId: number; categories: Category[]; onChange: () => void }) {
  const [doc, setDoc] = useState<Doc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyLow, setOnlyLow] = useState(false);

  useEffect(() => { ws.document(docId).then(setDoc).catch((e) => setError(errText(e))); }, [docId]);
  if (error) return <div className="error">{error}</div>;
  if (!doc) return <div className="empty">Loading…</div>;
  const locked = doc.status === "approved";

  async function save(item: Item, body: { category?: string; data?: Record<string, unknown> }) {
    try { setDoc(await ws.editItem(doc!.document_id, item.item_id, body)); } catch (e) { setError(errText(e)); }
  }
  async function approve(on: boolean) {
    try { setDoc(on ? await ws.approve(doc!.document_id) : await ws.unapprove(doc!.document_id)); setError(null); onChange(); }
    catch (e) { setError(errText(e)); }
  }

  const items = doc.items.filter((i) => !onlyLow || i.confidence !== "high");
  const lowCount = doc.items.filter((i) => i.confidence === "low").length;
  const s = doc.summary;

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <h2>{doc.kind === "rent_roll" ? "Rent roll" : "T-12"}: {doc.filename}</h2>
          <span className="sub">{doc.items.length} {doc.kind === "rent_roll" ? "units" : "lines"} · {lowCount} low confidence <Help text={GLOSSARY.confidence} /></span>
        </div>
        {(s.warnings?.length || s.missing_months?.length || s.duplicate_months?.length || s.reconciliation?.length || s.totals_check) ? (
          <div className="scr-warnings" style={{ marginBottom: 12 }}>
            {s.missing_months?.length ? <div className="scr-warning">Missing month(s): {s.missing_months.map(monthLabel).join(", ")}</div> : null}
            {s.duplicate_months?.length ? <div className="scr-warning">Duplicated month(s): {s.duplicate_months.map(monthLabel).join(", ")}</div> : null}
            {s.reconciliation?.filter((r) => r.kind !== "subtotal_month").map((r, i) => <div key={i} className="scr-warning">Doesn&apos;t add up: {r.detail} <span className="muted">({r.source})</span></div>)}
            {s.totals_check && <div className="scr-warning">Rent roll totals row: {s.totals_check}</div>}
            {s.warnings?.map((w, i) => <div key={`w${i}`} className="scr-warning">{w}</div>)}
          </div>
        ) : null}
        <div className="scr-actions" style={{ alignItems: "center" }}>
          {locked
            ? <><span className="scr-status PASS">✓ Approved: these figures are used in the analysis</span><button className="btn secondary" onClick={() => approve(false)}>Un-approve to edit</button></>
            : <><button className="btn" onClick={() => approve(true)}>Approve these figures</button><span className="sub">Nothing from this file is used until you approve it.</span></>}
          <label className="filters" style={{ marginLeft: "auto" }}><input type="checkbox" checked={onlyLow} onChange={(e) => setOnlyLow(e.target.checked)} /> Show only rows to check</label>
        </div>
      </section>

      <section className="card">
        <div className="table-wrap">
          {doc.kind === "rent_roll" ? (
            <table className="review">
              <thead><tr><th>Unit</th><th>Type</th><th className="num">Bd/Ba</th><th className="num">Sq ft</th><th>Status</th>
                <th className="num">Rent</th><th className="num">Market</th><th>Lease end</th><th>Confidence</th><th>Source</th></tr></thead>
              <tbody>{items.map((it) => {
                const d = it.data as Record<string, string | number | boolean | null>;
                return (
                  <tr key={it.item_id} className={`row-${it.confidence}`}>
                    <td>{String(d.unit_id)}</td><td>{String(d.unit_type ?? "—")}</td>
                    <td className="num">{d.beds ?? "—"}/{d.baths ?? "—"}</td><td className="num">{d.sqft ?? "—"}</td>
                    <td><select value={String(d.status ?? "")} disabled={locked} onChange={(e) => save(it, { data: { status: e.target.value || null } })}>
                      <option value="">(not recognized)</option>
                      {["occupied", "vacant", "model", "down", "employee"].map((x) => <option key={x} value={x}>{x}</option>)}
                    </select></td>
                    <td className="num"><Money value={d.current_rent} disabled={locked} onSave={(v) => save(it, { data: { current_rent: v || null } })} /></td>
                    <td className="num"><Money value={d.market_rent} disabled={locked} onSave={(v) => save(it, { data: { market_rent: v || null } })} /></td>
                    <td>{d.month_to_month ? "Month-to-month" : String(d.lease_end ?? "—")}</td>
                    <td><ConfBadge item={it} />{it.confidence_note && <div className="muted" style={{ fontSize: 11 }}>{it.confidence_note}</div>}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{it.source_ref}</td>
                  </tr>);
              })}</tbody>
            </table>
          ) : (
            <table className="review">
              <thead><tr><th>Original label</th><th>Category</th>
                {(s.months ?? []).map((m) => <th key={m} className="num">{monthLabel(m)}</th>)}<th className="num">Total</th><th>Confidence</th><th>Source</th></tr></thead>
              <tbody>{items.map((it) => {
                const d = it.data as { months: Record<string, string | null>; reported_total: string | null };
                const sub = ["total_income", "total_expenses", "net_operating_income"].includes(it.category ?? "");
                return (
                  <tr key={it.item_id} className={`row-${it.confidence}${sub ? " subtotal" : ""}`}>
                    <td>{it.original_label}</td>
                    <td><select value={it.category ?? "unmapped"} disabled={locked} onChange={(e) => save(it, { category: e.target.value })}
                      title={categories.find((c) => c.key === it.category)?.meaning}>
                      {["income", "expense", "below_line", "subtotal", "unknown"].map((sec) => (
                        <optgroup key={sec} label={{ income: "Income", expense: "Operating expenses", below_line: "Below the line (not in NOI)", subtotal: "Totals (checks only)", unknown: "Needs a choice" }[sec]}>
                          {categories.filter((c) => c.section === sec).map((c) => <option key={c.key} value={c.key}>{c.key.replaceAll("_", " ")}</option>)}
                        </optgroup>))}
                    </select></td>
                    {(s.months ?? []).map((m) => (
                      <td key={m} className="num"><Money value={d.months[m]} disabled={locked || sub}
                        onSave={(v) => save(it, { data: { months: { ...d.months, [m]: v || null } } })} /></td>))}
                    <td className="num">{usd(d.reported_total)}</td>
                    <td><ConfBadge item={it} /></td>
                    <td className="muted" style={{ fontSize: 11 }}>{it.source_ref}</td>
                  </tr>);
              })}</tbody>
            </table>
          )}
        </div>
        {error && <div className="error" role="alert" style={{ marginTop: 12 }}>{error}</div>}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- Flags

function FlagCard({ f, onChange }: { f: Flag; onChange: () => void }) {
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function set(status: "open" | "dismissed") {
    try { await ws.flagStatus(f.flag_id, status, status === "dismissed" ? note : undefined); onChange(); }
    catch (e) { setError(errText(e)); }
  }
  return (
    <div className={`flag sev-${f.severity}${f.status === "dismissed" ? " dismissed" : ""}`}>
      <div className="flag-head">
        <span className={`sev sev-${f.severity}`}>{f.severity}</span>
        <b>{f.rule_id}</b> {f.title}
        {f.status === "dismissed" && <span className="scr-status NOT" style={{ marginLeft: 8 }}>Dismissed</span>}
      </div>
      <p style={{ margin: "6px 0" }}>{f.explanation}</p>
      <dl className="kv" style={{ margin: "6px 0" }}>{f.evidence.map((e, i) => <Fragment key={i}><dt>{e.label}</dt><dd>{/^https?:\/\//.test(e.value) ? <a href={e.value} target="_blank" rel="noreferrer noopener">{e.value}</a> : e.value}</dd></Fragment>)}</dl>
      <div className="sub"><b>Suggested action:</b> {f.suggested_action}</div>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Source: {f.sources.join(" · ")}</div>
      {f.status === "dismissed" ? (
        <div style={{ marginTop: 8 }}><span className="sub">Note: {f.dismissal_note}</span> <button className="linkbtn" onClick={() => set("open")}>Reopen</button></div>
      ) : open ? (
        <div className="scr-actions" style={{ marginTop: 8 }}>
          <input type="text" placeholder="Why is this OK? (required)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
          <button className="btn secondary" onClick={() => set("dismissed")} disabled={!note.trim()}>Dismiss</button>
          <button className="linkbtn" onClick={() => setOpen(false)}>Cancel</button>
        </div>
      ) : <button className="linkbtn" style={{ marginTop: 8 }} onClick={() => setOpen(true)}>Dismiss with a note</button>}
      {error && <div className="error" style={{ marginTop: 6 }}>{error}</div>}
    </div>
  );
}

function FlagsStep({ dealId }: { dealId: number }) {
  const [a, setA] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => ws.analysis(dealId).then(setA).catch((e) => setError(errText(e))), [dealId]);
  useEffect(() => { load(); }, [load]);
  if (error) return <div className="error">{error}</div>;
  if (!a) return <div className="empty">Running checks…</div>;
  const rr = a.rent_roll, t = a.t12;
  const open = a.flags.filter((f) => f.status === "open");
  const count = (s: string) => open.filter((f) => f.severity === s).length;
  return (
    <div className="stack">
      {!rr && !t && <div className="card empty">Approve a rent roll and/or a T-12 on the Review step to run the checks.</div>}
      {(rr || t) && (
        <div className="kpis">
          {rr && <div className="kpi"><div className="label">Physical occupancy <Help text={GLOSSARY.occupancy} /></div><div className="value">{pctf(rr.physical_occupancy)}</div><div className="note">{rr.occupied_count} of {rr.unit_count} units</div></div>}
          {rr && <div className="kpi"><div className="label">In-place rent (annual) <Help text={GLOSSARY.gpr} /></div><div className="value">{usd(rr.in_place_annual)}</div><div className="note">Loss to lease {usd(rr.loss_to_lease_annual)}/yr <Help text={GLOSSARY.ltl} /></div></div>}
          {t && <div className="kpi"><div className="label">T-12 NOI (recurring) <Help text={GLOSSARY.nonRecurring} /></div><div className="value">{usd(t.noi)}</div><div className="note">{t.months_present} months{t.months_present < 12 ? ", annualized" : ""} · one-time {usd(t.non_recurring_income)}</div></div>}
          {t && <div className="kpi"><div className="label">T-12 vacancy / expense ratio <Help text={GLOSSARY.t3} /></div><div className="value">{pctf(t.reported_vacancy_pct)}</div><div className="note">Expenses {pctf(t.expense_ratio)} of income · T-3 income {usd(t.t3_income)}</div></div>}
        </div>
      )}
      <section className="card">
        <div className="card-head">
          <h2>Flags</h2>
          <span className="sub">{count("high")} high · {count("medium")} medium · {count("low")} low open. Suggestions only: nothing changes the numbers until an adjustment is approved.</span>
        </div>
        {a.flags.length === 0 ? <div className="empty">{rr || t ? "No flags. The checks found nothing unusual." : "No data approved yet."}</div>
          : <div className="stack">{a.flags.map((f) => <FlagCard key={f.flag_id} f={f} onChange={load} />)}</div>}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- Market

type RentRow = {
  unit_type: string; n: number; median: number | null; low: number | null; high: number | null;
  subject_in_place: number | null; subject_market: number | null;
  subject_in_place_vs_median_pct: number | null; subject_market_vs_median_pct: number | null;
};
type SaleSummary = {
  n: number; price_per_unit: { median: number | null; low: number | null; high: number | null };
  cap_rate: { median: number | null; n: number; note: string }; date_range: string[] | null;
};
const diffPct = (v: number | null) => (v === null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)}%`);

function MarketHeader({ r, onRefresh, busy }: { r: MarketResult; onRefresh: () => void; busy: boolean }) {
  return (
    <div className="sub" style={{ marginBottom: 8 }}>
      {r.result.synthetic && <span className="sev sev-medium">Synthetic demo data</span>}
      <b>{r.result.provider_label}</b> · retrieved {new Date(r.result.retrieved_at).toLocaleString()}{r.result.cached ? " (cached)" : ""}
      {r.result.stale && <span className="sev sev-high" style={{ marginLeft: 6 }}>Stale: older than {r.result.stale_days} days</span>}
      {" · "}{r.result.area} · {r.result.comps.length} comps{" "}
      <button className="linkbtn" onClick={onRefresh} disabled={busy}>Refresh</button>
      {r.result.notes.map((n, i) => <div key={i}>{n}</div>)}
    </div>
  );
}

function MarketStep({ deal, onChange }: { deal: Deal; onChange: () => void }) {
  const [address, setAddress] = useState(String(deal.property_facts.address ?? ""));
  const [city, setCity] = useState(String(deal.property_facts.city ?? ""));
  const [radius, setRadius] = useState("1");
  const [age, setAge] = useState("12");
  const [res, setRes] = useState<{ rent?: MarketResult; sale?: MarketResult }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function get(kind: "rent" | "sale", refresh = false) {
    setBusy(true); setError(null);
    try {
      const r = await marketApi.comps(deal.deal_id, { kind, address, city, radius_miles: Number(radius) || 1, max_age_months: Number(age) || 12, refresh });
      setRes((x) => ({ ...x, [kind]: r })); onChange();
    } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  const sale = res.sale;
  const saleSummary = sale?.summary as SaleSummary | undefined;
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Market and comps</h2><span className="sub">Comps support your judgment. They are not an appraisal.</span></div>
        <div className="scr-actions" style={{ alignItems: "end" }}>
          <label className="sub">Address<br /><input type="text" value={address} onChange={(e) => setAddress(e.target.value)} style={{ width: 240 }} /></label>
          <label className="sub">City<br /><input type="text" value={city} onChange={(e) => setCity(e.target.value)} style={{ width: 150 }} /></label>
          <label className="sub">Radius (miles)<br /><input type="text" inputMode="decimal" value={radius} onChange={(e) => setRadius(e.target.value)} style={{ width: 80 }} /></label>
          <label className="sub">Max age (months)<br /><input type="text" inputMode="numeric" value={age} onChange={(e) => setAge(e.target.value)} style={{ width: 80 }} /></label>
          <button className="btn" disabled={busy || !address || !city} onClick={() => get("rent")}>Rent comps</button>
          <button className="btn" disabled={busy || !address || !city} onClick={() => get("sale")}>Sale comps</button>
        </div>
        <p className="hint">This prototype uses an offline synthetic provider (fictional comps for Los Angeles, Santa Monica, Long Beach, Torrance and Pasadena). A real provider can be added later; it would be the only feature that needs the internet.</p>
        {error && <div className="error">{error}</div>}
      </section>

      {res.rent && (
        <section className="card">
          <div className="card-head"><h2>Rent comps</h2></div>
          <MarketHeader r={res.rent} onRefresh={() => get("rent", true)} busy={busy} />
          {res.rent.result.comps.length === 0 ? <div className="empty">No comps available.</div> : (<>
            <div className="table-wrap"><table>
              <thead><tr><th>Unit type</th><th className="num">Comps</th><th className="num">Median</th><th className="num">Range</th>
                <th className="num">Subject in-place</th><th className="num">vs median</th><th className="num">Subject market</th><th className="num">vs median</th></tr></thead>
              <tbody>{(res.rent.summary as RentRow[]).map((r) => (
                <tr key={r.unit_type}><td>{r.unit_type}</td><td className="num">{r.n}</td><td className="num">{usd(r.median)}</td>
                  <td className="num">{r.n ? `${usd(r.low)} – ${usd(r.high)}` : "—"}</td>
                  <td className="num">{usd(r.subject_in_place)}</td><td className="num">{diffPct(r.subject_in_place_vs_median_pct)}</td>
                  <td className="num">{usd(r.subject_market)}</td><td className="num">{diffPct(r.subject_market_vs_median_pct)}</td></tr>))}</tbody>
            </table></div>
            <details style={{ marginTop: 8 }}><summary>All {res.rent.result.comps.length} rent comps</summary>
              <div className="table-wrap"><table>
                <thead><tr><th>Source ID</th><th>Address</th><th>Type</th><th className="num">Sq ft</th><th className="num">Rent</th><th className="num">$/sq ft</th><th className="num">Miles</th><th>Listed</th></tr></thead>
                <tbody>{res.rent.result.comps.map((c) => (
                  <tr key={c.source_id}><td className="mono">{c.source_id}</td><td>{c.address}</td><td>{String(c.unit_type)}</td><td className="num">{String(c.sqft)}</td>
                    <td className="num">{usd(c.rent)}</td><td className="num">${Number(c.rent_per_sqft).toFixed(2)}</td><td className="num">{c.distance_miles}</td><td>{String(c.listed)}</td></tr>))}</tbody>
              </table></div></details>
          </>)}
        </section>)}

      {sale && saleSummary && (
        <section className="card">
          <div className="card-head"><h2>Sale comps</h2></div>
          <MarketHeader r={sale} onRefresh={() => get("sale", true)} busy={busy} />
          {sale.result.comps.length === 0 ? <div className="empty">No comps available.</div> : (<>
            <div className="kpis">
              <div className="kpi"><div className="label">Comp median price / unit</div><div className="value">{usd(saleSummary.price_per_unit.median)}</div><div className="note">{usd(saleSummary.price_per_unit.low)} – {usd(saleSummary.price_per_unit.high)} · {saleSummary.n} sales</div></div>
              <div className="kpi"><div className="label">This deal: price / unit</div><div className="value">{usd(sale.implied?.price_per_unit)}</div><div className="note">{usd(sale.implied?.price)} for {sale.implied?.units ?? "?"} units</div></div>
              <div className="kpi"><div className="label">Comp median cap rate</div><div className="value">{pctf(saleSummary.cap_rate.median, 2)}</div><div className="note">{saleSummary.cap_rate.note}</div></div>
              <div className="kpi"><div className="label">This deal: implied cap rate</div><div className="value">{pctf(sale.implied?.cap_rate, 2)}</div><div className="note">Approved T-12 NOI {usd(sale.implied?.noi)}</div></div>
            </div>
            <div className="table-wrap" style={{ marginTop: 12 }}><table>
              <thead><tr><th>Source ID</th><th>Address</th><th>Sold</th><th className="num">Price</th><th className="num">Units</th><th className="num">$/unit</th><th className="num">$/sq ft</th><th className="num">Built</th><th className="num">Cap rate</th></tr></thead>
              <tbody>{sale.result.comps.map((c) => (
                <tr key={c.source_id}><td className="mono">{c.source_id}</td><td>{c.address}</td><td>{String(c.sale_date)}</td><td className="num">{usd(c.price)}</td>
                  <td className="num">{String(c.units)}</td><td className="num">{usd(c.price_per_unit)}</td><td className="num">${Number(c.price_per_sqft).toFixed(0)}</td>
                  <td className="num">{String(c.year_built)}</td><td className="num">{c.cap_rate === null ? "not available" : `${(Number(c.cap_rate) * 100).toFixed(2)}% (reported by source)`}</td></tr>))}</tbody>
            </table></div>
            <p className="hint">Large gaps between the deal and the comps raise flag M1 on the Flags step.</p>
          </>)}
        </section>)}
    </div>
  );
}

// ---------------------------------------------------------------- California

const CONSTRUCTION = [["wood_frame", "Wood frame"], ["concrete", "Concrete"], ["masonry", "Masonry (brick/block)"], ["steel", "Steel"]];
const PARKING = [["tuck_under", "Tuck-under (open, under the building)"], ["open_ground_floor", "Open ground floor"], ["surface", "Surface lot"], ["subterranean", "Underground garage"], ["none", "None"]];
const SITE = [["gas_station", "Gas station"], ["dry_cleaner", "Dry cleaner"], ["auto_repair", "Auto repair"], ["industrial", "Industrial use"]];
const R_NAMES: Record<string, string> = { R1: "Prop 13 property tax", R2: "Rent control", R3: "Seismic", R4: "Environmental", R5: "Deferred maintenance" };

function CaliforniaStep({ deal, onChange }: { deal: Deal; onChange: () => void }) {
  const [data, setData] = useState<CaliforniaData | null>(null);
  const [facts, setFacts] = useState<Record<string, unknown>>({});
  const [cond, setCond] = useState<Record<string, ConditionItem>>({});
  const [flags, setFlags] = useState<Flag[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => marketApi.california(deal.deal_id).then((d) => {
    setData(d); setFacts(d.facts); setFlags(d.flags);
    setCond(Object.fromEntries(d.condition.map((c) => [c.item_key, c])));
  }).catch((e) => setError(errText(e))), [deal.deal_id]);
  useEffect(() => { load(); }, [load]);
  if (!data) return error ? <div className="error">{error}</div> : <div className="empty">Loading…</div>;

  const f = (k: string) => (facts[k] === undefined || facts[k] === null ? "" : String(facts[k]));
  const setF = (k: string, v: unknown) => setFacts({ ...facts, [k]: v });
  const num = (v: string) => (v === "" ? null : Number(v.replace(/[$,\s]/g, "")));
  const site = (facts.site_history ?? {}) as Record<string, boolean>;
  const setCondField = (key: string, field: string, v: string) => {
    const c = cond[key] ?? { item_key: key, details: {}, estimated_cost: null };
    setCond({ ...cond, [key]: field === "estimated_cost" ? { ...c, estimated_cost: num(v) } : { ...c, details: { ...c.details, [field]: v } } });
  };

  async function run() {
    setBusy(true); setError(null);
    try { const r = await marketApi.saveCalifornia(deal.deal_id, facts, Object.values(cond)); setFlags(r.flags); onChange(); }
    catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Property facts</h2><span className="sub">These drive the California red flags. Screening flags only: never legal advice.</span></div>
        <div className="grid-2">
          <div className="stack" style={{ gap: 8 }}>
            <div className="scr-field"><label>City</label><input type="text" value={f("city")} onChange={(e) => setF("city", e.target.value)} /></div>
            <div className="scr-field"><label>Year built <Help text="Used as a stand-in for the certificate of occupancy date. For rent control, confirm the actual certificate date." /></label><input type="text" inputMode="numeric" value={f("year_built")} onChange={(e) => setF("year_built", num(e.target.value))} /></div>
            <div className="scr-field"><label>Units</label><input type="text" inputMode="numeric" value={f("units")} onChange={(e) => setF("units", num(e.target.value))} /></div>
            <div className="scr-field"><label>Stories</label><input type="text" inputMode="numeric" value={f("stories")} onChange={(e) => setF("stories", num(e.target.value))} /></div>
          </div>
          <div className="stack" style={{ gap: 8 }}>
            <div className="scr-field"><label>Construction</label><select value={f("construction")} onChange={(e) => setF("construction", e.target.value)}><option value="">—</option>{CONSTRUCTION.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            <div className="scr-field"><label>Parking <Help text="Soft-story: a building whose ground floor, often open tuck-under parking, is much weaker than the floors above and can collapse in an earthquake." /></label><select value={f("parking")} onChange={(e) => setF("parking", e.target.value)}><option value="">—</option>{PARKING.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
            <div className="scr-field"><label>Transaction <Help text="Prop 13: in California a sale generally resets the taxable value to roughly the purchase price, so the buyer's tax bill can be much higher. A plain refinance doesn't." /></label><select value={f("transaction")} onChange={(e) => setF("transaction", e.target.value)}><option value="">—</option><option value="purchase">Purchase</option><option value="refinance">Refinance</option></select></div>
            <div className="scr-field"><label>Purchase price</label><div className="scr-prefix"><span>$</span><input type="text" inputMode="decimal" value={f("purchase_price")} onChange={(e) => setF("purchase_price", num(e.target.value))} /></div></div>
          </div>
        </div>
        <div className="filters" style={{ marginTop: 10 }}>Past or neighbouring use <Help text="Gas stations, dry cleaners, auto repair and industrial sites can leave contamination. A Phase I ESA is a standard environmental report on a property's history and contamination risk." />
          {SITE.map(([k, v]) => <label key={k}><input type="checkbox" checked={!!site[k]} onChange={(e) => setF("site_history", { ...site, [k]: e.target.checked })} /> {v}</label>)}</div>
      </section>

      <section className="card">
        <div className="card-head"><h2>Property condition checklist</h2><span className="sub">Ages and materials you know; leave the rest blank. Your cost estimates add up to a deferred-maintenance total.</span></div>
        <div className="table-wrap"><table className="review">
          <thead><tr><th>Component</th><th className="num">Age (years)</th><th>Type / material / brand</th><th className="num">Estimated cost</th><th>Typical life</th></tr></thead>
          <tbody>{data.life_table.map((l) => {
            const c = cond[l.item_key];
            return (
              <tr key={l.item_key}><td>{l.label}</td>
                <td className="num"><input className="cell-input" value={String(c?.details.age_years ?? "")} onChange={(e) => setCondField(l.item_key, "age_years", e.target.value.replace(/\D/g, ""))} /></td>
                <td><input type="text" value={String(c?.details.type ?? "")} placeholder={l.flag_if ? `e.g. ${l.flag_if.split("|")[0]}` : ""} onChange={(e) => setCondField(l.item_key, "type", e.target.value)} style={{ width: 200, fontSize: 12 }} /></td>
                <td className="num"><input className="cell-input" value={c?.estimated_cost ?? ""} onChange={(e) => setCondField(l.item_key, "estimated_cost", e.target.value)} /></td>
                <td className="muted" style={{ fontSize: 12 }}>{l.typical_life ? `${l.typical_life} yrs` : "flag if present"}{l.is_example ? " (example)" : ""}</td></tr>);
          })}</tbody>
        </table></div>
        <div className="scr-actions" style={{ marginTop: 12 }}><button className="btn" onClick={run} disabled={busy}>{busy ? "Checking…" : "Save and run California checks"}</button>
          <Link href="/screening/settings" className="sub">Rules and typical-life settings</Link></div>
        {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
      </section>

      {flags && (
        <section className="card">
          <div className="card-head"><h2>California red flags</h2>
            <span className="sub">{flags.filter((x) => x.status === "open").length} open · {["R1", "R2", "R3", "R4", "R5"].map((g) => `${g} ${flags.filter((x) => x.rule_id === g && x.status === "open").length}`).join(" · ")}</span></div>
          {flags.length === 0 ? <div className="empty">Save the facts above to run the checks.</div> : (
            <div className="stack">{["R1", "R2", "R3", "R4", "R5"].map((g) => {
              const fs = flags.filter((x) => x.rule_id === g);
              return fs.length ? <div key={g} className="stack" style={{ gap: 8 }}><h3>{g} · {R_NAMES[g]}</h3>{fs.map((x) => <FlagCard key={x.flag_id} f={x} onChange={load} />)}</div> : null;
            })}</div>)}
        </section>)}
    </div>
  );
}

// ---------------------------------------------------------------- Compare

const fmtLine = (kind: "usd" | "pct", v: number | null | undefined) => (kind === "pct" ? pctf(v ?? null) : usd(v));
const fmtDelta = (kind: "usd" | "pct", v: number) =>
  Math.abs(v) < 1e-9 ? "" : kind === "pct" ? `${v > 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts` : `${v > 0 ? "+" : "−"}${usd(Math.abs(v))}`;
const CASE_ROWS: [string, string, "usd" | "ratio" | "pct"][] = [
  ["Effective gross income", "egi", "usd"], ["Net operating income", "noi", "usd"], ["Annual debt service", "annual_debt_service", "usd"],
  ["DSCR", "dscr", "ratio"], ["LTV", "ltv", "pct"], ["Debt yield", "debt_yield", "pct"], ["Break-even occupancy", "break_even_occupancy", "pct"],
];
const LOAN_FIELDS: [string, string, string][] = [
  ["value", "Value used for screening", "$"], ["loan_amount", "Loan amount", "$"], ["interest_rate_pct", "Interest rate", "%"],
  ["amortization_years", "Amortization", "yrs"], ["interest_only_years", "Interest-only period", "yrs"], ["term_years", "Loan term", "yrs"],
];

function AdjustmentRow({ a, kind, label, onChange }: { a: Adjustment; kind: "usd" | "pct"; label: string; onChange: () => void }) {
  const [value, setValue] = useState(a.underwritten_value === null ? "" : kind === "pct" ? String(+(a.underwritten_value * 100).toFixed(4)) : String(Math.round(a.underwritten_value)));
  const [note, setNote] = useState(a.note);
  const [error, setError] = useState<string | null>(null);
  const parsed = () => { const n = Number(value.replace(/[$,%\s]/g, "")); return kind === "pct" ? n / 100 : n; };
  async function set(status: Adjustment["status"]) {
    try { await compareApi.patch(a.adjustment_id, { status, note, underwritten_value: parsed() }); onChange(); } catch (e) { setError(errText(e)); }
  }
  return (
    <tr className={a.status === "approved" ? "" : a.status === "rejected" ? "row-muted" : "row-low"}>
      <td>{label}<div className="muted" style={{ fontSize: 11 }}>{a.reason_code}{a.flag_fingerprint ? ` · from flag ${a.flag_fingerprint.split(":")[0]}` : " · manual"}</div></td>
      <td className="num">{fmtLine(kind, a.borrower_value)}</td>
      <td className="num"><input className="cell-input" value={value} onChange={(e) => setValue(e.target.value)} disabled={a.status === "approved"} />{kind === "pct" ? " %" : ""}</td>
      <td><input type="text" value={note} onChange={(e) => setNote(e.target.value)} disabled={a.status === "approved"} style={{ width: "100%", minWidth: 220, fontSize: 12 }} /></td>
      <td style={{ whiteSpace: "nowrap" }}>
        <span className={`scr-status ${a.status === "approved" ? "PASS" : a.status === "rejected" ? "FAIL" : "NOT"}`}>{a.status}</span>{" "}
        {a.status !== "approved" && <button className="linkbtn" onClick={() => set("approved")} disabled={!note.trim()}>Approve</button>}{" "}
        {a.status !== "rejected" && <button className="linkbtn" onClick={() => set("rejected")}>Reject</button>}{" "}
        {a.status !== "proposed" && <button className="linkbtn" onClick={() => set("proposed")}>Undo</button>}
        {error && <div className="error" style={{ fontSize: 12 }}>{error}</div>}
      </td>
    </tr>
  );
}

function CompareStep({ deal, onChange }: { deal: Deal; onChange: () => void }) {
  const [c, setC] = useState<Comparison | null>(null);
  const [loan, setLoan] = useState<Record<string, string>>({});
  const [sets, setSets] = useState<{ set_id: number; name: string }[]>([]);
  const [setId, setSetId] = useState<number | null>(null);
  const [newAdj, setNewAdj] = useState({ line_item: "payroll", value: "", reason_code: "OTHER", note: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const apply = (x: Comparison) => {
    setC(x); setSetId(x.guideline_set_id);
    setLoan(Object.fromEntries(Object.entries(x.loan).map(([k, v]) => [k, v === null || v === undefined ? "" : String(v)])));
  };
  const load = useCallback(() => compareApi.get(deal.deal_id).then(apply).catch((e) => setError(errText(e))), [deal.deal_id]);
  useEffect(() => { load(); screening.guidelines().then(setSets).catch(() => undefined); }, [load]);
  if (!c) return error ? <div className="error">{error}</div> : <div className="empty">Loading…</div>;

  async function saveLoan() {
    const body = Object.fromEntries(Object.entries(loan).map(([k, v]) => [k, v === "" ? null : Number(v.replace(/[$,%\s]/g, ""))]));
    try { apply(await compareApi.saveLoan(deal.deal_id, body, setId)); setError(null); } catch (e) { setError(errText(e)); }
  }
  async function suggest() {
    try { const x = await compareApi.suggest(deal.deal_id); apply(x); setMsg(x.added ? `Added ${x.added} suggested adjustment(s) from open flags.` : "No new suggestions: every open flag already has one, or no flag has a numeric fix."); onChange(); }
    catch (e) { setError(errText(e)); }
  }
  async function add() {
    const kind = c!.lines.find((l) => l.key === newAdj.line_item)?.kind ?? "usd";
    const n = Number(newAdj.value.replace(/[$,%\s]/g, ""));
    try {
      apply(await compareApi.add(deal.deal_id, { line_item: newAdj.line_item, underwritten_value: kind === "pct" ? n / 100 : n, reason_code: newAdj.reason_code, note: newAdj.note }));
      setNewAdj({ ...newAdj, value: "", note: "" });
    } catch (e) { setError(errText(e)); }
  }

  const b = c.borrower_case, u = c.underwritten_case;
  const metric = (r: CaseReport, key: string, kind: string) => {
    const v = r.metrics ? (r.metrics as Record<string, number | null>)[key] : null;
    return kind === "ratio" ? (v === null || v === undefined ? "n/a" : v.toFixed(2)) : kind === "pct" ? pctf(v) : usd(v);
  };
  const kindOf = (k: string) => c.lines.find((l) => l.key === k)?.kind ?? "usd";
  const order = { proposed: 0, approved: 1, rejected: 2 } as const;

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Loan request</h2><span className="sub">Used for both cases. Borrower figures come from: {c.source}</span></div>
        <div className="scr-actions" style={{ alignItems: "end" }}>
          {LOAN_FIELDS.map(([k, label, unit]) => (
            <label key={k} className="sub">{label} ({unit})<br /><input type="text" inputMode="decimal" value={loan[k] ?? ""} onChange={(e) => setLoan({ ...loan, [k]: e.target.value })} style={{ width: k === "value" || k === "loan_amount" ? 120 : 70 }} /></label>))}
          <label className="sub">Guidelines<br /><select value={setId ?? ""} onChange={(e) => setSetId(Number(e.target.value))}>{sets.map((s) => <option key={s.set_id} value={s.set_id}>{s.name}</option>)}</select></label>
          <button className="btn" onClick={saveLoan}>Save and recalculate</button>
        </div>
        {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
      </section>

      <div className={`scr-overall ${u.overall === "FAIL" ? "FAIL" : u.overall === "PASS" ? "PASS" : "NOT"}`}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>{c.summary}</span>
      </div>

      <section className="card">
        <div className="card-head"><h2>Results side by side</h2><span className="sub">Both cases run through the Tier 1 calculation engine</span></div>
        <div className="table-wrap"><table>
          <thead><tr><th /><th className="num">Borrower</th><th className="num">Underwritten</th></tr></thead>
          <tbody>
            {CASE_ROWS.map(([label, key, kind]) => <tr key={key}><td>{label}</td><td className="num">{metric(b, key, kind)}</td><td className="num"><b>{metric(u, key, kind)}</b></td></tr>)}
            <tr><td>Guidelines ({u.guideline_set.name})</td>
              <td className="num"><span className={`scr-status ${b.overall === "PASS" ? "PASS" : b.overall === "FAIL" ? "FAIL" : "NOT"}`}>{b.overall ?? "n/a"}</span></td>
              <td className="num"><span className={`scr-status ${u.overall === "PASS" ? "PASS" : u.overall === "FAIL" ? "FAIL" : "NOT"}`}>{u.overall ?? "n/a"}</span></td></tr>
          </tbody>
        </table></div>
        {u.rules && <div className="sub" style={{ marginTop: 8 }}>Underwritten: {u.rules.filter((r) => r.status !== "NOT CHECKED").map((r) => r.message).join(" · ")}</div>}
      </section>

      <section className="card">
        <div className="card-head"><h2>Borrower vs underwritten (annual)</h2><span className="sub">Only approved adjustments change the underwritten column</span></div>
        <div className="table-wrap"><table>
          <thead><tr><th>Line item</th><th className="num">Borrower</th><th className="num">Underwritten</th><th className="num">Change</th><th>Reason</th><th>Note / source</th></tr></thead>
          <tbody>
            {c.lines.map((l) => (
              <tr key={l.key}><td>{l.label}</td><td className="num">{fmtLine(l.kind, l.borrower)}</td><td className="num"><b>{fmtLine(l.kind, l.underwritten)}</b></td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>{fmtDelta(l.kind, l.delta)}</td><td style={{ fontSize: 12 }}>{l.adjustment?.reason_code ?? ""}</td>
                <td className="sub" style={{ fontSize: 12, minWidth: 260 }}>{l.adjustment ? `${l.adjustment.note}${l.adjustment.flag_fingerprint ? ` (flag ${l.adjustment.flag_fingerprint.split(":")[0]})` : ""}` : ""}</td></tr>))}
            {(["egi", "expenses", "reserves", "noi"] as const).map((k) => (
              <tr key={k} className="subtotal"><td><b>{{ egi: "Effective gross income", expenses: "Operating expenses", reserves: "Reserves", noi: "Net operating income" }[k]}</b></td>
                <td className="num">{usd(c.totals.borrower[k])}</td><td className="num"><b>{usd(c.totals.underwritten[k])}</b></td><td className="num">{fmtDelta("usd", c.totals.difference[k])}</td><td /><td /></tr>))}
          </tbody>
        </table></div>
      </section>

      <section className="card">
        <div className="card-head"><h2>Adjustments</h2>
          <span className="sub">Every adjustment needs a reason and a note. Suggestions come from open flags; approve, edit, or reject each one.</span></div>
        <div className="scr-actions" style={{ marginBottom: 10 }}><button className="btn" onClick={suggest}>Suggest adjustments from flags</button>{msg && <span className="sub">{msg}</span>}</div>
        {c.adjustments.length === 0 ? <div className="empty">No adjustments yet.</div> : (
          <div className="table-wrap"><table className="review">
            <thead><tr><th>Line item</th><th className="num">Borrower</th><th className="num">Underwritten</th><th>Note</th><th>Status</th></tr></thead>
            <tbody>{[...c.adjustments].sort((x, y) => order[x.status] - order[y.status]).map((a) => (
              <AdjustmentRow key={`${a.adjustment_id}-${a.status}`} a={a} kind={kindOf(a.line_item)} label={c.line_labels[a.line_item] ?? a.line_item} onChange={load} />))}</tbody>
          </table></div>)}
        <h3 style={{ margin: "14px 0 8px" }}>Add your own adjustment</h3>
        <div className="scr-actions" style={{ alignItems: "end" }}>
          <label className="sub">Line item<br /><select value={newAdj.line_item} onChange={(e) => setNewAdj({ ...newAdj, line_item: e.target.value })}>{c.lines.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}</select></label>
          <label className="sub">Underwritten value{kindOf(newAdj.line_item) === "pct" ? " (%)" : " ($)"}<br /><input type="text" inputMode="decimal" value={newAdj.value} onChange={(e) => setNewAdj({ ...newAdj, value: e.target.value })} style={{ width: 110 }} /></label>
          <label className="sub">Reason<br /><select value={newAdj.reason_code} onChange={(e) => setNewAdj({ ...newAdj, reason_code: e.target.value })}>{c.reason_codes.map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
          <label className="sub" style={{ flex: 1, minWidth: 220 }}>Note (required)<br /><input type="text" value={newAdj.note} onChange={(e) => setNewAdj({ ...newAdj, note: e.target.value })} style={{ width: "100%" }} /></label>
          <button className="btn secondary" onClick={add} disabled={!newAdj.value || !newAdj.note.trim()}>Add</button>
        </div>
      </section>

      {u.stress && u.stress.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>Stress tests: underwritten case</h2></div>
          <div className="table-wrap"><table>
            <thead><tr><th>Scenario</th><th className="num">NOI</th><th className="num">DSCR</th><th className="num">LTV</th><th>Result</th></tr></thead>
            <tbody>{u.stress.map((s) => (
              <tr key={s.key}><td>{s.label}</td><td className="num">{usd(s.noi)}</td><td className="num">{s.dscr === null ? "n/a" : s.dscr.toFixed(2)}</td>
                <td className="num">{pctf(s.ltv)}</td><td><span className={`scr-status ${s.overall === "PASS" ? "PASS" : s.overall === "FAIL" ? "FAIL" : "NOT"}`}>{s.overall}</span></td></tr>))}</tbody>
          </table></div>
        </section>)}
    </div>
  );
}

// ---------------------------------------------------------------- Export

function ExportStep({ deal }: { deal: Deal }) {
  const [busy, setBusy] = useState<"" | "pdf" | "xlsx">("");
  const [error, setError] = useState<string | null>(null);
  async function get(kind: "pdf" | "xlsx") {
    setBusy(kind); setError(null);
    try { await compareApi.download(deal.deal_id, kind); } catch (e) { setError(errText(e)); } finally { setBusy(""); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Export</h2><span className="sub">Screening and underwriting support only. Not an appraisal, legal advice, or a credit approval.</span></div>
      <div className="grid-2">
        <div>
          <h3 style={{ marginBottom: 6 }}>PDF underwriting memo</h3>
          <p className="sub">Summary sentence, borrower vs underwritten table with reasons, results side by side, stress tests, every flag with its evidence and source, market support with sources, and the data-source list. The disclaimer is on every page. Tenant names never appear.</p>
          <button className="btn" onClick={() => get("pdf")} disabled={!!busy}>{busy === "pdf" ? "Preparing…" : "Download PDF"}</button>
        </div>
        <div>
          <h3 style={{ marginBottom: 6 }}>Excel workbook (live formulas)</h3>
          <p className="sub">Borrower, Adjustments, Underwritten, Flags and Comps sheets, plus the Tier 1 calculation sheets. Change an adjustment&apos;s value or status on the Adjustments sheet and the underwritten NOI, DSCR and guideline results recalculate.</p>
          <button className="btn" onClick={() => get("xlsx")} disabled={!!busy}>{busy === "xlsx" ? "Preparing…" : "Download Excel"}</button>
        </div>
      </div>
      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}
    </section>
  );
}

// ---------------------------------------------------------------- page

function Workspace() {
  const { id } = useParams<{ id: string }>();
  const dealId = Number(id);
  const [deal, setDeal] = useState<Deal | null>(null);
  const [cats, setCats] = useState<Category[]>([]);
  const params = useSearchParams();
  const [step, setStep] = useState(params.get("step") ?? "upload");
  const [docId, setDocId] = useState<number | null>(params.get("doc") ? Number(params.get("doc")) : null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => ws.deal(dealId).then(setDeal).catch((e) => setError(errText(e))), [dealId]);
  useEffect(() => { load(); ws.categories().then(setCats).catch(() => undefined); }, [load]);

  if (error) return <><div className="breadcrumb"><Link href="/screening/deals">← Deals</Link></div><div className="error">{error}</div></>;
  if (!deal) return <div className="empty">Loading…</div>;
  const reviewable = deal.documents.filter((d) => d.status !== "failed");
  const current = docId ?? reviewable[reviewable.length - 1]?.document_id ?? null;

  return (
    <>
      <div className="breadcrumb"><Link href="/screening/deals">← Deals</Link> · <Link href="/screening/settings">Screening settings</Link></div>
      <div className="page-head">
        <div><h1>{deal.name}</h1><div className="sub">{String(deal.property_facts.property_type ?? "")}{deal.property_facts.units ? ` · ${deal.property_facts.units} units` : ""} · Screening aid only: not an appraisal, legal advice, or a credit approval</div></div>
      </div>
      <nav className="steps" aria-label="Steps">
        {STEPS.map((s, i) => (
          <button key={s.key} className={`step${step === s.key ? " active" : ""}`} disabled={!!s.later}
            title={s.later ? `Coming in ${s.later}` : undefined} onClick={() => setStep(s.key)}>
            <span className="num">{i + 1}</span>{s.label}{s.later && <span className="muted" style={{ fontSize: 11 }}> (soon)</span>}
          </button>))}
      </nav>
      {step === "upload" && <UploadStep deal={deal} onChange={load} openReview={(d) => { setDocId(d); setStep("review"); }} />}
      {step === "review" && (current === null ? <div className="card empty">Upload a document first.</div> : (
        <>
          {reviewable.length > 1 && (
            <div className="filters" style={{ marginBottom: 12 }}>Document
              <select value={current} onChange={(e) => setDocId(Number(e.target.value))}>
                {reviewable.map((d) => <option key={d.document_id} value={d.document_id}>{d.filename} ({d.status === "approved" ? "approved" : "needs review"})</option>)}
              </select>
            </div>)}
          <ReviewStep key={current} docId={current} categories={cats} onChange={load} />
        </>))}
      {step === "flags" && <FlagsStep dealId={dealId} />}
      {step === "market" && <MarketStep deal={deal} onChange={load} />}
      {step === "california" && <CaliforniaStep deal={deal} onChange={load} />}
      {step === "compare" && <CompareStep deal={deal} onChange={load} />}
      {step === "export" && <ExportStep deal={deal} />}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={null}><Shell>{() => <Workspace />}</Shell></Suspense>;
}
