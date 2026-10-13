"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { ApiError, type User } from "@/lib/api";
import {
  labApi, STAGE_LABEL, type Compare, type Models, type Offline, type Overview, type Schema, type Stage, type Tool,
  type Trace, type TraceRow,
} from "@/lib/lab";

const TABS = [
  ["overview", "Overview"], ["traces", "Request traces"], ["compare", "Model on / off"], ["models", "Models"],
  ["tools", "Tools"], ["schema", "Data schema"], ["offline", "Offline proof"],
] as const;
type Tab = (typeof TABS)[number][0];

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "The API is unreachable.");
const secs = (ms: number | null | undefined) => (ms === null || ms === undefined ? "—" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`);
const when = (iso: string) => new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });

function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setError(null); fn().then(setData).catch((e) => setError(errText(e))); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error };
}

function Loading({ error }: { error: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : <div className="empty">Loading…</div>;
}

function SourceChip({ source }: { source: string }) {
  return source === "local_model"
    ? <span className="chip ok">✓ Local model</span>
    : <span className="chip neutral">Verified summary</span>;
}

// ---------------------------------------------------------------- overview

function FlowStep({ n, title, body, tone }: { n: number; title: string; body: string; tone?: "model" | "check" }) {
  return (
    <li className={`lab-step${tone ? ` ${tone}` : ""}`}>
      <span className="lab-step-n" aria-hidden="true">{n}</span>
      <div><b>{title}</b><div className="muted" style={{ fontSize: 13 }}>{body}</div></div>
    </li>
  );
}

function OverviewTab() {
  const { data, error } = useLoad(labApi.overview);
  if (!data) return <Loading error={error} />;
  const s = data.trace_stats;
  return (
    <div className="stack">
      <div className="kpis">
        <div className="kpi"><div className="label">Traced answers</div><div className="value">{s.total}</div>
          <div className="note">Kept {s.retention_days} days</div></div>
        <div className="kpi"><div className="label">Worded by the local model</div><div className="value">{s.model_answers}</div>
          <div className="note">{s.template_answers} used the verified summary</div></div>
        <div className="kpi"><div className="label">Model drafts rejected</div><div className="value">{s.rejected}</div>
          <div className="note">A figure didn&apos;t match the facts</div></div>
        <div className="kpi"><div className="label">Response time</div><div className="value">{secs(s.p50_ms)}</div>
          <div className="note">median · p95 {secs(s.p95_ms)}</div></div>
      </div>
      <section className="card" aria-labelledby="flow-h">
        <div className="card-head"><h2 id="flow-h">How a chat answer is made</h2>
          <span className="muted" style={{ fontSize: 12 }}>Everything runs on this PC</span></div>
        <ol className="lab-flow">
          <FlowStep n={1} title="Sign-in and scope" body="The API checks the signed-in user's token and loads only the funds they're granted (SQL join on user_portfolio_access)." />
          <FlowStep n={2} title="Deterministic router" body="Plain code (not the model) picks the answer type, or gives a fixed reply for unsupported questions." />
          <FlowStep n={3} title="Vetted function → verified facts" body="A fixed, parameterized query computes the numbers. The model never sees SQL and never does math." />
          <FlowStep n={4} title="Local model words the answer" body={`${data.chat_model} on Ollama at 127.0.0.1. It gets the question, a verified summary and the minimized facts.`} tone="model" />
          <FlowStep n={5} title="Number check" body="Every $ and % in the draft must match the facts; flagged properties must all be named. Otherwise the verified summary is shown." tone="check" />
          <FlowStep n={6} title="Answer with sources" body="Shown with the facts used, the record IDs, and a link to this trace." />
        </ol>
      </section>
      <div className="grid-2">
        <section className="card" aria-labelledby="types-h">
          <div className="card-head"><h2 id="types-h">Answer types</h2></div>
          <table><thead><tr><th>Answer type</th><th>Facts come from</th></tr></thead>
            <tbody>{data.answer_types.map((a) => <tr key={a.intent}><td><code>{a.intent}</code></td><td>{a.facts_from}</td></tr>)}</tbody></table>
        </section>
        <section className="card" aria-labelledby="prompt-h">
          <div className="card-head"><h2 id="prompt-h">What the model is sent</h2></div>
          <p className="hint" style={{ marginTop: 0 }}>{data.trace_policy}</p>
          <details open><summary>Prompt layout</summary><pre className="lab-pre">{data.prompt_template}</pre></details>
          <details><summary>System instructions</summary><pre className="lab-pre">{data.system_prompt}</pre></details>
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- traces

function Waterfall({ stages }: { stages: Stage[] }) {
  const total = stages.reduce((a, s) => a + s.ms, 0) || 1;
  const skip = new Set(["stage", "ms", "tool"]);
  return (
    <ol className="lab-waterfall">
      {stages.map((s, i) => {
        const pctW = Math.max(0.6, (s.ms / total) * 100);
        const detail = Object.entries(s).filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined);
        return (
          <li key={i}>
            <div className="lab-wf-head">
              <span><b>{i + 1}. {STAGE_LABEL[s.stage] ?? s.stage}</b>{s.tool ? <> · <code>{String(s.tool)}</code></> : null}</span>
              <span className="num">{secs(s.ms)}</span>
            </div>
            <div className="lab-wf-track" aria-hidden="true">
              <div className={`lab-wf-bar ${s.stage === "model" ? "model" : ""}`} style={{ width: `${pctW}%` }} />
            </div>
            {detail.length > 0 && (
              <div className="muted" style={{ fontSize: 12 }}>
                {detail.map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ")}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function TraceDetail({ id }: { id: string }) {
  const { data, error } = useLoad(() => labApi.trace(id), [id]);
  if (!data) return <Loading error={error} />;
  return (
    <section className="card" aria-labelledby="trace-h">
      <div className="card-head">
        <h2 id="trace-h">Trace {data.trace_id.slice(0, 8)}</h2>
        <span style={{ display: "flex", gap: 6 }}><SourceChip source={data.answer_source} />
          <span className={`chip ${data.verification_status === "rejected" ? "rule" : data.verification_status === "verified" ? "ok" : "neutral"}`}>
            {data.verification_status === "verified" ? "✓" : data.verification_status === "rejected" ? "✕" : "–"} Numbers {data.verification_status.replace("_", " ")}</span></span>
      </div>
      <dl className="kv">
        <dt>When</dt><dd>{when(data.created_at)} · {data.username} · funds {data.funds.join(", ")}</dd>
        <dt>Answer type</dt><dd><code>{data.intent}</code> for the period ending {data.period ?? "—"}</dd>
        <dt>Question</dt><dd>{data.question_chars} characters (the text itself is never stored)</dd>
        <dt>Model</dt><dd>{data.model ?? "not used"}{data.prompt_tokens ? ` · ${data.prompt_tokens} prompt tokens, ${data.output_tokens} output tokens` : ""}</dd>
        <dt>Number check</dt><dd>{data.numbers_checked} figures checked, {data.unsupported_numbers} not supported by the facts</dd>
        <dt>Cited properties</dt><dd>{data.cited_property_ids.length ? data.cited_property_ids.map((p) => (
          <Link key={p} href={`/properties/${p}${data.period ? `?period=${data.period}` : ""}`} style={{ marginRight: 8 }}>{p}</Link>)) : "none"}</dd>
        <dt>Facts SHA-256</dt><dd><code style={{ wordBreak: "break-all" }}>{data.facts_sha256 ?? "—"}</code></dd>
        <dt>Total time</dt><dd>{secs(data.total_ms)}</dd>
        {data.notes.length > 0 && <><dt>Notes</dt><dd>{data.notes.join(" ")}</dd></>}
      </dl>
      <h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>Stages</h3>
      <Waterfall stages={data.stages} />
      <details className="hint" style={{ marginTop: 10 }}>
        <summary>Source record IDs ({data.source_record_ids.length})</summary>
        <div style={{ wordBreak: "break-word" }}>monthly_financials / monthly_budgets: {data.source_record_ids.join(", ") || "none"}</div>
      </details>
    </section>
  );
}

function TracesTab({ user, selected, select }: { user: User; selected: string | null; select: (id: string) => void }) {
  const { data, error } = useLoad(labApi.traces);
  if (!data) return <Loading error={error} />;
  return (
    <div className="stack">
      {selected && <TraceDetail id={selected} />}
      <section className="card" aria-labelledby="tl-h">
        <div className="card-head"><h2 id="tl-h">Recent requests</h2>
          <span className="muted" style={{ fontSize: 12 }}>Showing {data.scope}{user.role === "admin" ? " (admin)" : ""}</span></div>
        {data.traces.length === 0 ? <div className="empty">No traces yet. Ask the chat a question on the Portfolio page.</div> : (
          <div className="table-wrap"><table className="wide">
            <thead><tr><th>When</th>{user.role === "admin" && <th>User</th>}<th>Answer type</th><th>Answer</th><th>Numbers</th>
              <th className="num">Model</th><th className="num">Total</th></tr></thead>
            <tbody>{data.traces.map((t: TraceRow) => (
              <tr key={t.trace_id} className="clickable" aria-selected={selected === t.trace_id} onClick={() => select(t.trace_id)}
                  style={selected === t.trace_id ? { background: "var(--surface-2)" } : undefined}>
                <td style={{ whiteSpace: "nowrap" }}>{when(t.created_at)}{t.endpoint !== "chat" && <div className="muted" style={{ fontSize: 11 }}>{t.endpoint.replace(/_/g, " ")}</div>}</td>
                {user.role === "admin" && <td>{t.username}</td>}
                <td><code>{t.intent}</code></td><td><SourceChip source={t.answer_source} /></td>
                <td>{t.verification_status === "not_applicable" ? "—" : `${t.verification_status} (${t.numbers_checked})`}</td>
                <td className="num">{secs(t.model_ms)}</td><td className="num">{secs(t.total_ms)}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- model on / off

const SAMPLES = ["Which assets are underperforming?", "Why was Westlake flagged?", "Give me a portfolio overview"];

function CompareTab() {
  const [q, setQ] = useState(SAMPLES[0]);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Compare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = (question: string) => {
    setBusy(true); setError(null); setRes(null);
    labApi.compare(question).then(setRes).catch((e) => setError(errText(e))).finally(() => setBusy(false));
  };
  return (
    <div className="stack">
      <section className="card" aria-labelledby="cmp-h">
        <div className="card-head"><h2 id="cmp-h">Same question, model off and on</h2></div>
        <p className="hint" style={{ marginTop: 0 }}>
          PropertyIQ answers twice: once with the local model switched off (verified summary only), once with it on.
          The verified facts must be identical, so the numbers never depend on the model. With the model on, this takes 10–60 seconds.
        </p>
        <form className="chat-form" onSubmit={(e) => { e.preventDefault(); run(q); }} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Question" maxLength={500}
                 style={{ flex: 1, minWidth: 220, padding: "8px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)" }} />
          <button className="btn" disabled={busy || q.trim().length < 3}>{busy ? "Running…" : "Compare"}</button>
        </form>
        <div className="chips" style={{ marginTop: 8 }}>
          {SAMPLES.map((s) => <button key={s} className="linkbtn" style={{ fontSize: 12, marginRight: 10 }} onClick={() => { setQ(s); run(s); }} disabled={busy}>{s}</button>)}
        </div>
        {busy && <div className="hint"><span className="spinner" aria-hidden="true" /> Asking the local model…</div>}
        {error && <div className="error" role="alert" style={{ marginTop: 8 }}>{error}</div>}
      </section>
      {res && (
        <>
          <div className={`lab-verdict ${res.same_facts ? "ok" : "bad"}`} role="status">
            {res.same_facts ? "✓ Identical verified facts in both runs" : "✕ The facts differed between runs"} · facts SHA-256{" "}
            <code>{(res.model_on.facts_sha256 ?? "none").slice(0, 16)}…</code>
          </div>
          <div className="grid-2">
            {(["model_off", "model_on"] as const).map((k) => {
              const r = res[k];
              return (
                <section key={k} className="card">
                  <div className="card-head"><h2>{k === "model_off" ? "Model off" : "Model on"}</h2><SourceChip source={r.answer_source} /></div>
                  <p style={{ whiteSpace: "pre-wrap", marginTop: 0 }}>{r.answer}</p>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {secs(r.latency_ms)} · numbers {r.verification.status.replace("_", " ")} ({r.verification.numbers_checked} checked)
                    {r.trace_id && <> · <Link href={`/lab?tab=traces&trace=${r.trace_id}`}>trace</Link></>}
                  </div>
                  {r.notes.length > 0 && <div className="hint">{r.notes.join(" ")}</div>}
                </section>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- models, tools, schema, offline

function ModelsTab() {
  const { data, error } = useLoad<Models>(labApi.models);
  if (!data) return <Loading error={error} />;
  return (
    <section className="card" aria-labelledby="m-h">
      <div className="card-head"><h2 id="m-h">Installed local models</h2>
        <span className="muted" style={{ fontSize: 12 }}>{data.reachable ? `Ollama ${data.ollama_version} at ${data.ollama_url}` : "Ollama isn't answering"}</span></div>
      <div className="table-wrap"><table className="wide">
        <thead><tr><th>Model</th><th>Role</th><th>Family</th><th>Parameters</th><th>Quantization</th><th className="num">Size</th><th>Allowlisted</th><th>In memory</th><th>Digest</th></tr></thead>
        <tbody>{data.models.map((m) => (
          <tr key={m.name}>
            <td><code>{m.name}</code>{m.capabilities.length > 0 && <div className="muted" style={{ fontSize: 11 }}>{m.capabilities.join(", ")}</div>}</td>
            <td>{m.role}</td><td>{m.family ?? "—"}</td><td>{m.parameters ?? "—"}</td><td>{m.quantization ?? "—"}</td>
            <td className="num">{m.size_gb.toFixed(2)} GB</td>
            <td>{m.allowlisted ? <span className="chip ok">✓ Yes</span> : <span className="chip neutral">No</span>}</td>
            <td>{m.loaded_in_memory ? "Yes" : "No"}</td><td><code>{m.digest}</code></td>
          </tr>))}</tbody>
      </table></div>
      <p className="hint">Allowlist: {data.allowlist.join(", ")}. The API refuses any model not on it, any cloud model, and any address that isn&apos;t on this PC or the local network.</p>
    </section>
  );
}

function ToolsTab() {
  const { data, error } = useLoad(labApi.tools);
  if (!data) return <Loading error={error} />;
  return (
    <section className="card" aria-labelledby="t-h">
      <div className="card-head"><h2 id="t-h">Vetted functions</h2>
        <span className="muted" style={{ fontSize: 12 }}>Read from the running code</span></div>
      <p className="hint" style={{ marginTop: 0 }}>The model can&apos;t call these. The router picks one, it runs with the user&apos;s scope, and only its minimized output reaches the model.</p>
      <div className="table-wrap"><table className="wide">
        <thead><tr><th>Function</th><th>Kind</th><th>Returns</th><th>Scope</th><th>Used by</th></tr></thead>
        <tbody>{data.tools.map((t: Tool) => (
          <tr key={t.id}>
            <td><code>{t.id}</code><div className="muted" style={{ fontSize: 11 }}>({t.parameters.join(", ")}) · {t.module.replace(/\./g, "/")}.py line {t.source_line}</div></td>
            <td>{t.kind}</td><td>{t.returns}</td><td style={{ fontSize: 13 }}>{t.scope}</td><td style={{ fontSize: 13 }}>{t.used_by.join("; ")}</td>
          </tr>))}</tbody>
      </table></div>
    </section>
  );
}

function SchemaTab() {
  const { data, error } = useLoad<Schema>(labApi.schema);
  const [open, setOpen] = useState<string | null>("loans");
  if (!data) return <Loading error={error} />;
  return (
    <div className="stack">
      <section className="card" aria-labelledby="s-h">
        <div className="card-head"><h2 id="s-h">Tables in schema piq</h2>
          <span className="muted" style={{ fontSize: 12 }}>{data.tables.length} tables{data.row_counts_shown ? "" : " · row counts hidden (they include funds you can't see)"}</span></div>
        <div className="table-wrap"><table className="wide">
          <thead><tr><th>Table</th><th>What it holds</th><th className="num">Columns</th>{data.row_counts_shown && <th className="num">Rows</th>}<th>Links to</th></tr></thead>
          <tbody>{data.tables.map((t) => (
            <Fragment key={t.name}>
              <tr className="clickable" onClick={() => setOpen(open === t.name ? null : t.name)} aria-expanded={open === t.name}>
                <td><code>{t.name}</code></td><td style={{ fontSize: 13 }}>{t.note ?? "—"}</td><td className="num">{t.columns.length}</td>
                {data.row_counts_shown && <td className="num">{t.rows?.toLocaleString("en-US")}</td>}
                <td style={{ fontSize: 13 }}>{[...new Set(t.references.map((r) => r.table))].join(", ") || "—"}</td>
              </tr>
              {open === t.name && (
                <tr><td colSpan={data.row_counts_shown ? 5 : 4} style={{ background: "var(--surface-2)" }}>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 12 }}>
                    {t.columns.map((c) => {
                      const ref = t.references.find((r) => r.column === c.name);
                      return <span key={c.name}><code>{c.name}</code> <span className="muted">{c.type}{c.nullable ? "" : ", required"}{ref ? ` → ${ref.table}.${ref.ref_column}` : ""}</span></span>;
                    })}
                  </div>
                </td></tr>
              )}
            </Fragment>))}</tbody>
        </table></div>
      </section>
      <section className="card" aria-labelledby="r-h">
        <div className="card-head"><h2 id="r-h">Database logins and what they can touch</h2></div>
        <table><thead><tr><th>Login</th><th className="num">Tables it can read</th><th className="num">Tables it can change</th><th>Used by</th></tr></thead>
          <tbody>{data.roles.map((r) => (
            <tr key={r.rolname}><td><code>{r.rolname}</code></td><td className="num">{r.can_read}</td><td className="num">{r.can_write}</td>
              <td style={{ fontSize: 13 }}>{{ piq_readonly: "The API, for every read", piq_owner: "Migrations and seeding only",
                piq_screening_writer: "Loan screening saves", piq_trace_writer: "Writing request traces", piq_audit_writer: "Writing the access audit" }[r.rolname] ?? "—"}</td></tr>))}</tbody></table>
      </section>
    </div>
  );
}

function OfflineTab() {
  const { data, error } = useLoad<Offline>(labApi.offline);
  if (!data) return <Loading error={error} />;
  const on = data.lockdown.mode !== "off";
  return (
    <div className="stack">
      <section className="card" aria-labelledby="o-h">
        <div className="card-head"><h2 id="o-h">Is PropertyIQ cut off from the internet?</h2>
          <span className={`chip ${on ? "ok" : "neutral"}`}>{on ? `✓ Lockdown on (${data.lockdown.mode})` : "Lockdown off"}</span></div>
        <dl className="kv">
          <dt>Listening</dt><dd>{data.listening}</dd>
          <dt>AI service calls</dt><dd>Only Ollama on 127.0.0.1. No hosted AI libraries are installed; the model adapter refuses non-local addresses.</dd>
          <dt>Answers so far</dt><dd>{data.trace_stats.model_answers} worded by the local model, {data.trace_stats.template_answers} by the verified summary</dd>
        </dl>
        <p className="hint">To prove it: an admin turns on lockdown on the Admin page (Windows asks for permission), then asks the chat or runs
          &ldquo;Model on / off&rdquo;. The lockdown saves before/after evidence below, and the Admin checklist makes a real outside connection test.</p>
      </section>
      <section className="card" aria-labelledby="ev-h">
        <div className="card-head"><h2 id="ev-h">Lockdown evidence</h2></div>
        {data.evidence.length === 0 ? <div className="empty">No lockdown has been run yet, so there&apos;s no evidence to show.</div> : (
          <div className="table-wrap"><table className="wide">
            <thead><tr><th>When</th><th>Step</th><th>Mode</th><th>Test connection to the internet</th><th className="num">Outside connections</th><th>From PropertyIQ programs</th></tr></thead>
            <tbody>{data.evidence.map((e) => (
              <tr key={e.file}><td>{e.at?.replace("T", " ")}</td><td>{e.label}</td><td>{e.mode ?? "—"}</td>
                <td>{e.test_connection_succeeded === null ? "—" : e.test_connection_succeeded ? "Connected" : <b>Blocked</b>}</td>
                <td className="num">{e.outside_connections}</td>
                <td>{e.propertyiq_outside_connections === 0 ? "None" : `${e.propertyiq_outside_connections} (${e.propertyiq_processes.join(", ")})`}</td></tr>))}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- page

function Lab({ user }: { user: User }) {
  const params = useSearchParams();
  const router = useRouter();
  const tab = (params.get("tab") as Tab) ?? "overview";
  const trace = params.get("trace");
  const go = (t: Tab, extra = "") => router.replace(`/lab?tab=${t}${extra}`);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>AI Engineering Lab</h1>
          <div className="sub">How PropertyIQ&apos;s AI answers are made, checked and kept on this PC. Everything shown is read from the running system.</div>
        </div>
      </div>
      <div className="lab-tabs" role="tablist" aria-label="Lab sections">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => go(k)}>{label}</button>
        ))}
      </div>
      <div role="tabpanel" style={{ marginTop: 16 }}>
        {tab === "overview" && <OverviewTab />}
        {tab === "traces" && <TracesTab user={user} selected={trace} select={(id) => go("traces", `&trace=${id}`)} />}
        {tab === "compare" && <CompareTab />}
        {tab === "models" && <ModelsTab />}
        {tab === "tools" && <ToolsTab />}
        {tab === "schema" && <SchemaTab />}
        {tab === "offline" && <OfflineTab />}
      </div>
    </>
  );
}

export default function LabPage() {
  return <Suspense fallback={null}><Shell>{(user) => <Lab user={user} />}</Shell></Suspense>;
}
