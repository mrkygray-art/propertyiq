"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { usd } from "@/lib/format";
import { ratio, STATUS_META, type CovenantStatus, type LoanRow } from "@/lib/debt";

// DSCR (up = safer) against LTV (right = more leverage). Bubble area = balance; colour and legend = worst
// covenant status. Dashed guides at a 1.25× DSCR and 70% LTV split the chart into risk quadrants.
export default function RiskScatter({ loans, period }: { loans: LoanRow[]; period: string }) {
  const router = useRouter();
  const [hover, setHover] = useState<string | null>(null);
  const W = 640, H = 320, pad = { top: 14, right: 14, bottom: 38, left: 48 };
  const innerW = W - pad.left - pad.right, innerH = H - pad.top - pad.bottom;
  const pts = loans.filter((l) => l.dscr !== null && l.ltv_pct !== null);
  const xMin = 30, xMax = Math.max(100, Math.ceil(Math.max(...pts.map((l) => l.ltv_pct!)) / 10) * 10);
  const yMin = 0.75, yMax = Math.max(3, Math.ceil(Math.max(...pts.map((l) => l.dscr!)) * 2) / 2);
  const x = (v: number) => pad.left + ((Math.min(Math.max(v, xMin), xMax) - xMin) / (xMax - xMin)) * innerW;
  const y = (v: number) => pad.top + innerH - ((Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * innerH;
  const maxBal = Math.max(1, ...pts.map((l) => l.balance ?? 0));
  const r = (b: number | null) => 4 + 12 * Math.sqrt((b ?? 0) / maxBal);
  const xTicks = Array.from({ length: Math.floor((xMax - xMin) / 10) + 1 }, (_, i) => xMin + i * 10);
  const yTicks: number[] = [];
  for (let v = 1; v <= yMax + 1e-9; v += 0.5) yTicks.push(v);
  // Draw the riskiest last so they sit on top.
  const order: CovenantStatus[] = ["pass", "not_calculable", "watch", "breach"];
  const sorted = [...pts].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  const h = pts.find((l) => l.loan_id === hover);

  return (
    <div style={{ position: "relative" }}>
      <div className="legend">
        {(["breach", "watch", "pass"] as CovenantStatus[]).map((s) => (
          <span key={s}><span className="dot" style={{ background: STATUS_META[s].color, width: 10, height: 10 }} />
            {STATUS_META[s].icon} {STATUS_META[s].label} ({pts.filter((l) => l.status === s).length})</span>
        ))}
        <span className="muted">Bubble size = balance</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" className="chart"
           aria-label={`DSCR versus LTV for ${pts.length} loans. ${pts.filter((l) => l.status === "breach").length} breach a covenant.`}
           onMouseLeave={() => setHover(null)}>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line className="gridline" x1={pad.left} x2={pad.left + innerW} y1={y(t)} y2={y(t)} />
            <text className="axis" x={pad.left - 6} y={y(t) + 4} textAnchor="end">{t.toFixed(1)}×</text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={`x${t}`} className="axis" x={x(t)} y={pad.top + innerH + 16} textAnchor="middle">{t}%</text>
        ))}
        <text className="axis" x={pad.left + innerW / 2} y={H - 4} textAnchor="middle">Loan to value (LTV) →</text>
        <text className="axis" transform={`translate(12 ${pad.top + innerH / 2}) rotate(-90)`} textAnchor="middle">DSCR</text>
        <line className="threshold" x1={pad.left} x2={pad.left + innerW} y1={y(1.25)} y2={y(1.25)} />
        <text className="threshold-label" x={pad.left + innerW - 4} y={y(1.25) - 5} textAnchor="end">1.25× DSCR</text>
        <line className="threshold" x1={x(70)} x2={x(70)} y1={pad.top} y2={pad.top + innerH} />
        <text className="threshold-label" x={x(70) + 4} y={pad.top + 12}>70% LTV</text>
        <text className="axis" x={x(xMax) - 4} y={y(yMin) - 6} textAnchor="end" style={{ fill: "var(--critical)" }}>Higher risk</text>
        {sorted.map((l) => (
          <circle key={l.loan_id} cx={x(l.ltv_pct!)} cy={y(l.dscr!)} r={r(l.balance) + (hover === l.loan_id ? 2 : 0)}
                  fill={STATUS_META[l.status].color} fillOpacity={0.85} stroke="var(--surface)" strokeWidth={2}
                  style={{ cursor: "pointer" }} tabIndex={0} role="link"
                  aria-label={`${l.loan_id} ${l.name}: DSCR ${ratio(l.dscr)}, LTV ${l.ltv_pct}%, ${STATUS_META[l.status].label}`}
                  onMouseEnter={() => setHover(l.loan_id)} onFocus={() => setHover(l.loan_id)}
                  onClick={() => router.push(`/debt/${l.loan_id}?period=${period}`)}
                  onKeyDown={(e) => { if (e.key === "Enter") router.push(`/debt/${l.loan_id}?period=${period}`); }} />
        ))}
      </svg>
      {h && (
        <div className="tooltip" style={{ left: `${(x(h.ltv_pct!) / W) * 100}%`, top: `${(y(h.dscr!) / H) * 100}%`,
                                          transform: `translate(${x(h.ltv_pct!) > W * 0.6 ? "-105%" : "12px"}, -50%)` }}>
          <div className="t-title">{h.loan_id} · {h.name}</div>
          <div className="t-row"><span>DSCR</span><b>{ratio(h.dscr)}</b></div>
          <div className="t-row"><span>LTV</span><b>{h.ltv_pct?.toFixed(1)}%</b></div>
          <div className="t-row"><span>Balance</span><b>{usd(h.balance, true)}</b></div>
          <div className="t-row"><span>Covenants</span><b>{STATUS_META[h.status].icon} {STATUS_META[h.status].label}</b></div>
        </div>
      )}
    </div>
  );
}
