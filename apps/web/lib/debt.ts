// Typed client for the debt dashboard API (synthetic loans). Every response is limited to the user's funds.
import { request } from "@/lib/api";

export type CovenantStatus = "pass" | "watch" | "breach" | "not_calculable";

export interface CovenantResult {
  kind: "min_dscr" | "max_ltv" | "min_debt_yield"; label: string; threshold: number; value: number | null;
  status: CovenantStatus; covenant_id: number;
}

export interface Cap { strike_pct: number; expiry_date: string; active: boolean }

export interface LoanRow {
  loan_id: string; name: string; fund_id: string; fund_name: string; lender: string; rate_type: "fixed" | "floating";
  rate_pct: number | null; balance: number | null; original_principal: number; maturity_date: string;
  months_to_maturity: number; balloon: number; noi: number | null; debt_service: number | null; value: number | null;
  dscr: number | null; ltv_pct: number | null; debt_yield_pct: number | null; covenants: CovenantResult[];
  status: CovenantStatus; collateral: { property_id: string; name: string; allocation_pct: number }[];
  cap: Cap | null; notes: string[];
}

export interface DebtSummary {
  synthetic_data: true; period: string; periods: string[];
  scope: { username: string; funds: string[]; loan_count: number };
  totals: {
    balance: number; fixed_balance: number; floating_balance: number; floating_share_pct: number | null;
    weighted_rate_pct: number | null; aggregate_dscr: number | null; aggregate_ltv_pct: number | null;
    breach_count: number; watch_count: number; maturing_12m_balance: number; maturing_24m_balance: number;
  };
  maturity_wall: { year: number; fixed: number; floating: number; count: number; loans: string[] }[];
  trend: { period: string; aggregate_dscr: number | null; aggregate_ltv_pct: number | null }[];
  loans: LoanRow[]; definitions: Record<string, string>; payload_sha256: string;
}

export interface LoanDetail {
  synthetic_data: true; period: string; periods: string[];
  loan: {
    loan_id: string; name: string; fund_id: string; fund_name: string; lender: string; rate_type: "fixed" | "floating";
    fixed_rate_pct: number | null; index_name: string | null; spread_bps: number | null; original_principal: number;
    amortization_months: number; io_months: number; origination_date: string; maturity_date: string; balloon: number;
    cap: Cap | null;
  };
  metrics: {
    balance: number | null; noi: number | null; value: number | null; debt_service: number | null; interest: number;
    principal: number; rate_pct: number | null; dscr: number | null; ltv_pct: number | null; debt_yield_pct: number | null;
    notes: string[];
  };
  covenants: CovenantResult[]; status: CovenantStatus;
  collateral: {
    property_id: string; name: string; asset_class: string; market: string; allocation_pct: number;
    ttm_noi: number | null; allocated_noi: number | null; value: number | null; allocated_value: number | null;
    valuation_date: string | null; valuation_id: number | null;
  }[];
  history: {
    period: string; dscr: number | null; ltv_pct: number | null; debt_yield_pct: number | null; noi: number | null;
    debt_service: number | null; balance: number | null; rate_pct: number | null; covenants: CovenantResult[];
  }[];
  sources: Record<string, number[]>; definitions: Record<string, string>; payload_sha256: string;
}

const q = (period?: string | null) => (period ? `?period=${encodeURIComponent(period)}` : "");

export const debtApi = {
  summary: (period?: string | null) => request<DebtSummary>(`/api/debt/summary${q(period)}`),
  loan: (id: string, period?: string | null) => request<LoanDetail>(`/api/debt/loans/${encodeURIComponent(id)}${q(period)}`),
};

export const STATUS_META: Record<CovenantStatus, { label: string; icon: string; chip: string; color: string }> = {
  breach: { label: "Breach", icon: "✕", chip: "rule", color: "var(--critical)" },
  watch: { label: "Watch", icon: "!", chip: "warn", color: "var(--warning)" },
  pass: { label: "Pass", icon: "✓", chip: "ok", color: "var(--good)" },
  not_calculable: { label: "Not calculable", icon: "–", chip: "neutral", color: "var(--text-3)" },
};

export const ratio = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toFixed(2)}×`);

export function covenantValue(c: CovenantResult): string {
  if (c.value === null) return "—";
  return c.kind === "min_dscr" ? ratio(c.value) : `${c.value.toFixed(2)}%`;
}

export function covenantThreshold(c: CovenantResult): string {
  return c.kind === "min_dscr" ? `≥ ${c.threshold.toFixed(2)}×` : c.kind === "max_ltv" ? `≤ ${c.threshold}%` : `≥ ${c.threshold}%`;
}
