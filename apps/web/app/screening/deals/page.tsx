"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { ApiError, can, type User } from "@/lib/api";
import { PROPERTY_TYPES } from "@/lib/screening";
import { ws, type DealRow } from "@/lib/workspace";

function Deals({ user }: { user: User }) {
  const router = useRouter();
  const [deals, setDeals] = useState<DealRow[] | null>(null);
  const [name, setName] = useState("");
  const [units, setUnits] = useState("");
  const [ptype, setPtype] = useState("multifamily");
  const [fund, setFund] = useState(user.portfolios.length === 1 ? user.portfolios[0] : "");
  const [error, setError] = useState<string | null>(null);
  const canWork = can(user, "work_deals");

  useEffect(() => { ws.deals().then(setDeals).catch((e) => setError(e instanceof ApiError ? e.message : "The API is unreachable.")); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !fund) return;
    try {
      const facts = { property_type: ptype, ...(units ? { units: Number(units) } : {}) };
      const d = await ws.createDeal({ name: name.trim(), fund_id: fund, property_facts: facts, borrower_inputs: { property_type: ptype } });
      router.push(`/screening/deals/${d.deal_id}`);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Couldn't create the deal."); }
  }

  return (
    <>
      <div className="breadcrumb"><Link href="/screening">← Loan screening</Link> · <Link href="/screening/settings">Screening settings</Link></div>
      <div className="page-head">
        <div>
          <h1>Deal workspace</h1>
          <div className="sub">Upload a rent roll and T-12, review what was read, approve it, and see the flags. Files stay on this computer.
            {" "}Showing deals in {user.portfolios.join(" and ")}.</div>
        </div>
      </div>
      {error && <div className="error" role="alert" style={{ marginBottom: 16 }}>{error}</div>}
      <div className="scr-layout">
        {canWork ? (
          <form className="card scr-form" onSubmit={create} aria-label="New deal">
            <h2>New deal</h2>
            <div className="scr-field wide"><label htmlFor="dname">Deal or property name</label>
              <input id="dname" type="text" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Juniper Court Apartments" /></div>
            <div className="scr-field"><label htmlFor="dfund">Fund</label>
              <select id="dfund" value={fund} onChange={(e) => setFund(e.target.value)} disabled={user.portfolios.length === 1}>
                {user.portfolios.length > 1 && <option value="">Choose a fund</option>}
                {user.portfolios.map((p) => <option key={p} value={p}>{p}</option>)}
              </select></div>
            <div className="scr-field"><label htmlFor="dtype">Property type</label>
              <select id="dtype" value={ptype} onChange={(e) => setPtype(e.target.value)}>
                {PROPERTY_TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></div>
            <div className="scr-field"><label htmlFor="dunits">Units</label>
              <input id="dunits" type="text" inputMode="numeric" value={units} onChange={(e) => setUnits(e.target.value.replace(/\D/g, ""))} /></div>
            <button className="btn" type="submit" disabled={!name.trim() || !fund}>Create deal</button>
            <p className="hint">Only people granted this fund can see the deal. Rent roll intake is multifamily only for now; T-12 intake works for every property type.</p>
          </form>
        ) : (
          <div className="card"><h2>New deal</h2><p className="access-note">Your role is view only, so you can open deals and exports but not create or change them.</p></div>
        )}
        <section className="card">
          <div className="card-head"><h2>Deals</h2></div>
          {!deals ? <div className="empty">Loading…</div> : deals.length === 0 ? <div className="empty">No deals in your funds yet.</div> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Deal</th><th>Fund</th><th className="num">Documents</th><th className="num">Open flags</th><th>Updated</th></tr></thead>
                <tbody>
                  {deals.map((d) => (
                    <tr key={d.deal_id} className="clickable" onClick={() => router.push(`/screening/deals/${d.deal_id}`)}>
                      <td><Link href={`/screening/deals/${d.deal_id}`} onClick={(e) => e.stopPropagation()}>{d.name}</Link></td>
                      <td>{d.fund_id}</td>
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
  return <Suspense fallback={null}><Shell>{(user) => <Deals user={user} />}</Shell></Suspense>;
}
