"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Shell from "@/components/Shell";
import { api, ApiError, type Checklist, type CheckStatus, type FirewallStatus, type LockdownMode, type User } from "@/lib/api";

const STATUS: Record<CheckStatus, { label: string; icon: string; cls: string }> = {
  pass: { label: "Pass", icon: "✓", cls: "ok" },
  warn: { label: "Warning", icon: "!", cls: "warn" },
  fail: { label: "Fail", icon: "✕", cls: "rule" },
  info: { label: "Info", icon: "i", cls: "neutral" },
};

const MODES: { mode: LockdownMode; title: string; body: string; warning?: string }[] = [
  {
    mode: "app",
    title: "Block PropertyIQ from the internet",
    body: "Ollama, the API (Python), the web app (Node.js) and Docker Desktop can't reach the internet. They still talk to each other on this PC. Everything else on the computer, including remote access, keeps working.",
  },
  {
    mode: "computer",
    title: "Block the whole computer from the internet",
    body: "Blocks all outbound internet traffic from this PC. Your local network and PropertyIQ keep working.",
    warning: "Remote-access sessions (AnyViewer) will disconnect, and only someone at this PC can undo it, using the “PropertyIQ - Restore internet” shortcut on the desktop.",
  },
  { mode: "off", title: "Turn lockdown off", body: "Removes every PropertyIQ firewall rule and puts the firewall back exactly as it was." },
];

const MODE_LABEL: Record<string, string> = {
  off: "Off", app: "On: PropertyIQ programs blocked", computer: "On: whole computer blocked", unknown: "Unknown",
};

function Admin({ user }: { user: User }) {
  const [data, setData] = useState<Checklist | null>(null);
  const [fw, setFw] = useState<FirewallStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirm, setConfirm] = useState<LockdownMode | null>(null);
  const [waiting, setWaiting] = useState<{ mode: LockdownMode; message: string } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.checklist()
      .then((c) => { setData(c); setFw(c.lockdown); })
      .catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { if (user.role === "admin") load(); }, [user.role, load]);
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  if (user.role !== "admin") return <div className="error" role="alert">This page is for the admin account only.</div>;

  const request = async (mode: LockdownMode) => {
    setConfirm(null);
    setError(null);
    try {
      const r = await api.setLockdown(mode);
      setWaiting({ mode, message: r.message });
      const started = Date.now();
      if (timer.current) clearInterval(timer.current);
      timer.current = setInterval(async () => {
        const s = await api.lockdown().catch(() => null);
        if (s && s.mode === mode) {
          clearInterval(timer.current!);
          setWaiting(null);
          setFw(s);
          load();
        } else if (Date.now() - started > 120_000) {
          clearInterval(timer.current!);
          setWaiting(null);
          setError("Nothing changed after 2 minutes. The Windows prompt may have been declined or not seen.");
        }
      }, 3000);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The request failed.");
    }
  };

  const mode = fw?.mode ?? "unknown";
  const areas = data ? Array.from(new Set(data.items.map((i) => i.area))) : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Security and lockdown</h1>
          <div className="sub">Every check below is run live on this PC each time the page loads. Secret values are never shown.</div>
        </div>
      </div>

      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}

      <div className="stack">
        <section className="card" aria-labelledby="lockdown-h">
          <div className="card-head">
            <h2 id="lockdown-h">Internet lockdown</h2>
            <span className={`chip ${mode === "off" ? "neutral" : mode === "unknown" ? "warn" : "ok"}`}>{MODE_LABEL[mode]}</span>
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            Each change opens a Windows permission prompt on this PC&apos;s screen. Someone at the PC (or on a remote
            session that shows the prompt) must click <strong>Yes</strong>. Changes stay in place after a restart until turned off.
          </p>
          {waiting && (
            <div className="hint" role="status" style={{ padding: "10px 12px", background: "var(--surface-2)", borderRadius: 8 }}>
              <span className="spinner" aria-hidden="true" /> {waiting.message} Waiting for the firewall to change…
            </div>
          )}
          <div className="lockdown-options">
            {MODES.map((m) => {
              const current = mode === m.mode;
              return (
                <div key={m.mode} className={`lockdown-option${current ? " current" : ""}`}>
                  <h3>{m.title}</h3>
                  <p>{m.body}</p>
                  {m.warning && <p className="lockdown-warning"><strong>Warning:</strong> {m.warning}</p>}
                  {confirm === m.mode ? (
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button className="btn" onClick={() => request(m.mode)}>Yes, ask Windows</button>
                      <button className="btn secondary" onClick={() => setConfirm(null)}>Cancel</button>
                    </div>
                  ) : (
                    <button className={m.mode === "off" ? "btn secondary" : "btn"} disabled={current || !!waiting}
                            onClick={() => (m.mode === "computer" ? setConfirm(m.mode) : request(m.mode))}>
                      {current ? "Active now" : m.mode === "off" ? "Turn off" : "Turn on"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <section className="card" aria-labelledby="check-h">
          <div className="card-head">
            <h2 id="check-h">Security checklist</h2>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {data && (["pass", "warn", "fail", "info"] as CheckStatus[]).map((s) => (
                <span key={s} className={`chip ${STATUS[s].cls}`}>{STATUS[s].icon} {data.counts[s]} {STATUS[s].label.toLowerCase()}</span>
              ))}
              <button className="btn secondary" onClick={load} disabled={loading}>{loading ? "Checking…" : "Run again"}</button>
            </div>
          </div>
          {!data && !error && <div className="empty">Running checks…</div>}
          {data && <div className="hint" style={{ marginTop: 0 }}>Checked {data.checked_at.replace("T", " ")}</div>}
          {data && areas.map((area) => (
            <div key={area} style={{ marginTop: 16 }}>
              <h3 style={{ fontSize: 14, margin: "0 0 6px" }}>{area}</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th style={{ width: 110 }}>Result</th><th style={{ width: "34%" }}>Check</th><th>Evidence</th></tr>
                  </thead>
                  <tbody>
                    {data.items.filter((i) => i.area === area).map((i) => (
                      <tr key={i.key}>
                        <td><span className={`chip ${STATUS[i.status].cls}`}>{STATUS[i.status].icon} {STATUS[i.status].label}</span></td>
                        <td>{i.title}</td>
                        <td>
                          {i.detail}
                          {i.fix && <div className="muted" style={{ marginTop: 4 }}>To fix: {i.fix}</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

export default function AdminPage() {
  return <Shell>{(user) => <Admin user={user} />}</Shell>;
}
