// Tier 2 deal workspace client: deals, uploads, review, approval, analysis and flags.
import { ApiError } from "@/lib/api";

export type Confidence = "high" | "medium" | "low";
export type Severity = "high" | "medium" | "low";

export interface DocumentSummary {
  document_id: number; kind: "rent_roll" | "t12"; filename: string; status: "extracted" | "approved" | "failed";
  extraction_method: string | null; error: string | null; uploaded_at: string; approved_at: string | null;
  summary: {
    warnings?: string[]; months?: string[]; missing_months?: string[]; duplicate_months?: string[];
    reconciliation?: { kind: string; label: string; source: string; calculated: string; reported: string; difference: string; detail: string }[];
    columns_found?: string[]; totals_check?: string | null; table?: string;
  };
}

export interface Item {
  item_id: number; seq: number; item_type: "rent_roll_unit" | "t12_line"; source_ref: string; original_label: string;
  category: string | null; data: Record<string, unknown>; raw: { issues?: string[] }; confidence: Confidence;
  confidence_note: string; mapped_by: string; edited: boolean;
}

export interface Doc extends DocumentSummary { deal_id: number; items: Item[] }

export interface Deal {
  deal_id: number; name: string; guideline_set_id: number | null; created_by: string; updated_at: string;
  property_facts: Record<string, unknown>; borrower_inputs: Record<string, unknown>; documents: DocumentSummary[];
}

export interface DealRow { deal_id: number; name: string; updated_at: string; documents: number; open_flags: number; property_facts: Record<string, unknown> }

export interface Flag {
  flag_id: number; rule_id: string; fingerprint: string; severity: Severity; title: string; explanation: string;
  evidence: { label: string; value: string }[]; sources: string[]; suggested_action: string;
  status: "open" | "dismissed"; dismissal_note: string | null;
}

export interface Analysis {
  rent_roll: null | {
    unit_count: number; occupied_count: number; vacant_count: number; non_revenue_count: number; physical_occupancy: number | null;
    in_place_monthly: number; in_place_annual: number; gross_potential_annual: number; loss_to_lease_annual: number;
    by_type: { unit_type: string; units: number; occupied: number; avg_rent: number | null; avg_market: number | null; avg_sqft: number | null; rent_per_sqft: number | null }[];
    expirations: Record<string, number>; warnings: string[];
  };
  t12: null | {
    months: string[]; months_present: number; gross_potential_rent: number; effective_gross_income: number; recurring_egi: number;
    non_recurring_income: number; operating_expenses: number; noi: number; reported_vacancy_pct: number | null;
    expense_ratio: number | null; expense_per_unit: number | null; t3_income: number | null; t6_income: number | null;
    t3_expenses: number | null; t6_expenses: number | null; annual_by_category: Record<string, number>;
  };
  t12_info: null | { missing_months: string[]; reconciliation: unknown[] };
  flags: Flag[];
  category_meaning: Record<string, string>;
  category_section: Record<string, string>;
}

export interface Category { key: string; section: string; meaning: string }

function token(): string | null {
  try { return window.sessionStorage.getItem("piq_token"); } catch { return null; }
}

async function call<T>(path: string, init: RequestInit = {}, json = true): Promise<T> {
  const t = token();
  const res = await fetch(path, {
    ...init, cache: "no-store",
    headers: { ...(json ? { "Content-Type": "application/json" } : {}), ...(t ? { Authorization: `Bearer ${t}` } : {}), ...init.headers },
  });
  if (res.status === 401) { window.location.href = "/login"; }
  if (!res.ok) {
    let detail = res.statusText;
    try { const b = await res.json(); detail = typeof b.detail === "string" ? b.detail : "Request was not valid"; } catch { /* keep */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const ws = {
  categories: () => call<Category[]>("/api/screening/categories"),
  deals: () => call<DealRow[]>("/api/screening/deals"),
  deal: (id: number) => call<Deal>(`/api/screening/deals/${id}`),
  createDeal: (body: { name: string; property_facts: Record<string, unknown>; borrower_inputs: Record<string, unknown> }) =>
    call<Deal>("/api/screening/deals", { method: "POST", body: JSON.stringify(body) }),
  patchDeal: (id: number, body: Partial<Pick<Deal, "name" | "property_facts" | "borrower_inputs" | "guideline_set_id">>) =>
    call<Deal>(`/api/screening/deals/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteDeal: (id: number) => call(`/api/screening/deals/${id}`, { method: "DELETE" }),
  upload: (dealId: number, file: File, kind: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    return call<Doc>(`/api/screening/deals/${dealId}/documents`, { method: "POST", body: form }, false);
  },
  document: (id: number) => call<Doc>(`/api/screening/documents/${id}`),
  editItem: (docId: number, itemId: number, body: { category?: string; data?: Record<string, unknown> }) =>
    call<Doc>(`/api/screening/documents/${docId}/items/${itemId}`, { method: "PATCH", body: JSON.stringify(body) }),
  approve: (docId: number) => call<Doc>(`/api/screening/documents/${docId}/approve`, { method: "POST" }),
  unapprove: (docId: number) => call<Doc>(`/api/screening/documents/${docId}/unapprove`, { method: "POST" }),
  deleteDocument: (docId: number) => call(`/api/screening/documents/${docId}`, { method: "DELETE" }),
  analysis: (dealId: number) => call<Analysis>(`/api/screening/deals/${dealId}/analysis`),
  flagStatus: (flagId: number, status: "open" | "dismissed", note?: string) =>
    call(`/api/screening/flags/${flagId}/status`, { method: "POST", body: JSON.stringify({ status, note }) }),
};

export const usd = (v: unknown) => {
  const n = typeof v === "string" ? Number(v) : (v as number);
  return v === null || v === undefined || v === "" || Number.isNaN(n) ? "—" : `${n < 0 ? "−" : ""}$${Math.round(Math.abs(n)).toLocaleString("en-US")}`;
};
export const pctf = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
export const monthLabel = (m: string) => new Date(Date.UTC(+m.slice(0, 4), +m.slice(5) - 1, 1)).toLocaleString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });

// ---------------------------------------------------------------- Milestones 5-7

export interface Benchmark {
  benchmark_id: number; property_type: string; category: string; basis: "per_unit" | "per_sf" | "pct_egi";
  low: number | null; typical: number | null; high: number | null; source: string; last_reviewed: string; is_example: boolean;
}
export interface Rule {
  rule_key: string; flag_id: string; jurisdiction: string; name: string; trigger: Record<string, unknown>;
  description: string; source_url: string | null; last_verified: string | null; verified: boolean; notes: string;
}
export interface LifeRow {
  item_key: string; label: string; typical_life: number | null; flag_if: string | null; source: string; last_reviewed: string; is_example: boolean;
}
export interface Setting { key: string; value: string; description: string }
export interface Comp { source_id: string; address: string; distance_miles: number; url: string | null; [k: string]: unknown }
export interface MarketResult {
  result: { provider: string; provider_label: string; synthetic: boolean; retrieved_at: string; area: string; comps: Comp[];
    notes: string[]; cached: boolean; age_days: number; stale: boolean; stale_days: number };
  summary: unknown;
  implied?: { price: number | null; units: number | null; noi: number | null; price_per_unit: number | null; cap_rate: number | null };
}
export interface ConditionItem { item_key: string; details: Record<string, string | number | null>; estimated_cost: number | null }
export interface CaliforniaData {
  facts: Record<string, unknown>; condition: ConditionItem[]; life_table: LifeRow[]; rules: Rule[]; flags: Flag[];
}

const put = (body: unknown) => ({ method: "PUT", body: JSON.stringify(body) });

export const settingsApi = {
  benchmarks: () => call<Benchmark[]>("/api/screening/benchmarks"),
  addBenchmark: (b: Omit<Benchmark, "benchmark_id">) => call<Benchmark[]>("/api/screening/benchmarks", { method: "POST", body: JSON.stringify(b) }),
  saveBenchmark: (b: Benchmark) => { const { benchmark_id, ...rest } = b; return call<Benchmark[]>(`/api/screening/benchmarks/${benchmark_id}`, put(rest)); },
  deleteBenchmark: (id: number) => call<Benchmark[]>(`/api/screening/benchmarks/${id}`, { method: "DELETE" }),
  rules: () => call<Rule[]>("/api/screening/rules"),
  saveRule: (r: Rule) => call<Rule[]>(`/api/screening/rules/${r.rule_key}`, put(r)),
  life: () => call<LifeRow[]>("/api/screening/condition-life"),
  saveLife: (l: LifeRow) => call<LifeRow[]>(`/api/screening/condition-life/${l.item_key}`, put(l)),
  settings: () => call<Record<string, Setting>>("/api/screening/settings"),
  saveSetting: (key: string, value: string) => call<Record<string, Setting>>(`/api/screening/settings/${key}`, put({ value })),
};

export const marketApi = {
  comps: (dealId: number, body: { kind: "rent" | "sale"; address: string; city: string; radius_miles: number; max_age_months: number; refresh?: boolean }) =>
    call<MarketResult>(`/api/screening/deals/${dealId}/market`, { method: "POST", body: JSON.stringify(body) }),
  california: (dealId: number) => call<CaliforniaData>(`/api/screening/deals/${dealId}/california`),
  saveCalifornia: (dealId: number, facts: Record<string, unknown>, condition: ConditionItem[]) =>
    call<{ flags: Flag[] }>(`/api/screening/deals/${dealId}/california`, put({ facts, condition })),
};
