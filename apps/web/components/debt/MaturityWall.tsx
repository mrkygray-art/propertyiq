"use client";

import { useState } from "react";
import { usd } from "@/lib/format";
import type { DebtSummary } from "@/lib/debt";

// Principal due at maturity, by year, stacked fixed (bottom) and floating (top). Custom SVG, no chart library.
export default function MaturityWall({ data }: { data: DebtSummary["maturity_wall"] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 250, pad = { top: 16, right: 12, bottom: 30, left: 56 };
  const innerW = W - pad.left - pad.right, innerH = H - pad.top - pad.bottom;
  const max = Math.max(1, ...data.map((d) => d.fixed + d.floating));
  const step = niceStep(max / 4);
  const top = Math.ceil(max / step) * step;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const y = (v: number) => pad.top + innerH - (v / top) * innerH;
  const slot = innerW / Math.max(1, data.length);
  const bw = Math.min(56, slot * 0.6);
  const hovered = hover === null ? null : data[hover];

  return (
    <div style={{ position: "relative" }}>
      <div className="legend" aria-hidden="true">
        <span><span className="swatch" style={{ background: "var(--series-1)", height: 10 }} />Fixed rate</span>
        <span><span className="swatch" style={{ background: "var(--series-2)", height: 10 }} />Floating rate</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" className="chart"
           aria-label={`Maturity wall: principal due at maturity by year. ${data.map((d) => `${d.year}: ${usd(d.fixed + d.floating, true)}`).join("; ")}`}
           onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="gridline" x1={pad.left} x2={pad.left + innerW} y1={y(t)} y2={y(t)} />
            <text className="axis" x={pad.left - 6} y={y(t) + 4} textAnchor="end">{usd(t, true).replace(".00M", "M")}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = pad.left + slot * i + slot / 2;
          const yFixed = y(d.fixed), yTotal = y(d.fixed + d.floating);
          const gap = d.fixed > 0 && d.floating > 0 ? 2 : 0;
          return (
            <g key={d.year} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
               aria-label={`${d.year}: fixed ${usd(d.fixed, true)}, floating ${usd(d.floating, true)}, ${d.count} loans`}>
              <rect x={pad.left + slot * i} y={pad.top} width={slot} height={innerH} fill="transparent" />
              {d.fixed > 0 && <Bar x={cx - bw / 2} w={bw} y0={y(0)} y1={yFixed} round={d.floating === 0} color="var(--series-1)" />}
              {d.floating > 0 && <Bar x={cx - bw / 2} w={bw} y0={yFixed - gap} y1={yTotal} round color="var(--series-2)" />}
              <text className="axis" x={cx} y={H - 10} textAnchor="middle" style={{ fontWeight: hover === i ? 650 : 400 }}>{d.year}</text>
            </g>
          );
        })}
      </svg>
      {hovered && (
        <div className="tooltip" style={{ left: `${((pad.left + slot * hover! + slot / 2) / W) * 100}%`, top: 24, transform: "translateX(-50%)" }}>
          <div className="t-title">{hovered.year} · {hovered.count} loan{hovered.count === 1 ? "" : "s"}</div>
          <div className="t-row"><span>Fixed</span><b>{usd(hovered.fixed, true)}</b></div>
          <div className="t-row"><span>Floating</span><b>{usd(hovered.floating, true)}</b></div>
          <div className="t-row"><span>Total due</span><b>{usd(hovered.fixed + hovered.floating, true)}</b></div>
        </div>
      )}
      <details className="hint" style={{ marginTop: 6 }}>
        <summary>Show as table</summary>
        <table>
          <thead><tr><th>Year</th><th className="num">Loans</th><th className="num">Fixed</th><th className="num">Floating</th><th className="num">Total due</th></tr></thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.year}><td>{d.year}</td><td className="num">{d.count}</td><td className="num">{usd(d.fixed)}</td>
                <td className="num">{usd(d.floating)}</td><td className="num">{usd(d.fixed + d.floating)}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

function Bar({ x, w, y0, y1, round, color }: { x: number; w: number; y0: number; y1: number; round: boolean; color: string }) {
  const h = Math.max(0, y0 - y1);
  if (h <= 0) return null;
  const r = round ? Math.min(4, h, w / 2) : 0;
  const d = `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`;
  return <path d={d} fill={color} />;
}

function niceStep(raw: number): number {
  const mag = 10 ** Math.floor(Math.log10(raw || 1));
  const e = raw / mag;
  return (e >= 5 ? 10 : e >= 2 ? 5 : e >= 1 ? 2 : 1) * mag;
}
