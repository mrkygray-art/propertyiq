"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import Help from "@/components/Help";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import { PROPERTY_TYPES, screening, type GuidelineSet } from "@/lib/screening";

type RuleKey = "min_dscr" | "max_ltv" | "min_debt_yield" | "max_break_even" | "min_loan" | "max_loan";

const RULES: { key: RuleKey; label: string; unit: string; help: string }[] = [
  { key: "min_dscr", label: "Minimum DSCR", unit: "×", help: "The deal fails if NOI covers the loan payments fewer times than this. Example: 1.25." },
  { key: "max_ltv", label: "Maximum LTV", unit: "%", help: "The deal fails if the loan is a bigger share of the value than this. Example: 70%." },
  { key: "min_debt_yield", label: "Minimum debt yield", unit: "%", help: "The deal fails if NOI divided by the loan is below this. Example: 8%." },
  { key: "max_break_even", label: "Maximum break-even occupancy", unit: "%", help: "The deal fails if the property must be fuller than this to pay its costs and the loan. Example: 85%." },
  { key: "min_loan", label: "Minimum loan size", unit: "$", help: "Smallest loan you make. Leave off for no limit." },
  { key: "max_loan", label: "Maximum loan size", unit: "$", help: "Largest loan you make. Leave off for no limit." },
];

const BLANK: GuidelineSet = {
  set_id: 0, name: "", dscr_basis: "amortizing", allowed_types_enabled: false, allowed_types: PROPERTY_TYPES.map(([k]) => k),
  min_dscr: { enabled: true, threshold: 1.25 }, max_ltv: { enabled: true, threshold: 70 },
  min_debt_yield: { enabled: true, threshold: 8 }, max_break_even: { enabled: true, threshold: 85 },
  min_loan: { enabled: false, threshold: null }, max_loan: { enabled: false, threshold: null },
};

function Editor() {
  const [sets, setSets] = useState<GuidelineSet[]>([]);
  const [draft, setDraft] = useState<GuidelineSet | null>(null);
  const [texts, setTexts] = useState<Record<RuleKey, string>>({} as Record<RuleKey, string>);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = (selectId?: number) => screening.guidelines().then((s) => {
    setSets(s);
    const pick = s.find((x) => x.set_id === selectId) ?? s[0];
    if (pick) choose(pick);
  }).catch((e) => setMessage({ kind: "error", text: e instanceof ApiError ? e.message : "The API is unreachable." }));

  useEffect(() => { load(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  function choose(g: GuidelineSet) {
    setDraft(structuredClone(g));
    setTexts(Object.fromEntries(RULES.map((r) => [r.key, g[r.key].threshold === null ? "" : String(g[r.key].threshold)])) as Record<RuleKey, string>);
    setMessage(null);
  }

  if (!draft) return <div className="empty">Loading…</div>;

  const parsed = Object.fromEntries(RULES.map((r) => {
    const t = texts[r.key]?.replace(/[$,%\s]/g, "") ?? "";
    return [r.key, t === "" ? null : Number(t)];
  })) as Record<RuleKey, number | null>;
  const invalid = RULES.filter((r) => parsed[r.key] !== null && (!Number.isFinite(parsed[r.key]) || (parsed[r.key] as number) < 0));

  async function save(asNew: boolean) {
    if (!draft) return;
    if (!draft.name.trim()) { setMessage({ kind: "error", text: "Give the guideline set a name." }); return; }
    if (invalid.length) { setMessage({ kind: "error", text: `Check: ${invalid.map((r) => r.label).join(", ")}.` }); return; }
    setBusy(true);
    try {
      const body = { ...draft, ...Object.fromEntries(RULES.map((r) => [r.key, { enabled: draft[r.key].enabled, threshold: parsed[r.key] }])) };
      if (asNew && draft.set_id && sets.some((s) => s.name === body.name.trim())) body.name = `${body.name.trim()} (copy)`;
      const saved = await screening.saveGuidelines(asNew ? { ...body, set_id: undefined } : body);
      await load(saved.set_id);
      setMessage({ kind: "ok", text: `Saved "${saved.name}".` });
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof ApiError ? e.message : "Save failed." });
    } finally { setBusy(false); }
  }

  async function remove() {
    if (!draft?.set_id || !window.confirm(`Delete the guideline set "${draft.name}"?`)) return;
    setBusy(true);
    try { await screening.deleteGuidelines(draft.set_id); await load(); setMessage({ kind: "ok", text: "Deleted." }); }
    catch (e) { setMessage({ kind: "error", text: e instanceof ApiError ? e.message : "Delete failed." }); }
    finally { setBusy(false); }
  }

  const toggleType = (t: string) => setDraft({
    ...draft, allowed_types: draft.allowed_types.includes(t) ? draft.allowed_types.filter((x) => x !== t) : [...draft.allowed_types, t],
  });

  return (
    <>
      <div className="breadcrumb"><Link href="/screening">← Loan screening</Link></div>
      <div className="page-head">
        <div>
          <h1>Lender guidelines</h1>
          <div className="sub">Your own rules, applied to every deal. The starting values are examples only, not industry standards. Set your own.</div>
        </div>
      </div>
      {message && <div className={message.kind === "error" ? "error" : "scr-overall PASS"} role="status" style={{ marginBottom: 16 }}>{message.text}</div>}
      <div className="scr-layout">
        <section className="card">
          <div className="card-head"><h2>Guideline sets</h2></div>
          <div className="scr-sets">
            {sets.map((s) => (
              <button key={s.set_id} className={`scr-set${s.set_id === draft.set_id ? " active" : ""}`} onClick={() => choose(s)}>{s.name}</button>
            ))}
            <button className="scr-set" onClick={() => choose({ ...structuredClone(BLANK), name: "New guideline set" })}>+ New set</button>
          </div>
        </section>
        <section className="card">
          <div className="scr-field wide" style={{ marginBottom: 12 }}>
            <label htmlFor="gname">Name</label>
            <input id="gname" type="text" maxLength={80} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </div>
          {RULES.map((r) => (
            <div className="scr-rule" key={r.key}>
              <input type="checkbox" aria-label={`Turn ${r.label} on`} checked={draft[r.key].enabled}
                onChange={(e) => setDraft({ ...draft, [r.key]: { ...draft[r.key], enabled: e.target.checked } })} />
              <span style={{ display: "flex", gap: 4, alignItems: "center", opacity: draft[r.key].enabled ? 1 : 0.6 }}>{r.label}<Help text={r.help} /></span>
              <div className={r.unit === "$" ? "scr-prefix" : "scr-suffix"}>
                {r.unit === "$" && <span>$</span>}
                <input type="text" inputMode="decimal" aria-label={`${r.label} limit`} value={texts[r.key] ?? ""} disabled={!draft[r.key].enabled}
                  placeholder={r.unit === "$" ? "no limit" : ""} onChange={(e) => setTexts({ ...texts, [r.key]: e.target.value })} />
                {r.unit !== "$" && <span>{r.unit}</span>}
              </div>
            </div>
          ))}
          <div className="scr-rule" style={{ gridTemplateColumns: "22px minmax(0, 1fr)" }}>
            <input type="checkbox" aria-label="Limit property types" checked={draft.allowed_types_enabled}
              onChange={(e) => setDraft({ ...draft, allowed_types_enabled: e.target.checked })} />
            <span style={{ display: "flex", gap: 4, alignItems: "center" }}>Only these property types<Help text="Turn on to fail deals for property types you don't lend on." /></span>
          </div>
          <div className="scr-types" style={{ opacity: draft.allowed_types_enabled ? 1 : 0.5 }}>
            {PROPERTY_TYPES.map(([k, v]) => (
              <label key={k}><input type="checkbox" disabled={!draft.allowed_types_enabled} checked={draft.allowed_types.includes(k)} onChange={() => toggleType(k)} />{v}</label>
            ))}
          </div>
          <div className="scr-rule" style={{ gridTemplateColumns: "minmax(0, 1fr) 200px", marginTop: 6 }}>
            <span style={{ display: "flex", gap: 4, alignItems: "center" }}>DSCR used for pass/fail
              <Help text="When a loan has an interest-only period, payments are lower at first. Amortizing (the default) tests the full payment; interest-only tests the lower one." /></span>
            <select value={draft.dscr_basis} onChange={(e) => setDraft({ ...draft, dscr_basis: e.target.value as GuidelineSet["dscr_basis"] })}>
              <option value="amortizing">Amortizing payment</option>
              <option value="interest_only">Interest-only payment</option>
            </select>
          </div>
          <div className="scr-actions" style={{ marginTop: 16 }}>
            <button className="btn" disabled={busy} onClick={() => save(!draft.set_id)}>{draft.set_id ? "Save changes" : "Create set"}</button>
            {draft.set_id > 0 && <button className="btn secondary" disabled={busy} onClick={() => save(true)}>Save as new set</button>}
            {draft.set_id > 0 && sets.length > 1 && <button className="btn secondary" disabled={busy} onClick={remove}>Delete</button>}
          </div>
        </section>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Shell>{() => <Editor />}</Shell>
    </Suspense>
  );
}
