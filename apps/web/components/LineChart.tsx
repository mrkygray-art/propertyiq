"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { monthLabel } from "@/lib/format";

export interface Series {
  key: string;
  label: string;
  color: string; // a CSS variable, e.g. var(--series-1)
  dashed?: boolean;
  values: (number | null)[];
}

interface Props {
  labels: string[]; // ISO month ends
  series: Series[];
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  ariaLabel: string;
  height?: number;
  threshold?: { value: number; label: string };
  bandFrom?: number; // shade from this index to the end (current TTM)
  bandLabel?: string;
}

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const err = raw / mag;
  const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
  const ticks: number[] = [];
  for (let v = Math.floor(min / step) * step; v <= Math.ceil(max / step) * step + step / 2; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

export default function LineChart({
  labels, series, format, axisFormat, ariaLabel, height = 240, threshold, bandFrom, bandLabel,
}: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const directLabels = series.length <= 4 && width >= 480;
  const pad = { top: 12, right: directLabels ? 72 : 12, bottom: 26, left: 58 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  const { ticks, y } = useMemo(() => {
    const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
    if (threshold) all.push(threshold.value);
    const lo = Math.min(...all);
    const hi = Math.max(...all);
    const pad5 = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1;
    const t = niceTicks(lo - pad5, hi + pad5);
    const min = t[0];
    const max = t[t.length - 1];
    return { ticks: t, y: (v: number) => pad.top + innerH - ((v - min) / (max - min)) * innerH };
  }, [series, threshold, innerH, pad.top]);

  const n = labels.length;
  const x = (i: number) => pad.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);

  const path = (values: (number | null)[]) => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const xTickEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 70))));

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const rect = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - rect.left;
    const i = Math.round(((px - pad.left) / innerW) * (n - 1));
    setHover(Math.min(n - 1, Math.max(0, i)));
  }

  // Direct labels at the right end; nudge apart if they would collide.
  const ends = series
    .map((s) => {
      const last = [...s.values].reverse().find((v) => v !== null);
      return last === undefined || last === null ? null : { s, yv: y(last) };
    })
    .filter((e): e is { s: Series; yv: number } => e !== null)
    .sort((a, b) => a.yv - b.yv);
  for (let i = 1; i < ends.length; i++) if (ends[i].yv - ends[i - 1].yv < 14) ends[i].yv = ends[i - 1].yv + 14;

  const tipLeft = hover === null ? 0 : Math.min(Math.max(x(hover) + 12, 0), width - 170);

  return (
    <div className="chart" ref={wrap}>
      {series.length >= 2 && (
        <div className="legend" aria-hidden="true">
          {series.map((s) => (
            <span key={s.key}>
              <span className={`swatch${s.dashed ? " dashed" : ""}`} style={{ background: s.color, color: s.color }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      <svg height={height} role="img" aria-label={ariaLabel} onPointerLeave={() => setHover(null)}>
        {bandFrom !== undefined && bandFrom < n && (
          <g>
            <rect x={x(bandFrom) - (n > 1 ? innerW / (n - 1) / 2 : 0)} y={pad.top} width={pad.left + innerW - x(bandFrom) + (n > 1 ? innerW / (n - 1) / 2 : 0)} height={innerH} fill="var(--surface-2)" />
            {bandLabel && <text x={pad.left + innerW - 4} y={pad.top + 12} textAnchor="end" className="threshold-label" style={{ fill: "var(--text-3)", fontSize: 11 }}>{bandLabel}</text>}
          </g>
        )}
        <g className="axis">
          {ticks.map((t) => (
            <g key={t}>
              <line className="gridline" x1={pad.left} x2={pad.left + innerW} y1={y(t)} y2={y(t)} />
              <text x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end">{(axisFormat ?? format)(t)}</text>
            </g>
          ))}
          {labels.map((l, i) => (i % xTickEvery === (n - 1) % xTickEvery ? (
            <text key={l} x={x(i)} y={height - 6} textAnchor="middle">{monthLabel(l)}</text>
          ) : null))}
        </g>
        {threshold && (
          <g>
            <line className="threshold" x1={pad.left} x2={pad.left + innerW} y1={y(threshold.value)} y2={y(threshold.value)} />
            <text className="threshold-label" x={pad.left + innerW - 4} y={y(threshold.value) - 5} textAnchor="end">{threshold.label}</text>
          </g>
        )}
        {series.map((s) => (
          <path key={s.key} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={2}
            strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dashed ? "6 4" : undefined} />
        ))}
        {directLabels && ends.map(({ s, yv }) => (
          <text key={s.key} x={pad.left + innerW + 8} y={yv} dy="0.32em" style={{ fill: "var(--text-2)", fontSize: 11 }}>{s.label}</text>
        ))}
        {hover !== null && (
          <g>
            <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} />
            {series.map((s) => {
              const v = s.values[hover];
              return v === null ? null : (
                <circle key={s.key} cx={x(hover)} cy={y(v)} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              );
            })}
          </g>
        )}
        <rect x={pad.left} y={pad.top} width={innerW} height={innerH} fill="transparent" onPointerMove={onMove} onPointerDown={onMove} />
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: tipLeft, top: 28 }}>
          <div className="t-title">{monthLabel(labels[hover])}</div>
          {series.map((s) => (
            <div className="t-row" key={s.key}>
              <span><span className={`swatch${s.dashed ? " dashed" : ""}`} style={{ display: "inline-block", width: 10, height: 2, background: s.color, marginRight: 6, verticalAlign: "middle" }} />{s.label}</span>
              <b>{s.values[hover] === null ? "—" : format(s.values[hover] as number)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
