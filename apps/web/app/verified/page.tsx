"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, ApiError, type Underperforming } from "@/lib/api";
import { usd } from "@/lib/format";

function Verified() {
  const period = useSearchParams().get("period");
  const [data, setData] = useState<Underperforming | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.underperforming(period).then(setData).catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable."));
  }, [period]);

  if (error) return <div className="error" role="alert">{error}</div>;
  if (!data) return <div className="empty">Loading…</div>;

  return (
    <>
      <div className="breadcrumb"><Link href={`/?period=${data.period}`}>← Portfolio overview</Link></div>
      <div className="page-head">
        <div>
          <h1>Verified calculations</h1>
          <div className="sub">
            The exact payload from <code>GET /api/properties/underperforming?period={data.period}</code>. The dashboard and chat use these figures.
          </div>
        </div>
      </div>
      <div className="stack">
        <section className="card">
          <dl className="kv">
            <dt>Period</dt><dd>{data.period} (trailing 12 months)</dd>
            <dt>Scope</dt><dd>{data.scope.username} · {data.scope.portfolios.join(", ")} · {data.scope.properties_evaluated} properties evaluated</dd>
            <dt>Result</dt><dd>{data.flagged_count} flagged · {usd(data.total_budget_shortfall_flagged)} total budget shortfall</dd>
            <dt>Rule set</dt><dd>{data.rule_set.version}: {data.rule_set.rules.map((r) => r.condition).join("; ")}</dd>
            <dt>Priority</dt><dd>{data.rule_set.priority_rule}</dd>
            <dt>Payload SHA-256</dt><dd className="mono">{data.payload_sha256}</dd>
          </dl>
          <p className="sub" style={{ marginTop: 12 }}>
            The hash is SHA-256 of the JSON body with <code>payload_sha256</code> removed, keys sorted and no whitespace.
            The same seed and rule set always produce the same hash, so a result can be checked later.
          </p>
        </section>
        <section className="card">
          <div className="card-head"><h2>Payload</h2></div>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, maxHeight: "70vh", overflow: "auto" }}>
            {JSON.stringify(data, null, 2)}
          </pre>
        </section>
      </div>
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Shell>{() => <Verified />}</Shell>
    </Suspense>
  );
}
