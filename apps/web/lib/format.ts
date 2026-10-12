export function usd(v: number | null | undefined, compact = false): string {
  if (v === null || v === undefined) return "—";
  if (compact && Math.abs(v) >= 1_000_000_000) return `${v < 0 ? "−" : ""}$${(Math.abs(v) / 1_000_000_000).toFixed(2)}B`;
  if (compact && Math.abs(v) >= 1_000_000) return `${v < 0 ? "−" : ""}$${(Math.abs(v) / 1_000_000).toFixed(2)}M`;
  if (compact && Math.abs(v) >= 10_000) return `${v < 0 ? "−" : ""}$${Math.round(Math.abs(v) / 1000).toLocaleString("en-US")}K`;
  const s = Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
  return `${v < 0 ? "−" : ""}$${s}`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;
}

export function monthLabel(iso: string): string {
  const [y, m] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
}

export function size(v: number, unit: string): string {
  return unit === "units" ? `${v.toLocaleString("en-US")} units` : `${v.toLocaleString("en-US")} sf`;
}

// Short names only. Thresholds come from the API's rule set, which is configurable.
export const RULE_LABELS: Record<string, string> = {
  occupancy_below_min: "Low occupancy",
  noi_yoy_decline: "NOI YoY decline",
  noi_below_budget: "NOI below budget",
};

export const ASSET_LABELS: Record<string, string> = {
  office: "Office", retail: "Retail", industrial: "Industrial", multifamily: "Multifamily",
};
