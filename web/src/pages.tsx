import { useEffect, useMemo, useState } from "react";
import { ago, api, ApiError, bytes, ms, occ, shortId, type Benchmark, type Ledger, type Member, type Network, type NodeView, type Receipt, type Usage } from "./lib/api";
import { useMesh } from "./lib/mesh";
import { Button, CopyButton, Empty, ErrorNote, Metric, NodeStatus, Panel, Qr, ReceiptView } from "./components";

export type Go = (path: string) => void;

function useFetch<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => { if (path) api<T>(path).then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message)); };
  useEffect(load, [path, ...deps]);
  return { data, error, reload: load };
}

/** Re-renders every `interval` ms (null = paused) so time-derived text stays current. */
function useTick(interval: number | null) {
  const [, setT] = useState(0);
  useEffect(() => {
    if (interval === null) return;
    const id = setInterval(() => setT((t) => t + 1), interval);
    return () => clearInterval(id);
  }, [interval]);
}

/* ---------------- Join ---------------- */

export function JoinPage({ code, onJoined }: { code: string; onJoined: (m: Member) => void }) {
  const { data: net } = useFetch<Network>("/api/v1/network");
  const [nickname, setNickname] = useState("");
  const [joinCode, setJoinCode] = useState(code);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await api<{ member: Member }>("/api/v1/session", { method: "POST", json: { nickname, join_code: joinCode } });
      onJoined(r.member);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  return (
    <main className="mx-auto grid min-h-dvh max-w-5xl content-center gap-10 px-5 py-10 md:grid-cols-[1.1fr_1fr]">
      <div>
        <Logo />
        <h1 className="mt-8 text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl">Run a prompt on someone else's computer and see where every millisecond went.</h1>
        <p className="mt-5 max-w-[60ch] text-lg text-muted">People in this room are sharing models from their own machines. Pick one, send a prompt, and get a receipt with real token counts, timings and credits.</p>
        <div className="mt-8 border-t border-line pt-5">
          {net ? (
            <>
              <p className="text-sm"><span className="text-2xl font-semibold">{net.online_node_count}</span> <span className="text-muted">{net.online_node_count === 1 ? "computer is" : "computers are"} sharing right now, with {net.member_count} {net.member_count === 1 ? "person" : "people"} joined</span></p>
              {net.models.length > 0 ? (
                <ul className="mt-3 flex flex-wrap gap-2">{net.models.map((m) => <li key={m.model_id} className="rounded-md border border-line bg-surface px-2.5 py-1 text-sm">{m.model_id} <span className="text-muted">on {m.worker_count}</span></li>)}</ul>
              ) : <p className="mt-2 text-sm text-muted">No models are being shared yet. You can still join and share your own.</p>}
            </>
          ) : <p className="text-sm text-muted">Checking the network…</p>}
        </div>
      </div>
      <form onSubmit={submit} className="self-center rounded-lg border border-line bg-surface p-6">
        <h2 className="text-xl font-semibold">Join the network</h2>
        <p className="mt-1 text-sm text-muted">No account needed. You get a demo session in this browser.</p>
        <label className="mt-6 block text-sm font-medium" htmlFor="nick">Nickname</label>
        <input id="nick" required maxLength={32} autoFocus value={nickname} onChange={(e) => setNickname(e.target.value)}
          className="mt-1.5 w-full rounded-md border border-line bg-surface px-3 py-2 text-base" placeholder="How others will see you" />
        <label className="mt-4 block text-sm font-medium" htmlFor="code">Join code</label>
        <input id="code" required value={joinCode} onChange={(e) => setJoinCode(e.target.value)} autoComplete="off"
          className="mt-1.5 w-full rounded-md border border-line bg-surface px-3 py-2 text-base" />
        {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}
        <Button className="mt-6 w-full py-2.5" disabled={busy || !nickname.trim() || !joinCode.trim()}>{busy ? "Joining…" : "Join"}</Button>
        <p className="mt-4 text-xs text-muted">This is a public demo. Prompts are processed on participants' own computers, so don't send anything private.</p>
      </form>
    </main>
  );
}

export function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden><path d="M6 6L26 6L16 26Z" stroke="#15201d" strokeWidth="2" fill="none" /><g fill="#2457d6"><circle cx="6" cy="6" r="4" /><circle cx="26" cy="6" r="4" /><circle cx="16" cy="26" r="4" /></g></svg>
      <span className="text-[17px] font-semibold tracking-tight">OpenComputeMesh</span>
    </div>
  );
}

/* ---------------- Overview ---------------- */

const EVENT_TEXT: Record<string, string> = { accepted: "submitted", dispatched: "dispatched to", first_token: "first token from", completed: "completed on", failed: "failed", cancelled: "cancelled", credit_settled: "settled credits for" };

export function Overview({ member, go }: { member: Member; go: Go }) {
  const { network } = useMesh();
  const { data: ledger } = useFetch<Ledger>("/api/v1/me/ledger?limit=5", [network]);
  const { data: usage } = useFetch<Usage>("/api/v1/me/usage", [network]);
  const { data: invite } = useFetch<{ join_path: string }>("/api/v1/network/join-info");
  const inviteUrl = invite ? `${location.origin}${invite.join_path}` : "";
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{network?.name ?? "Network"}</h1>
          <p className="mt-1 text-muted">{network ? `${network.online_node_count} of ${network.node_count} computers online, ${network.member_count} people joined` : "Connecting…"}</p>
        </div>
        <Button onClick={() => go("/run")} className="px-5 py-2.5">Run a model</Button>
      </header>
      <div className="grid gap-6 lg:grid-cols-3">
        <Panel title="Models available now" className="lg:col-span-2">
          {network?.models.length ? (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-normal">Model</th><th className="pb-2 font-normal">Size</th><th className="pb-2 font-normal">Workers</th><th /></tr></thead>
              <tbody>{network.models.map((m) => (
                <tr key={m.model_id} className="border-t border-line">
                  <td className="py-2.5 font-medium">{m.model_id}</td>
                  <td className="py-2.5 text-muted">{[m.parameter_size, m.quantization].filter(Boolean).join(", ") || "—"}</td>
                  <td className="py-2.5">{m.worker_count}</td>
                  <td className="py-2.5 text-right"><button className="text-mesh hover:underline" onClick={() => go(`/run?model=${encodeURIComponent(m.model_id)}`)}>Use</button></td>
                </tr>))}</tbody>
            </table>
          ) : <Empty>Nobody is sharing a model right now. <button className="text-mesh underline" onClick={() => go("/nodes")}>Share your compute</button> to get things started.</Empty>}
        </Panel>
        <Panel title="Invite people">
          {inviteUrl ? (
            <div className="flex flex-col items-center gap-3">
              <Qr text={inviteUrl} />
              <p className="break-all text-center text-xs text-muted">{inviteUrl}</p>
              <CopyButton text={inviteUrl} label="Copy invite link" />
            </div>
          ) : <p className="text-sm text-muted">Loading invite…</p>}
        </Panel>
        <Panel title="Your credits" action={<button className="text-sm text-mesh hover:underline" onClick={() => go("/usage")}>Ledger</button>}>
          <p className="text-3xl font-semibold">{occ(ledger?.balance_micro_occ ?? member.balance_micro_occ)} <span className="text-base font-normal text-muted">OCC</span></p>
          <ul className="mt-3 space-y-1.5 text-sm">
            {ledger?.entries.map((e) => <li key={e.id} className="flex justify-between gap-3"><span className="text-muted">{entryLabel(e.entry_type)}</span><span>{e.amount_micro_occ > 0 ? "+" : ""}{occ(e.amount_micro_occ)}</span></li>)}
          </ul>
        </Panel>
        <Panel title="You gave and used">
          {usage ? (
            <dl className="grid grid-cols-2 gap-4">
              <Metric label="Jobs you served" value={usage.contributed.total.jobs} />
              <Metric label="Jobs you ran" value={usage.consumed.total.jobs} />
              <Metric label="Tokens you processed" value={(usage.contributed.total.input_tokens + usage.contributed.total.output_tokens).toLocaleString()} />
              <Metric label="Tokens you used" value={(usage.consumed.total.input_tokens + usage.consumed.total.output_tokens).toLocaleString()} />
            </dl>
          ) : <p className="text-sm text-muted">Loading…</p>}
        </Panel>
        <Panel title="Live activity">
          {network?.activity?.length ? (
            <ol className="space-y-2 text-sm">
              {network.activity.slice(0, 8).map((a, i) => (
                <li key={i} className="flex gap-2"><span className="w-[4.5rem] shrink-0 text-xs leading-5 whitespace-nowrap text-muted">{new Date(a.occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
                  <span><span className="font-medium">{a.requester_nickname}</span>'s {a.model_id} request {EVENT_TEXT[a.event_type] ?? a.event_type}{a.node_name && ["dispatched", "first_token", "completed", "credit_settled"].includes(a.event_type) ? <> <span className="font-medium">{a.node_name}</span></> : null}</span></li>))}
            </ol>
          ) : <Empty>No requests yet.</Empty>}
        </Panel>
      </div>
    </div>
  );
}

export function entryLabel(t: string) {
  return ({ demo_grant: "Demo grant", reservation: "Hold placed", reservation_release: "Hold released", job_debit: "Paid for a request", provider_reward: "Earned serving a request", reversal: "Reversal", adjustment: "Adjustment" } as Record<string, string>)[t] ?? t;
}

/* ---------------- Run ---------------- */

type Run = { ref: string; requestId?: string; phase: "sending" | "queued" | "routed" | "running" | "done" | "failed" | "cancelled";
  model: string; nodeName?: string; reason?: string; output: string; chunks: number; receipt?: Receipt; error?: string; t0: number; tFirst?: number; tDone?: number };

const CONSENT_KEY = "ocm-consent-v1";

export function RunPage({ member, initialModel, go }: { member: Member; initialModel?: string; go: Go }) {
  const { network, send, subscribe, connected } = useMesh();
  const models = network?.models ?? [];
  const [model, setModel] = useState(initialModel ?? "");
  const [prompt, setPrompt] = useState("");
  const [maxTokens, setMaxTokens] = useState(256);
  const [temperature, setTemperature] = useState(0.7);
  const [target, setTarget] = useState("");
  const [consent, setConsent] = useState(() => localStorage.getItem(CONSENT_KEY) === "yes");
  const [run, setRun] = useState<Run | null>(null);
  const [bench, setBench] = useState<{ phase: string; result?: Benchmark; error?: string } | null>(null);
  const [eligible, setEligible] = useState<{ available: boolean; reason?: string; local_node?: string } | null>(null);

  useEffect(() => { if (!model && models.length) setModel(models[0].model_id); }, [models, model]);
  useEffect(() => {
    if (!model) return;
    api<typeof eligible>(`/api/v1/benchmarks/eligibility?model=${encodeURIComponent(model)}`).then(setEligible).catch(() => setEligible(null));
  }, [model, network]);

  useEffect(() => subscribe((m) => {
    if (m.type.startsWith("benchmark.")) {
      if (m.type === "benchmark.progress") setBench({ phase: m.stage === "local" ? `Running on your node ${m.node_name}…` : "Running on the shared network…" });
      if (m.type === "benchmark.completed") setBench({ phase: "done", result: m.benchmark });
      if (m.type === "benchmark.failed") setBench({ phase: "failed", error: m.message });
      return;
    }
    setRun((cur) => applyEvent(cur, m, performance.now()));
  }), [subscribe]);

  const params = () => ({ model, messages: [{ role: "user", content: prompt }], max_tokens: maxTokens, temperature });
  const start = () => {
    if (!consent) return;
    const ref = crypto.randomUUID();
    const r: Run = { ref, phase: "sending", model, output: "", chunks: 0, t0: performance.now() };
    setRun(r); setBench(null);
    if (!send({ type: "request.start", idempotency_key: ref, ...params(), ...(target ? { node_id: target } : {}) }))
      setRun({ ...r, phase: "failed", error: "Not connected to the coordinator. Reconnecting…" });
  };
  const busy = run && ["sending", "queued", "routed", "running"].includes(run.phase);
  const workers = (network?.nodes ?? []).filter((n) => n.status === "ONLINE" && n.models.some((x) => x.model_id === model));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Run a model</h1>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <Panel>
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); start(); }}>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-medium">Model
                <select value={model} onChange={(e) => { setModel(e.target.value); setTarget(""); }} disabled={!models.length}
                  className="mt-1.5 w-full rounded-md border border-line bg-surface px-3 py-2 font-normal">
                  {!models.length && <option>No models online</option>}
                  {models.map((m) => <option key={m.model_id} value={m.model_id}>{m.model_id} ({m.worker_count} worker{m.worker_count === 1 ? "" : "s"})</option>)}
                </select>
              </label>
              <label className="block text-sm font-medium">Worker
                <select value={target} onChange={(e) => setTarget(e.target.value)} className="mt-1.5 w-full rounded-md border border-line bg-surface px-3 py-2 font-normal">
                  <option value="">Best available worker</option>
                  {workers.map((n) => <option key={n.id} value={n.id}>{n.display_name} ({n.owner_nickname})</option>)}
                </select>
              </label>
            </div>
            <label className="block text-sm font-medium">Prompt
              <textarea required value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} maxLength={12000}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) start(); }}
                className="mt-1.5 w-full resize-y rounded-md border border-line bg-surface px-3 py-2 font-normal" placeholder="Explain what a GPU does, in two sentences." />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block text-sm font-medium">Max output tokens <span className="font-normal text-muted">{maxTokens}</span>
                <input type="range" min={16} max={2048} step={16} value={maxTokens} onChange={(e) => setMaxTokens(+e.target.value)} className="mt-2 w-full accent-mesh" />
              </label>
              <label className="block text-sm font-medium">Temperature <span className="font-normal text-muted">{temperature.toFixed(1)}</span>
                <input type="range" min={0} max={1.5} step={0.1} value={temperature} onChange={(e) => setTemperature(+e.target.value)} className="mt-2 w-full accent-mesh" />
              </label>
            </div>
            {!consent ? (
              <div className="rounded-md border border-warn/30 bg-[#fdf6ec] p-3 text-sm">
                <p>Your prompt will be processed by the selected computer's owner-operated model. Only send information you are comfortable sharing with that participant.</p>
                <Button type="button" variant="quiet" className="mt-2" onClick={() => { localStorage.setItem(CONSENT_KEY, "yes"); setConsent(true); }}>I understand</Button>
              </div>
            ) : null}
            {!models.length && network && (
              <div className="rounded-md border border-line bg-paper p-3 text-sm">
                <p>No models are online right now, so there is nothing to run. Anyone in the network can share a model from their own computer.</p>
                <button type="button" className="mt-1 font-medium text-mesh underline" onClick={() => go("/nodes?share=1")}>Go to Nodes, Share my compute</button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <Button disabled={!consent || !model || !prompt.trim() || !!busy || !connected || !models.length}>{busy ? "Running…" : "Run"}</Button>
              {busy && run?.requestId && <Button type="button" variant="quiet" onClick={() => send({ type: "request.cancel", request_id: run.requestId })}>Cancel</Button>}
              {!connected && <span className="flex items-center gap-1.5 text-sm text-warn"><span aria-hidden className="h-1.5 w-1.5 rounded-full bg-warn" />Reconnecting to the coordinator…</span>}
              <span className="text-xs text-muted">Balance {occ(member.balance_micro_occ)} OCC. A worst-case hold is placed, then you pay only for measured tokens.</span>
            </div>
          </form>
        </Panel>
        <Panel title={run ? <RunTitle run={run} /> : "Output"}>
          {!run ? <Empty>Output streams here token by token as the worker generates it.</Empty> : (
            <div className="space-y-5">
              {run.error && <ErrorNote>{run.error}</ErrorNote>}
              {busy && (
                <p className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-muted">
                  <Elapsed t0={run.t0} />
                  <span><span className="font-medium text-ink">{run.chunks}</span> {run.chunks === 1 ? "chunk" : "chunks"} received</span>
                  <span className="text-xs leading-5">Seen by this browser; runtime token counts arrive in the receipt.</span>
                </p>
              )}
              {(run.output || run.phase === "running") && (
                <pre className={`max-h-[50vh] overflow-auto whitespace-pre-wrap break-words font-mono text-[13.5px] leading-relaxed ${run.phase === "running" ? "caret" : ""}`}>{run.output}</pre>
              )}
              {run.receipt && run.receipt.status !== "CREATED" && (run.phase === "done" || run.phase === "failed" || run.phase === "cancelled") && (
                <div className="border-t border-line pt-5"><ReceiptView r={run.receipt} browser={{ e2e: run.tDone ? Math.round(run.tDone - run.t0) : null, ttft: run.tFirst ? Math.round(run.tFirst - run.t0) : null }} /></div>
              )}
            </div>
          )}
          <p className="sr-only" aria-live="polite">{run ? runText(run) : ""}</p>
        </Panel>
      </div>
      <Panel title="Compare with my local model">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-[70ch] text-sm text-muted">
            {eligible?.available ? `Runs this prompt once on your node ${eligible.local_node}, then once on a different worker with the same model digest. Both runs are charged like normal requests.` : eligible?.reason ?? "Pick a model to check whether a comparison is possible."}
          </p>
          <Button variant="quiet" disabled={!eligible?.available || !prompt.trim() || !consent || (bench !== null && !["done", "failed"].includes(bench.phase))}
            onClick={() => { setBench({ phase: "Starting…" }); send({ type: "benchmark.start", idempotency_key: crypto.randomUUID(), ...params() }); }}>Run comparison</Button>
        </div>
        {bench && !bench.result && <p className="mt-3 text-sm">{bench.error ? <span className="text-bad">{bench.error}</span> : bench.phase}</p>}
        {bench?.result && <BenchmarkView b={bench.result} />}
      </Panel>
    </div>
  );
}

function applyEvent(cur: Run | null, m: { type: string; [k: string]: any }, now: number): Run | null {
  if (!cur) return cur;
  if (m.client_ref === cur.ref && (m.type === "request.created" || m.type === "request.state"))
    return { ...cur, requestId: m.request_id, ...(m.receipt ? { phase: "done", receipt: m.receipt } : {}) };
  if (m.type === "request.failed" && m.client_ref === cur.ref && !m.request_id) return { ...cur, phase: "failed", error: m.message };
  if (m.client_ref === cur.ref && m.request_id && !cur.requestId) cur = { ...cur, requestId: m.request_id };
  if (!cur.requestId || m.request_id !== cur.requestId) return cur;
  switch (m.type) {
    case "request.queued": return { ...cur, phase: "queued" };
    case "request.routed": return { ...cur, phase: "routed", nodeName: m.node_name, reason: m.reason };
    case "request.started": return { ...cur, phase: "running" };
    case "request.token": return { ...cur, phase: "running", output: cur.output + m.text, chunks: cur.chunks + 1, tFirst: cur.tFirst ?? now };
    case "request.completed": return { ...cur, phase: "done", receipt: m.receipt, tDone: now };
    case "request.failed": return { ...cur, phase: "failed", error: m.message, receipt: m.receipt, tDone: now };
    case "request.cancelled": return { ...cur, phase: "cancelled", receipt: m.receipt, tDone: now };
  }
  return cur;
}

/** Owns the 100 ms ticker so only this span re-renders during a run. */
function Elapsed({ t0 }: { t0: number }) {
  useTick(100);
  return <span>Elapsed <span className="font-medium text-ink">{ms(Math.round(Math.max(0, performance.now() - t0)))}</span></span>;
}

function runText(run: Run) {
  return { sending: "Sending…", queued: "Waiting for a free worker…", routed: `Routed to ${run.nodeName}`, running: `Generating on ${run.nodeName ?? "worker"}`, done: `Done on ${run.nodeName ?? run.receipt?.node?.display_name ?? "worker"}`, failed: "Failed", cancelled: "Cancelled" }[run.phase];
}

function RunTitle({ run }: { run: Run }) {
  const text = runText(run);
  return <span className="flex items-center gap-2">{["sending", "queued", "routed", "running"].includes(run.phase) && <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-mesh" />}{text}</span>;
}

function BenchmarkView({ b }: { b: Benchmark }) {
  const card = (title: string, s: Benchmark["local"], extra?: React.ReactNode) => (
    <div className="rounded-md border border-line p-4">
      <h3 className="font-semibold">{title}</h3>
      <p className="text-xs text-muted">{s.node_name ?? "no worker"}; {s.status.toLowerCase()}{s.runtime_load_ms !== null ? (s.runtime_load_ms > 1000 ? "; cold start (model loaded)" : "; warm model") : ""}</p>
      <dl className="mt-3 grid grid-cols-2 gap-3">
        <Metric label="End-to-end" value={ms(s.end_to_end_ms)} />
        <Metric label="Runtime total" value={ms(s.runtime_total_ms)} />
        <Metric label="First token" value={ms(s.time_to_first_token_ms)} />
        <Metric label="Output speed" value={s.tokens_per_second ? `${s.tokens_per_second} tok/s` : null} />
        <Metric label="Tokens in / out" value={s.input_tokens !== null ? `${s.input_tokens} / ${s.output_tokens}` : null} />
        {extra}
      </dl>
    </div>
  );
  return (
    <div className="mt-4 grid gap-4 md:grid-cols-3">
      {card("Your local node", b.local)}
      {card("Shared network", b.remote, <><Metric label="Queue wait" value={ms(b.remote.queue_wait_ms)} /><Metric label="Credits" value={b.remote.settled_credits !== null ? `${occ(b.remote.settled_credits)} OCC` : null} /></>)}
      <div className="rounded-md bg-paper p-4">
        <h3 className="font-semibold">Observed difference</h3>
        {b.comparison_status === "comparable_single_run" && b.difference ? (
          <p className="mt-2 text-2xl font-semibold">{b.difference.end_to_end_ms > 0 ? "+" : ""}{ms(b.difference.end_to_end_ms)} <span className="text-base font-normal text-muted">({b.difference.percent > 0 ? "+" : ""}{b.difference.percent}%) for the network run</span></p>
        ) : <p className="mt-2 text-sm">Not comparable: one run failed, lacked token counts, or ran on mismatched models.</p>}
        {b.local.output_tokens !== null && b.remote.output_tokens !== null && b.local.output_tokens !== b.remote.output_tokens &&
          <p className="mt-3 text-sm">The two outputs differed in length ({b.local.output_tokens} vs {b.remote.output_tokens} tokens), so output speed is the fairer comparison. Use temperature 0 for closer outputs.</p>}
        <p className="mt-3 text-xs text-muted">{b.caveat}</p>
      </div>
    </div>
  );
}

/* ---------------- Nodes ---------------- */

export function NodesPage({ share = false }: { share?: boolean }) {
  const { network } = useMesh();
  useTick(5000);
  const nodes = network?.nodes ?? [];
  const [error, setError] = useState<string | null>(null);
  const act = async (n: NodeView, action: "pause" | "resume" | "revoke" | "refresh-models") => {
    setError(null);
    try {
      if (action === "revoke") await api(`/api/v1/nodes/${n.id}`, { method: "DELETE" });
      else await api(`/api/v1/nodes/${n.id}/${action}`, { method: "POST" });
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Nodes</h1>
      <ShareCompute initialOpen={share} />
      {error && <ErrorNote>{error}</ErrorNote>}
      {nodes.length === 0 ? <Empty>No computers have joined yet.</Empty> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {nodes.map((n) => (
            <Panel key={n.id} title={<span className="flex items-center gap-3">{n.display_name}{n.is_mine && <span className="rounded bg-mesh-soft px-1.5 py-0.5 text-xs font-medium text-mesh">Yours</span>}</span>} action={<NodeStatus status={n.status} />}>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <Field k="Owner" v={n.owner_nickname} />
                <Field k="Heartbeat" v={ago(n.last_heartbeat_at)} />
                <Field k="System" v={[n.platform, n.architecture].filter(Boolean).join(", ")} />
                <Field k="CPU" v={n.cpu_model ? `${n.cpu_model}${n.logical_cpu_count ? `, ${n.logical_cpu_count} threads` : ""}` : null} />
                <Field k="Memory" v={n.memory_total_bytes ? `${bytes(n.memory_available_bytes)} free of ${bytes(n.memory_total_bytes)}` : null} />
                <Field k="GPU" v={n.gpus.length ? n.gpus.map((g) => g.name).join(", ") : null} hint={n.gpus.map((g) => `source: ${g.source}`).join("; ")} />
                <Field k="Runtime" v={n.runtime_name ? `${n.runtime_name} ${n.runtime_version ?? ""}` : null} />
                <Field k="Load" v={`${n.active_jobs} of ${n.max_concurrent_jobs} job slots busy`} />
              </dl>
              <div className="mt-3 text-sm"><span className="text-muted">Shares </span>{n.models.length ? n.models.map((m) => m.model_id).join(", ") : "no models"}</div>
              <p className="mt-2 text-xs text-muted">Measured per job: worker wall time and runtime-reported tokens. CPU and GPU active time are not measured.</p>
              {n.is_mine && n.status !== "REVOKED" && (
                <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">
                  {n.sharing_enabled ? <Button variant="quiet" onClick={() => act(n, "pause")}>Pause sharing</Button> : <Button variant="quiet" onClick={() => act(n, "resume")}>Resume sharing</Button>}
                  <Button variant="quiet" disabled={!n.connected} onClick={() => act(n, "refresh-models")}>Refresh models</Button>
                  <Button variant="danger" onClick={() => act(n, "revoke")}>Remove node</Button>
                </div>
              )}
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({ k, v, hint }: { k: string; v: string | null | undefined; hint?: string }) {
  return <div title={hint}><dt className="text-xs text-muted">{k}</dt><dd>{v || <span className="text-muted">Unavailable</span>}</dd></div>;
}

function ShareCompute({ initialOpen }: { initialOpen: boolean }) {
  const [token, setToken] = useState<{ enrollment_token: string; expires_at: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(initialOpen);
  const origin = location.origin;
  const command = `uv run ocm-agent --coordinator ${origin} --name "my-laptop"`;
  const make = async () => {
    setError(null);
    try { setToken(await api("/api/v1/nodes/enrollment-tokens", { method: "POST" })); } catch (e) { setError((e as Error).message); }
  };
  return (
    <Panel title="Share my compute" action={<Button variant={open ? "quiet" : "primary"} onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide steps" : "Share my compute"}</Button>}>
      <p className="max-w-[75ch] text-sm text-muted">A small agent on your computer connects out to this coordinator and talks to Ollama on 127.0.0.1 only. Nothing on your machine is opened to the internet, models are never downloaded for you, and you choose which installed models to share. You can pause or remove the node at any time.</p>
      {open && (
        <ol className="mt-5 space-y-5 text-sm">
          <Step n={1} title="Have Ollama running with a model you've already pulled">
            <code className="block rounded bg-paper px-3 py-2 font-mono text-[13px]">ollama serve{"\n"}ollama pull qwen2.5:1.5b   # your choice; only if you want it</code>
          </Step>
          <Step n={2} title="Get a one-time enrollment token">
            {token ? (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2"><code className="rounded bg-paper px-3 py-2 font-mono text-[13px]">{token.enrollment_token}</code><CopyButton text={token.enrollment_token} /></div>
                <p className="text-xs text-muted">Single use, expires {new Date(token.expires_at).toLocaleTimeString()}. Paste it when the agent asks; it never goes in a URL or your shell history.</p>
              </div>
            ) : <Button variant="quiet" onClick={make}>Create token</Button>}
            {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}
          </Step>
          <Step n={3} title="Run the agent from a checkout of this repository">
            <div className="flex flex-wrap items-center gap-2"><code className="block grow rounded bg-paper px-3 py-2 font-mono text-[13px]">{command}</code><CopyButton text={command} /></div>
            <p className="mt-1.5 text-xs text-muted">It lists your installed models and asks which to share, then asks for the token. Your node appears below within a few seconds.</p>
          </Step>
        </ol>
      )}
    </Panel>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return <li className="grid grid-cols-[1.75rem_1fr] gap-2"><span className="flex h-6 w-6 items-center justify-center rounded-full border border-line text-xs font-semibold">{n}</span><div><p className="mb-2 font-medium">{title}</p>{children}</div></li>;
}

/* ---------------- Usage ---------------- */

export function UsagePage() {
  const { network } = useMesh();
  const { data: ledger, error } = useFetch<Ledger>("/api/v1/me/ledger?limit=100", [network]);
  const { data: usage } = useFetch<Usage>("/api/v1/me/usage", [network]);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">My usage</h1>
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="grid gap-6 lg:grid-cols-2">
        {usage && (["contributed", "consumed"] as const).map((side) => (
          <Panel key={side} title={side === "contributed" ? "Compute you contributed" : "Compute you used"}>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Metric label="Jobs" value={`${usage[side].total.completed} of ${usage[side].total.jobs} completed`} />
              <Metric label="Input tokens" value={usage[side].total.input_tokens.toLocaleString()} hint="Runtime-reported" />
              <Metric label="Output tokens" value={usage[side].total.output_tokens.toLocaleString()} hint="Runtime-reported" />
              <Metric label="Worker execution time" value={ms(usage[side].total.worker_ms) ?? "0 ms"} hint="Wall time measured by the node agent" />
              <Metric label={side === "contributed" ? "Credits earned" : "Credits spent"} value={`${occ(usage[side].total.settled_micro_occ)} OCC`} />
            </dl>
            {side === "contributed" && <p className="mt-3 text-xs text-muted">{usage.contributed.note}</p>}
          </Panel>
        ))}
      </div>
      <Panel title={<>Ledger <span className="font-normal text-muted">balance {occ(ledger?.balance_micro_occ)} OCC</span></>}>
        {ledger && <p className="mb-4 max-w-[80ch] text-sm text-muted">{ledger.explanation} Tariff {ledger.tariff.version}: {ledger.tariff.input_occ_per_1k} OCC per 1k input tokens, {ledger.tariff.output_occ_per_1k} per 1k output tokens ({ledger.tariff.label}).</p>}
        {ledger?.entries.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead className="text-left text-xs text-muted"><tr><th className="pb-2 font-normal">When</th><th className="pb-2 font-normal">Entry</th><th className="pb-2 font-normal">Request</th><th className="pb-2 text-right font-normal">OCC</th></tr></thead>
              <tbody>{ledger.entries.map((e) => (
                <tr key={e.id} className="border-t border-line">
                  <td className="py-2 text-muted">{new Date(e.created_at).toLocaleTimeString()}</td>
                  <td className="py-2">{entryLabel(e.entry_type)}</td>
                  <td className="py-2 text-muted">{e.job_id ? `${shortId(e.job_id)} ${e.model_id ?? ""}` : "—"}</td>
                  <td className={`py-2 text-right ${e.amount_micro_occ < 0 ? "text-bad" : "text-good"}`}>{e.amount_micro_occ > 0 ? "+" : ""}{occ(e.amount_micro_occ)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>No ledger entries.</Empty>}
      </Panel>
    </div>
  );
}

/* ---------------- Activity ---------------- */

export function ActivityPage({ member }: { member: Member }) {
  const { network } = useMesh();
  const { data, error } = useFetch<{ jobs: Receipt[] }>("/api/v1/jobs?limit=50", [network]);
  const [open, setOpen] = useState<string | null>(null);
  const jobs = useMemo(() => data?.jobs ?? [], [data]);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Requests</h1>
      <p className="text-sm text-muted">Requests you ran, and requests your nodes served. Prompts and outputs are not stored.</p>
      {error && <ErrorNote>{error}</ErrorNote>}
      {jobs.length === 0 ? <Empty>No requests yet.</Empty> : (
        <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
          {jobs.map((j) => (
            <li key={j.request_id}>
              <button className="flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left hover:bg-paper" onClick={() => setOpen(open === j.request_id ? null : j.request_id)} aria-expanded={open === j.request_id}>
                <span><span className="font-medium">{j.model_id}</span> <span className="text-sm text-muted">{j.requester_nickname === member.nickname ? "you ran" : `${j.requester_nickname} ran`} on {j.node?.display_name ?? "no worker"}{j.kind !== "request" ? ` (${j.kind.replace("_", " ")})` : ""}</span></span>
                <span className="flex items-center gap-4 text-sm"><span className="text-muted">{j.tokens.total !== null ? `${j.tokens.total} tokens` : ""}</span><span className="text-muted">{ms(j.timings_ms.coordinator_end_to_end)}</span><StatusText s={j.status} code={j.failure_code} /></span>
              </button>
              {open === j.request_id && <div className="border-t border-line px-4 py-4"><ReceiptView r={j} /></div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatusText({ s, code }: { s: string; code: string | null }) {
  const c = s === "COMPLETED" ? "text-good" : s === "FAILED" || s === "EXPIRED" ? "text-bad" : "text-muted";
  return <span className={c} title={code ?? undefined}>{s.charAt(0) + s.slice(1).toLowerCase()}</span>;
}

/* ---------------- Settings ---------------- */

export function SettingsPage({ member, onLeft }: { member: Member; onLeft: () => void }) {
  const [key, setKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const curl = `curl ${location.origin}/v1/chat/completions -H "Authorization: Bearer ${key ?? "<api key>"}" -H "content-type: application/json" -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"hi"}]}'`;
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
      <Panel title="API access">
        <p className="text-sm text-muted">Call the network from scripts with an OpenAI-style, non-streaming <code className="font-mono">/v1/chat/completions</code> endpoint. Usage is charged to you like web requests.</p>
        {key ? <div className="mt-3 flex flex-wrap items-center gap-2"><code className="rounded bg-paper px-3 py-2 font-mono text-[13px] break-all">{key}</code><CopyButton text={key} /><span className="text-xs text-muted">Shown once.</span></div>
          : <Button variant="quiet" className="mt-3" onClick={() => api<{ api_key: string }>("/api/v1/session/api-key", { method: "POST" }).then((r) => setKey(r.api_key)).catch((e: ApiError) => setError(e.message))}>{member.has_api_key ? "Replace API key" : "Create API key"}</Button>}
        <pre className="mt-3 overflow-x-auto rounded bg-paper px-3 py-2 font-mono text-[12px]">{curl}</pre>
        {error && <ErrorNote>{error}</ErrorNote>}
      </Panel>
      <Panel title="Leave this network">
        <p className="text-sm text-muted">Ends your session in this browser. Nodes you share keep running until you stop the agent or remove them.</p>
        <Button variant="danger" className="mt-3" onClick={() => api("/api/v1/session/leave", { method: "POST" }).then(onLeft)}>Leave network</Button>
      </Panel>
    </div>
  );
}
