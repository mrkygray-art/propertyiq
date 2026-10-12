// Loan screening API client. Kept separate from lib/api.ts so the module can be removed cleanly.
import { ApiError } from "@/lib/api";

export type Status = "PASS" | "FAIL" | "NOT CHECKED";

export interface DealForm {
  property_name: string; property_type: string; size: string; size_unit: "units" | "sf";
  value: string; pgi: string; other_income: string; vacancy_pct: string; operating_expenses: string; reserves: string;
  loan_amount: string; interest_rate_pct: string; amortization_years: string; interest_only_years: string; term_years: string;
}

export interface Metrics {
  egi: number; noi: number; monthly_payment: number | null; annual_debt_service: number | null;
  interest_only_debt_service: number | null; dscr: number | null; dscr_interest_only: number | null;
  ltv: number | null; debt_yield: number | null; cap_rate: number | null; break_even_occupancy: number | null;
  debt_constant: number | null;
}

export interface RuleResult {
  rule_id: string; label: string; status: Status; actual: number | string | null; threshold: number | string | null;
  kind: "ratio" | "pct" | "usd" | "text"; message: string;
}

export interface StressRow {
  key: string; label: string; noi: number; annual_debt_service: number | null; dscr: number | null; ltv: number | null;
  debt_yield: number | null; break_even_occupancy: number | null; value_used: number | null;
  interest_rate: number; vacancy: number; overall: Status; failed: string[];
}

export interface ScreenResult {
  disclaimer: string; generated_at: string; valid: boolean; errors: string[];
  guideline_set: { set_id: number; name: string; dscr_basis: string };
  assumptions: { label: string; value: string }[];
  metrics?: Metrics; warnings?: string[]; rules?: RuleResult[]; overall?: Status; stress?: StressRow[];
  grid?: { rates: number[]; vacancies: number[]; dscr: (number | null)[][]; min_dscr: number | null; base_rate: number; base_vacancy: number };
}

export interface RuleSetting { enabled: boolean; threshold: number | null }

export interface GuidelineSet {
  set_id: number; name: string; updated_at?: string;
  min_dscr: RuleSetting; max_ltv: RuleSetting; min_debt_yield: RuleSetting; max_break_even: RuleSetting;
  min_loan: RuleSetting; max_loan: RuleSetting;
  allowed_types_enabled: boolean; allowed_types: string[]; dscr_basis: "amortizing" | "interest_only";
}

export const PROPERTY_TYPES: [string, string][] = [
  ["multifamily", "Multifamily"], ["office", "Office"], ["retail", "Retail"],
  ["industrial", "Industrial"], ["mixed_use", "Mixed-use"], ["other", "Other"],
];

// Test case A from the Tier 1 spec, so a demo starts with a known-good deal.
export const EXAMPLE_DEAL: DealForm = {
  property_name: "Maple Run Apartments (synthetic)", property_type: "multifamily", size: "64", size_unit: "units",
  value: "6000000", pgi: "600000", other_income: "0", vacancy_pct: "5", operating_expenses: "170000", reserves: "0",
  loan_amount: "4000000", interest_rate_pct: "6.5", amortization_years: "30", interest_only_years: "0", term_years: "10",
};

export const EMPTY_DEAL: DealForm = {
  property_name: "", property_type: "multifamily", size: "", size_unit: "units", value: "", pgi: "", other_income: "0",
  vacancy_pct: "", operating_expenses: "", reserves: "0", loan_amount: "", interest_rate_pct: "", amortization_years: "30",
  interest_only_years: "0", term_years: "",
};

function token(): string | null {
  try { return window.sessionStorage.getItem("piq_token"); } catch { return null; }
}

async function call(path: string, init: RequestInit = {}): Promise<Response> {
  const t = token();
  const res = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...init.headers },
  });
  if (res.status === 401) {
    try { window.sessionStorage.removeItem("piq_token"); window.sessionStorage.removeItem("piq_user"); } catch { /* ignore */ }
    window.location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const b = await res.json(); detail = typeof b.detail === "string" ? b.detail : "Request was not valid"; } catch { /* keep */ }
    throw new ApiError(res.status, detail);
  }
  return res;
}

// Strip "$", commas and "%" so people can type numbers the way they write them.
const n = (s: string): number | null => {
  const clean = s.replace(/[$,%\s]/g, "");
  if (clean === "") return null;
  const v = Number(clean);
  return Number.isFinite(v) ? v : NaN;
};

export function dealPayload(f: DealForm) {
  return {
    property_name: f.property_name, property_type: f.property_type,
    size: n(f.size), size_unit: n(f.size) === null ? null : f.size_unit,
    value: n(f.value), pgi: n(f.pgi), other_income: n(f.other_income) ?? 0, vacancy_pct: n(f.vacancy_pct),
    operating_expenses: n(f.operating_expenses), reserves: n(f.reserves) ?? 0, loan_amount: n(f.loan_amount),
    interest_rate_pct: n(f.interest_rate_pct), amortization_years: n(f.amortization_years),
    interest_only_years: n(f.interest_only_years) ?? 0, term_years: n(f.term_years),
  };
}

export function badNumbers(f: DealForm): string[] {
  const labels: Record<string, string> = {
    size: "Units or square feet", value: "Property value", pgi: "Potential gross income", other_income: "Other income",
    vacancy_pct: "Vacancy", operating_expenses: "Operating expenses", reserves: "Replacement reserves",
    loan_amount: "Loan amount", interest_rate_pct: "Interest rate", amortization_years: "Amortization",
    interest_only_years: "Interest-only period", term_years: "Loan term",
  };
  return Object.entries(labels).filter(([k]) => Number.isNaN(n(f[k as keyof DealForm] as string))).map(([, l]) => `${l} must be a number.`);
}

export const screening = {
  status: async () => (await call("/api/screening/status")).json() as Promise<{ enabled: boolean; disclaimer: string }>,
  evaluate: async (f: DealForm, setId: number | null) =>
    (await call("/api/screening/evaluate", { method: "POST", body: JSON.stringify({ deal: dealPayload(f), guideline_set_id: setId }) })).json() as Promise<ScreenResult>,
  guidelines: async () => (await call("/api/screening/guidelines")).json() as Promise<GuidelineSet[]>,
  saveGuidelines: async (g: Omit<GuidelineSet, "set_id"> & { set_id?: number }) => {
    const { set_id, updated_at: _, ...body } = g as GuidelineSet;
    const res = await call(set_id ? `/api/screening/guidelines/${set_id}` : "/api/screening/guidelines",
      { method: set_id ? "PUT" : "POST", body: JSON.stringify(body) });
    return res.json() as Promise<GuidelineSet>;
  },
  deleteGuidelines: async (setId: number) => { await call(`/api/screening/guidelines/${setId}`, { method: "DELETE" }); },
  download: async (kind: "pdf" | "xlsx", f: DealForm, setId: number | null) => {
    const res = await call(`/api/screening/export/${kind}`, { method: "POST", body: JSON.stringify({ deal: dealPayload(f), guideline_set_id: setId }) });
    const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? `screening.${kind}`;
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};

// Display rules from the spec: dollars whole, ratios 2 decimals, percents 1 decimal.
export const fmt = {
  usd: (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${v < 0 ? "−" : ""}$${Math.round(Math.abs(v)).toLocaleString("en-US")}`),
  ratio: (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : v.toFixed(2)),
  pct: (v: number | null | undefined, places = 1) => (v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(places)}%`),
};

export function fmtRule(v: number | string | null, kind: RuleResult["kind"]): string {
  if (v === null) return "n/a";
  if (typeof v === "string") return v.replaceAll("_", "-");
  return kind === "ratio" ? fmt.ratio(v) : kind === "pct" ? fmt.pct(v) : kind === "usd" ? fmt.usd(v) : String(v);
}
