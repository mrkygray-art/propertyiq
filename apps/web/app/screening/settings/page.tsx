"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import Help from "@/components/Help";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import { settingsApi, type Benchmark, type LifeRow, type Rule, type Setting } from "@/lib/workspace";

const TABS = [["benchmarks", "Expense benchmarks"], ["rules", "California rules"], ["life", "Component life"], ["other", "Other settings"]] as const;
const errText = (e: unknown) => (e instanceof ApiError ? e.message : "The API is unreachable.");
const BASIS: Record<string, string> = { per_unit: "$ per unit / year", per_sf: "$ per sq ft / year", pct_egi: "fraction of income (0.04 = 4%)" };

function Status({ msg }: { msg: { ok: boolean; text: string } | null }) {
  return msg ? <div className={msg.ok ? "scr-overall PASS" : "error"} role="status" style={{ marginBottom: 12, padding: "8px 12px" }}>{msg.text}</div> : null;
}

function Benchmarks() {
  const [rows, setRows] = useState<Benchmark[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { settingsApi.benchmarks().then(setRows).catch((e) => setMsg({ ok: false, text: errText(e) })); }, []);
  const edit = (i: number, k: keyof Benchmark, v: unknown) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  async function save(r: Benchmark) {
    try { setRows(await settingsApi.saveBenchmark(r)); setMsg({ ok: true, text: `Saved ${r.property_type} ${r.category.replaceAll("_", " ")}.` }); }
    catch (e) { setMsg({ ok: false, text: errText(e) }); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Expense benchmarks</h2>
        <span className="sub">Used by flags A4 (expense below benchmark) and A5 (management fee). The shipped values are examples only. Replace them with your own standards and sources.</span></div>
      <Status msg={msg} />
      <div className="table-wrap"><table className="review">
        <thead><tr><th>Type</th><th>Category</th><th>Basis</th><th className="num">Low</th><th className="num">Typical</th><th className="num">High</th><th>Source</th><th>Reviewed</th><th>Example?</th><th /></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={r.benchmark_id} className={r.is_example ? "row-low" : ""}>
            <td>{r.property_type}</td><td>{r.category.replaceAll("_", " ")}</td><td className="muted" style={{ fontSize: 12 }}>{BASIS[r.basis]}</td>
            {(["low", "typical", "high"] as const).map((k) => (
              <td key={k} className="num"><input className="cell-input" value={r[k] ?? ""} onChange={(e) => edit(i, k, e.target.value === "" ? null : e.target.value)} /></td>))}
            <td><input type="text" value={r.source} onChange={(e) => edit(i, "source", e.target.value)} style={{ width: 230, fontSize: 12 }} /></td>
            <td><input type="date" value={r.last_reviewed} onChange={(e) => edit(i, "last_reviewed", e.target.value)} style={{ fontSize: 12 }} /></td>
            <td><input type="checkbox" checked={r.is_example} onChange={(e) => edit(i, "is_example", e.target.checked)} aria-label="Example placeholder" /></td>
            <td><button className="linkbtn" onClick={() => save(r)}>Save</button></td>
          </tr>))}</tbody>
      </table></div>
    </section>
  );
}

function Rules() {
  const [rows, setRows] = useState<Rule[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { settingsApi.rules().then(setRows).catch((e) => setMsg({ ok: false, text: errText(e) })); }, []);
  const edit = (key: string, k: keyof Rule, v: unknown) => setRows(rows.map((r) => (r.rule_key === key ? { ...r, [k]: v } : r)));
  async function save(r: Rule) {
    try { setRows(await settingsApi.saveRule(r)); setMsg({ ok: true, text: `Saved "${r.name}".` }); }
    catch (e) { setMsg({ ok: false, text: errText(e) }); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>California red-flag rules</h2>
        <span className="sub">Jurisdiction rules change. Each rule shows its official source and when it was last checked. UNVERIFIED rules must be confirmed before relying on them. These are screening flags, not legal advice.</span></div>
      <Status msg={msg} />
      <div className="stack">{rows.map((r) => (
        <div key={r.rule_key} className={`flag ${r.verified ? "sev-low" : "sev-medium"}`}>
          <div className="flag-head">
            <span className={`sev ${r.verified ? "sev-low" : "sev-medium"}`}>{r.verified ? `Verified ${r.last_verified}` : "UNVERIFIED"}</span>
            <b>{r.flag_id}</b> {r.name} <span className="muted" style={{ fontSize: 12 }}>· {r.jurisdiction}</span>
            <button className="linkbtn" style={{ marginLeft: "auto" }} onClick={() => setOpen(open === r.rule_key ? null : r.rule_key)}>{open === r.rule_key ? "Close" : "Edit"}</button>
          </div>
          {open !== r.rule_key ? (
            <>
              <p style={{ margin: "6px 0" }}>{r.description}</p>
              <div className="muted" style={{ fontSize: 12 }}>Source: {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer noopener">{r.source_url}</a> : "none"}{r.notes && ` · ${r.notes}`}</div>
            </>
          ) : (
            <div className="stack" style={{ gap: 8, marginTop: 8 }}>
              <label className="sub">Plain-language description<textarea value={r.description} onChange={(e) => edit(r.rule_key, "description", e.target.value)} style={{ width: "100%", minHeight: 80 }} /></label>
              <label className="sub">Official source URL<input type="text" value={r.source_url ?? ""} onChange={(e) => edit(r.rule_key, "source_url", e.target.value)} style={{ width: "100%" }} /></label>
              <div className="scr-actions">
                <label className="sub">Last verified <input type="date" value={r.last_verified ?? ""} onChange={(e) => edit(r.rule_key, "last_verified", e.target.value || null)} /></label>
                <label className="sub"><input type="checkbox" checked={r.verified} onChange={(e) => edit(r.rule_key, "verified", e.target.checked)} /> I checked this against the source</label>
              </div>
              <label className="sub">Trigger (JSON) <Help text="The conditions that make the flag fire, such as city names, a year-built cutoff, or construction type. Change with care." />
                <textarea value={JSON.stringify(r.trigger, null, 1)} onChange={(e) => { try { edit(r.rule_key, "trigger", JSON.parse(e.target.value)); } catch { /* wait for valid JSON */ } }}
                  style={{ width: "100%", minHeight: 60, fontFamily: "var(--mono)", fontSize: 12 }} /></label>
              <label className="sub">Notes<input type="text" value={r.notes} onChange={(e) => edit(r.rule_key, "notes", e.target.value)} style={{ width: "100%" }} /></label>
              <div><button className="btn" onClick={() => save(r)}>Save rule</button></div>
            </div>
          )}
        </div>))}</div>
    </section>
  );
}

function Life() {
  const [rows, setRows] = useState<LifeRow[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { settingsApi.life().then(setRows).catch((e) => setMsg({ ok: false, text: errText(e) })); }, []);
  const edit = (i: number, k: keyof LifeRow, v: unknown) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  async function save(r: LifeRow) {
    try { setRows(await settingsApi.saveLife(r)); setMsg({ ok: true, text: `Saved ${r.label}.` }); } catch (e) { setMsg({ ok: false, text: errText(e) }); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Component typical life</h2>
        <span className="sub">Used by flag R5 (deferred maintenance). An item at or past its typical life is flagged; &quot;flag if&quot; marks a material or brand that&apos;s flagged at any age. Example values only.</span></div>
      <Status msg={msg} />
      <div className="table-wrap"><table className="review">
        <thead><tr><th>Component</th><th className="num">Typical life (years)</th><th>Flag if (words)</th><th>Source</th><th>Reviewed</th><th>Example?</th><th /></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={r.item_key} className={r.is_example ? "row-low" : ""}>
            <td>{r.label}</td>
            <td className="num"><input className="cell-input" value={r.typical_life ?? ""} onChange={(e) => edit(i, "typical_life", e.target.value === "" ? null : Number(e.target.value.replace(/\D/g, "")))} /></td>
            <td><input type="text" value={r.flag_if ?? ""} onChange={(e) => edit(i, "flag_if", e.target.value)} style={{ width: 170, fontSize: 12 }} /></td>
            <td><input type="text" value={r.source} onChange={(e) => edit(i, "source", e.target.value)} style={{ width: 250, fontSize: 12 }} /></td>
            <td><input type="date" value={r.last_reviewed} onChange={(e) => edit(i, "last_reviewed", e.target.value)} style={{ fontSize: 12 }} /></td>
            <td><input type="checkbox" checked={r.is_example} onChange={(e) => edit(i, "is_example", e.target.checked)} aria-label="Example placeholder" /></td>
            <td><button className="linkbtn" onClick={() => save(r)}>Save</button></td>
          </tr>))}</tbody>
      </table></div>
    </section>
  );
}

function Other() {
  const [rows, setRows] = useState<Record<string, Setting>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { settingsApi.settings().then(setRows).catch((e) => setMsg({ ok: false, text: errText(e) })); }, []);
  async function save(key: string) {
    try { setRows(await settingsApi.saveSetting(key, rows[key].value)); setMsg({ ok: true, text: "Saved." }); } catch (e) { setMsg({ ok: false, text: errText(e) }); }
  }
  return (
    <section className="card">
      <div className="card-head"><h2>Other settings</h2></div>
      <Status msg={msg} />
      <div className="stack" style={{ gap: 8 }}>{Object.values(rows).map((s) => (
        <div key={s.key} className="scr-rule" style={{ gridTemplateColumns: "minmax(0, 1fr) 140px 60px" }}>
          <span>{s.description}</span>
          <input type="text" value={s.value} onChange={(e) => setRows({ ...rows, [s.key]: { ...s, value: e.target.value } })} />
          <button className="linkbtn" onClick={() => save(s.key)}>Save</button>
        </div>))}</div>
    </section>
  );
}

function Settings() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("benchmarks");
  return (
    <>
      <div className="breadcrumb"><Link href="/screening/deals">← Deal workspace</Link></div>
      <div className="page-head"><div><h1>Screening settings</h1>
        <div className="sub">Reference data behind the flags. Every value shows its source and review date; examples stay labeled until you replace them.</div></div></div>
      <nav className="steps" aria-label="Settings sections">
        {TABS.map(([k, label]) => <button key={k} className={`step${tab === k ? " active" : ""}`} onClick={() => setTab(k)}>{label}</button>)}
      </nav>
      {tab === "benchmarks" && <Benchmarks />}
      {tab === "rules" && <Rules />}
      {tab === "life" && <Life />}
      {tab === "other" && <Other />}
    </>
  );
}

export default function Page() {
  return <Suspense fallback={null}><Shell>{() => <Settings />}</Shell></Suspense>;
}
