// Typed client for the PropertyIQ API. All calls are same-origin; Next forwards /api to 127.0.0.1:8000.

export type RuleStatus = "triggered" | "not_triggered" | "not_evaluated";

export interface User { username: string; display_name: string; role: string; portfolios: string[] }

export interface Rule {
  rule_id: string; status: RuleStatus; value: number | null; comparator: string; threshold: number; explanation: string;
}

export interface Metrics {
  occupancy_pct: number | null; noi_actual: number | null; noi_budget: number | null;
  budget_variance: number | null; budget_shortfall: number | null; noi_to_budget_pct: number | null;
  noi_prior: number | null; noi_yoy_pct: number | null; data_issues: string[];
}

export interface Calculation {
  period: string;
  current_ttm: { start: string; end: string };
  prior_ttm: { start: string; end: string };
  inputs: Record<string, number | string | null>;
  formulas: Record<string, string>;
  source_records: Record<string, number[]>;
  rule_set_version: string;
}

export interface Assessment {
  property_id: string; name: string; asset_class: string; portfolio_id: string; portfolio_name: string;
  market: string; size_unit: string; rentable_size: number;
  flagged: boolean; priority_rank: number | null; rules_triggered: string[]; data_complete: boolean;
  metrics: Metrics; rules: Rule[]; calculation: Calculation;
}

export interface RuleSet { version: string; logic: string; rules: { rule_id: string; condition: string }[]; priority_rule: string }

export interface Scope { username: string; portfolios: string[]; properties_evaluated: number }

export interface Underperforming {
  synthetic_data: true; period: string; rule_set: RuleSet; scope: Scope; flagged_count: number;
  total_budget_shortfall_flagged: number; flagged: Assessment[]; incomplete_data: string[]; note: string;
  payload_sha256: string;
}

export interface AssetClassSummary {
  asset_class: string; property_count: number; size_unit: string; occupancy_pct: number | null;
  noi_actual: number; noi_budget: number; budget_variance: number; flagged_count: number;
}

export interface Summary {
  synthetic_data: true; period: string; scope: Scope; property_count: number; flagged_count: number;
  total_budget_shortfall_flagged: number; noi_actual: number; noi_budget: number; budget_variance: number;
  noi_prior: number; noi_yoy_pct: number | null; average_property_occupancy_pct: number | null;
  by_asset_class: AssetClassSummary[]; rule_set_version: string; payload_sha256: string;
}

export interface Month {
  month_end: string; record_id: number; budget_record_id: number | null; operating_revenue: number;
  operating_expenses: number; noi: number; budget_noi: number | null; occupancy_pct: number;
}

export interface PropertyDetail { period: string; property: Assessment; rule_set: RuleSet; monthly: Month[]; payload_sha256: string }

export interface Periods { default_period: string | null; periods: string[]; definition: string }

export interface Citation { property_id: string; name: string; period: string; source_records: Record<string, number[]>; link: string }

export interface ChatAnswer {
  period: string; intent: string; answer: string; answer_source: "local_model" | "verified_template";
  model: string | null; verification: { status: "verified" | "rejected" | "not_applicable"; numbers_checked: number; unsupported_numbers: string[]; missing_properties: string[] };
  facts: Record<string, unknown>; citations: Citation[]; calculation_link: string;
  focus_property_id: string | null; notes: string[]; latency_ms: number;
}

export interface Health { status: "ok" | "degraded"; database: boolean; local_model: boolean }

export type CheckStatus = "pass" | "warn" | "fail" | "info";
export type LockdownMode = "off" | "app" | "computer";
export interface CheckItem { area: string; key: string; title: string; status: CheckStatus; detail: string; fix: string | null }
export interface FirewallStatus {
  mode: LockdownMode | "unknown"; rule_count?: number; since?: string | null; wsl_outbound?: string | null;
  firewall_products?: { name: string; enabled: boolean }[]; profiles?: { name: string; outbound: string }[];
  api_can_reach_internet?: boolean;
}
export interface Checklist { checked_at: string; counts: Record<CheckStatus, number>; lockdown: FirewallStatus; items: CheckItem[] }

const TOKEN_KEY = "piq_token";
const USER_KEY = "piq_user";

function storage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.sessionStorage; } catch { return null; }
}

export function getUser(): User | null {
  try { const raw = storage()?.getItem(USER_KEY); return raw ? (JSON.parse(raw) as User) : null; } catch { return null; }
}

export function signOut(): void {
  storage()?.removeItem(TOKEN_KEY);
  storage()?.removeItem(USER_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = storage()?.getItem(TOKEN_KEY);
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
    cache: "no-store",
  });
  if (res.status === 401 && path !== "/api/auth/login") {
    signOut();
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : "Request was not valid";
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export async function login(username: string, password: string): Promise<User> {
  const body = await request<{ token: string; user: User }>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ username, password }),
  });
  storage()?.setItem(TOKEN_KEY, body.token);
  storage()?.setItem(USER_KEY, JSON.stringify(body.user));
  return body.user;
}

const q = (period?: string | null) => (period ? `?period=${encodeURIComponent(period)}` : "");

export const api = {
  health: () => request<Health>("/health"),
  periods: () => request<Periods>("/api/periods"),
  summary: (period?: string | null) => request<Summary>(`/api/portfolio/summary${q(period)}`),
  underperforming: (period?: string | null) => request<Underperforming>(`/api/properties/underperforming${q(period)}`),
  property: (id: string, period?: string | null) => request<PropertyDetail>(`/api/properties/${encodeURIComponent(id)}${q(period)}`),
  chat: (question: string, period: string | null, contextPropertyId: string | null) =>
    request<ChatAnswer>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question, period, context_property_id: contextPropertyId }),
    }),
  lockdownBanner: () => request<{ mode: LockdownMode; since: string | null }>("/api/lockdown/status"),
  checklist: () => request<Checklist>("/api/admin/checklist"),
  lockdown: () => request<FirewallStatus>("/api/admin/lockdown"),
  setLockdown: (mode: LockdownMode) =>
    request<{ requested: LockdownMode; message: string }>("/api/admin/lockdown", { method: "POST", body: JSON.stringify({ mode }) }),
};
