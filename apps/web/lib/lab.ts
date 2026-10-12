// Typed client for the AI Engineering Lab API.
import { request } from "@/lib/api";

export interface Stage { stage: string; ms: number; tool?: string; [k: string]: unknown }

export interface TraceRow {
  trace_id: string; created_at: string; username: string; endpoint: string; period: string | null; intent: string;
  answer_source: "local_model" | "verified_template"; model: string | null; verification_status: string;
  numbers_checked: number; unsupported_numbers: number; cited_property_ids: string[]; total_ms: number; model_ms: number | null;
}

export interface Trace extends TraceRow {
  funds: string[]; source_record_ids: number[]; facts_sha256: string | null; question_chars: number;
  prompt_tokens: number | null; output_tokens: number | null; stages: Stage[]; notes: string[];
}

export interface TraceStats {
  total: number; model_answers: number; template_answers: number; rejected: number;
  p50_ms: number | null; p95_ms: number | null; oldest: string | null; retention_days: number;
}

export interface Overview {
  trace_stats: TraceStats; answer_types: { intent: string; facts_from: string }[]; chat_model: string;
  system_prompt: string; prompt_template: string; trace_policy: string;
}

export interface ModelInfo {
  name: string; role: string; family: string | null; parameters: string | null; quantization: string | null;
  format: string | null; size_gb: number; digest: string; modified: string; capabilities: string[];
  allowlisted: boolean; loaded_in_memory: boolean; cloud: boolean;
}

export interface Models {
  ollama_url: string; chat_model: string; embed_model: string; allowlist: string[]; reachable: boolean;
  ollama_version?: string; models: ModelInfo[];
}

export interface Tool {
  id: string; kind: string; used_by: string[]; scope: string; returns: string; module: string; function: string;
  parameters: string[]; doc: string; source_line: number;
}

export interface SchemaTable {
  name: string; note: string | null; rows: number | null;
  columns: { name: string; type: string; nullable: boolean }[];
  references: { column: string; table: string; ref_column: string }[];
}

export interface Schema {
  tables: SchemaTable[]; row_counts_shown: boolean;
  roles: { rolname: string; can_read: number; can_write: number }[];
}

export interface Evidence {
  file: string; label: string; at: string; mode: string | null; test_connection_succeeded: boolean | null;
  outside_connections: number; propertyiq_outside_connections: number; propertyiq_processes: string[];
}

export interface Offline {
  lockdown: { mode: string; since: string | null }; evidence: Evidence[]; trace_stats: TraceStats; listening: string;
}

export interface CompareRun {
  answer: string; answer_source: string; model: string | null; intent: string;
  verification: { status: string; numbers_checked: number; unsupported_numbers: string[] };
  facts_sha256: string | null; latency_ms: number; stages: Stage[]; notes: string[]; trace_id: string | null;
}

export interface Compare { period: string; model_off: CompareRun; model_on: CompareRun; same_facts: boolean; same_intent: boolean }

export const labApi = {
  overview: () => request<Overview>("/api/lab/overview"),
  traces: () => request<{ traces: TraceRow[]; scope: string }>("/api/lab/traces"),
  trace: (id: string) => request<Trace>(`/api/lab/traces/${encodeURIComponent(id)}`),
  models: () => request<Models>("/api/lab/models"),
  tools: () => request<{ tools: Tool[]; answer_types: { intent: string; facts_from: string }[] }>("/api/lab/tools"),
  schema: () => request<Schema>("/api/lab/schema"),
  offline: () => request<Offline>("/api/lab/offline"),
  compare: (question: string) => request<Compare>("/api/lab/compare", { method: "POST", body: JSON.stringify({ question }) }),
};

export const STAGE_LABEL: Record<string, string> = {
  scope: "Load the user's scope", route: "Pick the answer type", facts: "Build verified facts",
  model: "Local model drafts the wording", verify: "Check every number", answer: "Answer shown",
};
