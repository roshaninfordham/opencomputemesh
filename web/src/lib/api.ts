export type Member = { id: string; nickname: string; network_id: string; created_at: string; balance_micro_occ: number; has_api_key: boolean };
export type ModelInfo = { model_id: string; digest?: string | null; family?: string | null; parameter_size?: string | null; quantization?: string | null; size_bytes?: number | null };
export type NetModel = { model_id: string; worker_count: number; digests?: string[]; family?: string | null; parameter_size?: string | null; quantization?: string | null; node_ids?: string[] };
export type NodeView = {
  id: string; display_name: string; owner_nickname: string | null; is_mine: boolean; status: string; connected: boolean;
  platform: string | null; architecture: string | null; cpu_model: string | null; logical_cpu_count: number | null;
  memory_total_bytes: number | null; memory_available_bytes: number | null;
  gpus: { name: string; memory_total_bytes: number | null; source: string }[];
  runtime_name: string | null; runtime_version: string | null; models: ModelInfo[]; sharing_enabled: boolean;
  max_concurrent_jobs: number; active_jobs: number; last_heartbeat_at: string | null; created_at: string;
};
export type Activity = { occurred_at: string; event_type: string; job_id: string; model_id: string; kind: string; node_name: string | null; requester_nickname: string | null };
export type Network = {
  id?: string; name: string; online_node_count: number; node_count?: number; member_count: number; demo_mode: boolean;
  models: NetModel[]; nodes?: NodeView[]; activity?: Activity[];
};
export type LedgerEntry = { id: number; entry_type: string; amount_micro_occ: number; created_at: string; nickname?: string; job_id?: string | null; model_id?: string | null };
export type Receipt = {
  request_id: string; kind: string; status: string; failure_code: string | null; model_id: string; model_digest: string | null;
  node: { id: string; display_name: string; owner_nickname: string | null; runtime: string } | null;
  requester_nickname: string; route_reason: string | null;
  tokens: { input: number | null; output: number | null; total: number | null; source: string | null };
  timings_ms: { coordinator_end_to_end: number | null; queue_wait: number | null; time_to_first_token: number | null; worker_elapsed: number | null;
    runtime_total: number | null; runtime_load: number | null; prompt_eval: number | null; generation: number | null };
  tokens_per_second: number | null; retry_count: number; cancel_effect: string | null;
  timestamps: Record<string, string | null>;
  credits: { tariff: { version: string; input_occ_per_1k: string; output_occ_per_1k: string; min_charge_occ: string } | null;
    reserved_micro_occ: number; settled_micro_occ: number | null; measured_micro_occ: number | null; capped: boolean; settlement_status: string; entries: LedgerEntry[] };
  hosted_api_equivalent: { configured: boolean; available?: boolean; message?: string; usd?: string; provider?: string; provider_model_id?: string; source_url?: string; source_checked_at?: string; label?: string };
};
export type UsageSide = { by_model: { model_id: string; jobs: number; completed: number; input_tokens: number; output_tokens: number; worker_ms: number; settled_micro_occ: number }[];
  total: { jobs: number; completed: number; input_tokens: number; output_tokens: number; worker_ms: number; settled_micro_occ: number } };
export type Usage = { consumed: UsageSide; contributed: UsageSide & { note: string } };
export type Ledger = { balance_micro_occ: number; totals_by_type: Record<string, number>; entries: LedgerEntry[]; explanation: string;
  tariff: { version: string; input_occ_per_1k: string; output_occ_per_1k: string; label: string } };
export type BenchSide = { id: string; status: string; node_name: string | null; input_tokens: number | null; output_tokens: number | null; end_to_end_ms: number | null;
  queue_wait_ms: number | null; time_to_first_token_ms: number | null; runtime_total_ms: number | null; runtime_load_ms: number | null; tokens_per_second: number | null;
  settled_credits: number | null; receipt: Receipt };
export type Benchmark = { id: string; model_id: string; model_digest: string | null; comparison_status: string; local: BenchSide; remote: BenchSide;
  difference: { end_to_end_ms: number; percent: number } | null; caveat: string; created_at: string };
export type ThroughputBucket = { bucket: string; model_id: string; jobs: number; completed: number; input_tokens: number | null; output_tokens: number | null;
  avg_ttft_ms: number | null; p95_ttft_ms: number | null; avg_e2e_ms: number | null; p95_e2e_ms: number | null; avg_tokens_per_second: number | null };
export type Throughput = { enabled: false } | { enabled: true; source: string; minutes: number; buckets: ThroughputBucket[]; buffered_rows: number; dropped_rows: number };

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number, public details?: unknown) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  let body = init?.body;
  if (init?.json !== undefined) { headers["content-type"] = "application/json"; body = JSON.stringify(init.json); }
  const res = await fetch(path, { ...init, headers, body, credentials: "same-origin" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = data?.error ?? {};
    throw new ApiError(e.code ?? "HTTP_ERROR", e.message ?? `Request failed (${res.status})`, res.status, e.details);
  }
  return data as T;
}

export const occ = (micro: number | null | undefined) => {
  if (micro === null || micro === undefined) return "—";
  const v = micro / 1_000_000;
  return v.toLocaleString(undefined, { maximumFractionDigits: 6 });
};
export const ms = (v: number | null | undefined) =>
  v === null || v === undefined ? null : v >= 10_000 ? `${(v / 1000).toFixed(1)} s` : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v} ms`;
export const bytes = (b: number | null | undefined) => (b ? `${(b / 1024 ** 3).toFixed(b > 10 * 1024 ** 3 ? 0 : 1)} GB` : null);
export const ago = (iso: string | null) => {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
export const shortId = (id: string) => id.slice(0, 8);
