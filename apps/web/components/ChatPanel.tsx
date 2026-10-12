"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatAnswer } from "@/lib/api";

type Message = { role: "user"; text: string } | { role: "bot"; answer: ChatAnswer } | { role: "error"; text: string };

const DEFAULT_SUGGESTIONS = [
  "Summarize our underperforming assets.",
  "Why was Westlake flagged?",
  "Show the underlying NOI budget variance.",
  "Give me a portfolio overview.",
];

function SourceBadge({ a }: { a: ChatAnswer }) {
  if (a.answer_source === "local_model") {
    return <span className="chip ok" title={`${a.verification.numbers_checked} figures matched verified data`}>✓ Local model · figures verified</span>;
  }
  if (a.verification.status === "rejected") {
    const why = [...a.verification.unsupported_numbers.map((n) => `unmatched ${n}`), ...a.verification.missing_properties.map((p) => `omitted ${p}`)];
    return <span className="chip rule" title={why.join(", ")}>! Model draft rejected · verified summary</span>;
  }
  return <span className="chip neutral">Verified summary</span>;
}

// The model sometimes uses **bold**. Render just that; everything else stays plain text (no HTML injection).
function RichText({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : part)}</>;
}

function Answer({ a }: { a: ChatAnswer }) {
  const records = a.citations.reduce((n, c) => n + Object.values(c.source_records).reduce((m, ids) => m + ids.length, 0), 0);
  return (
    <div className="msg bot">
      <div className="text"><RichText text={a.answer} /></div>
      <div className="msg-meta">
        <SourceBadge a={a} />
        <span>Period {a.period}</span>
        <span>· {(a.latency_ms / 1000).toFixed(1)}s</span>
      </div>
      {a.notes.length > 0 && <div className="msg-notes">{a.notes.join(" ")}</div>}
      {a.citations.length > 0 && (
        <div className="citations">
          <span className="muted">Sources ({records} database records):</span>
          {a.citations.map((c) => (
            <Link key={c.property_id} href={c.link}>{c.property_id} {c.name}, TTM ending {c.period}</Link>
          ))}
          <Link href={a.calculation_link}>View the verified calculations →</Link>
        </div>
      )}
      {Object.keys(a.facts).length > 0 && (
        <details>
          <summary>Facts sent to the local model</summary>
          <pre>{JSON.stringify(a.facts, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export default function ChatPanel({ period, suggestions = DEFAULT_SUGGESTIONS, initialFocus = null }: {
  period: string | null; suggestions?: string[]; initialFocus?: string | null;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [focus, setFocus] = useState<string | null>(initialFocus);
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [messages, busy]);
  useEffect(() => {
    if (!busy) return;
    const start = Date.now();
    const t = setInterval(() => setElapsed(Math.round((Date.now() - start) / 1000)), 500);
    return () => { clearInterval(t); setElapsed(0); };
  }, [busy]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setMessages((m) => [...m, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    try {
      const a = await api.chat(q, period, focus);
      if (a.focus_property_id) setFocus(a.focus_property_id);
      setMessages((m) => [...m, { role: "bot", answer: a }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "error", text: err instanceof ApiError ? err.message : "The API is unreachable." }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card chat" aria-label="Ask PropertyIQ">
      <div className="chat-head">
        <h2>Ask PropertyIQ</h2>
        <div className="sub">Answers come from verified calculations. The local model only explains them.</div>
      </div>
      <div className="chat-log" ref={log} aria-live="polite">
        {messages.length === 0 && <div className="empty">Try a question below. Nothing leaves this computer.</div>}
        {messages.map((m, i) =>
          m.role === "user" ? <div key={i} className="msg user">{m.text}</div>
            : m.role === "bot" ? <Answer key={i} a={m.answer} />
              : <div key={i} className="error" role="alert">{m.text}</div>,
        )}
        {busy && (
          <div className="thinking"><span className="spinner" aria-hidden="true" /> Running on the local model… {elapsed}s</div>
        )}
      </div>
      <div className="suggestions">
        {suggestions.map((s) => (
          <button key={s} className="suggestion" onClick={() => ask(s)} disabled={busy}>{s}</button>
        ))}
      </div>
      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
        <textarea
          value={input}
          maxLength={500}
          placeholder="Ask about the portfolio…"
          aria-label="Question"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input); } }}
        />
        <button className="btn" type="submit" disabled={busy || !input.trim()}>Ask</button>
      </form>
    </section>
  );
}
