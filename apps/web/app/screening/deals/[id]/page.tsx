"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useCallback, useEffect, useState } from "react";
import Help from "@/components/Help";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import {
  monthLabel, pctf, usd, ws, type Analysis, type Category, type Deal, type Doc, type Flag, type Item,
} from "@/lib/workspace";

const STEPS = [
  { key: "upload", label: "Upload" }, { key: "review", label: "Review" }, { key: "flags", label: "Flags" },
  { key: "market", label: "Market", later: "Milestone 6" }, { key: "california", label: "California", later: "Milestone 7" },
  { key: "compare", label: "Compare", later: "Milestone 8" }, { key: "export", label: "Export", later: "Milestone 9" },
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
      <dl className="kv" style={{ margin: "6px 0" }}>{f.evidence.map((e, i) => <Fragment key={i}><dt>{e.label}</dt><dd>{e.value}</dd></Fragment>)}</dl>
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
      <div className="breadcrumb"><Link href="/screening/deals">← Deals</Link></div>
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
    </>
  );
}

export default function Page() {
  return <Suspense fallback={null}><Shell>{() => <Workspace />}</Shell></Suspense>;
}
