"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { ApiError } from "@/lib/api";
import { PROPERTY_TYPES } from "@/lib/screening";
import { ws, type DealRow } from "@/lib/workspace";

function Deals() {
  const router = useRouter();
  const [deals, setDeals] = useState<DealRow[] | null>(null);
  const [name, setName] = useState("");
  const [units, setUnits] = useState("");
  const [ptype, setPtype] = useState("multifamily");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { ws.deals().then(setDeals).catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable.")); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const facts = { property_type: ptype, ...(units ? { units: Number(units) } : {}) };
      const d = await ws.createDeal({ name: name.trim(), property_facts: facts, borrower_inputs: { property_type: ptype } });
      router.push(`/screening/deals/${d.deal_id}`);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't create the deal."); }
  }

  return (
    <>
      <div className="breadcrumb"><Link href="/screening">← Loan screening</Link></div>
      <div className="page-head">
        <div>
          <h1>Deal workspace</h1>
          <div className="sub">Upload a rent roll and T-12, review what was read, approve it, and see the flags. Files stay on this computer.</div>
        </div>
      </div>
      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}
      <div className="scr-layout">
        <form className="card scr-form" onSubmit={create} aria-label="New deal">
          <h2>New deal</h2>
          <div className="scr-field wide"><label htmlFor="dname">Deal or property name</label>
            <input id="dname" type="text" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Juniper Court Apartments" /></div>
          <div className="scr-field"><label htmlFor="dtype">Property type</label>
            <select id="dtype" value={ptype} onChange={(e) => setPtype(e.target.value)}>
              {PROPERTY_TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select></div>
          <div className="scr-field"><label htmlFor="dunits">Units</label>
            <input id="dunits" type="text" inputMode="numeric" value={units} onChange={(e) => setUnits(e.target.value.replace(/\D/g, ""))} /></div>
          <button className="btn" type="submit" disabled={!name.trim()}>Create deal</button>
          <p className="hint">Rent roll intake is multifamily only for now. T-12 intake works for every property type.</p>
        </form>
        <section className="card">
          <div className="card-head"><h2>Deals</h2></div>
          {!deals ? <div className="empty">Loading…</div> : deals.length === 0 ? <div className="empty">No deals yet.</div> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Deal</th><th className="num">Documents</th><th className="num">Open flags</th><th>Updated</th></tr></thead>
                <tbody>
                  {deals.map((d) => (
                    <tr key={d.deal_id} className="clickable" onClick={() => router.push(`/screening/deals/${d.deal_id}`)}>
                      <td><Link href={`/screening/deals/${d.deal_id}`} onClick={(e) => e.stopPropagation()}>{d.name}</Link></td>
                      <td className="num">{d.documents}</td>
                      <td className="num">{d.open_flags}</td>
                      <td>{new Date(d.updated_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}

export default function Page() {
  return <Suspense fallback={null}><Shell>{() => <Deals />}</Shell></Suspense>;
}
