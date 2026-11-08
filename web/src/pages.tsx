import { useEffect, useMemo, useState } from "react";
import { ago, api, ApiError, bytes, ms, occ, shortId, type Ledger, type Member, type Network, type NodeView, type Receipt, type Throughput, type Usage } from "./lib/api";
import { useMesh } from "./lib/mesh";
import { Button, CopyButton, Empty, ErrorNote, Metric, NodeStatus, Panel, Qr, ReceiptView, ThroughputChart } from "./components";

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

/** One page heading, so titles, descriptions and actions line up across pages. */
export function PageHeader({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {children && <p className="mt-1 max-w-[75ch] text-muted">{children}</p>}
      </div>
      {action}
    </header>
  );
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

/* ---------------- Machines ---------------- */

export function MachinesPage({ share = false }: { share?: boolean }) {
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
      <PageHeader title="Machines">Computers sharing models with this network. A browser can join without sharing anything; a machine is added with a small agent.</PageHeader>
      <InvitePanel />
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

function InvitePanel() {
  const { data: invite } = useFetch<{ join_path: string }>("/api/v1/network/join-info");
  const inviteUrl = invite ? `${location.origin}${invite.join_path}` : "";
  return (
    <Panel title="Invite people">
      {inviteUrl ? (
        <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center">
          <Qr text={inviteUrl} size={132} />
          <div className="min-w-0 space-y-2">
            <p className="break-all text-sm text-muted">{inviteUrl}</p>
            <CopyButton text={inviteUrl} label="Copy invite link" />
            <p className="text-xs text-muted">Opens the join page in any browser. Joining from a browser shares no compute.</p>
          </div>
        </div>
      ) : <p className="text-sm text-muted">Loading invite…</p>}
    </Panel>
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

/* ---------------- Activity ---------------- */

export function ActivityPage({ member }: { member: Member }) {
  const { network } = useMesh();
  const { data, error } = useFetch<{ jobs: Receipt[] }>("/api/v1/jobs?limit=50", [network]);
  const { data: throughput, error: throughputError } = useFetch<Throughput>("/api/v1/network/throughput?minutes=60", [network]);
  const [open, setOpen] = useState<string | null>(null);
  const jobs = useMemo(() => data?.jobs ?? [], [data]);
  return (
    <div className="space-y-8">
      <PageHeader title="Activity">Requests you ran and requests your nodes served, what moved in your ledger, and the last hour&apos;s throughput. Prompts and outputs are not stored.</PageHeader>
      <section aria-labelledby="requests-heading" className="space-y-3">
        <h2 id="requests-heading" className="text-lg font-semibold">Requests</h2>
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
      </section>
      <Credits />
      {throughputError ? <Panel title="Throughput (last hour)"><p className="text-sm text-muted">Time-series metrics unavailable: {throughputError}</p></Panel>
        : throughput?.enabled ? (
        <Panel title="Throughput (last hour)">
          <ThroughputChart buckets={throughput.buckets} minutes={throughput.minutes} />
          <p className="mt-2 text-xs text-muted">From Tiger Data continuous aggregate <code>ocm_job_metrics_1m</code></p>
        </Panel>
      ) : null}
    </div>
  );
}

function Credits() {
  const { network } = useMesh();
  const { data: ledger, error } = useFetch<Ledger>("/api/v1/me/ledger?limit=100", [network]);
  const { data: usage } = useFetch<Usage>("/api/v1/me/usage", [network]);
  return (
    <section id="credits" aria-labelledby="credits-heading" className="scroll-mt-6 space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="credits-heading" className="text-lg font-semibold">Credits</h2>
        {ledger && <p className="text-sm text-muted">Balance <span className="font-medium text-ink">{occ(ledger.balance_micro_occ)} OCC</span></p>}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {usage && (
        <div className="grid gap-6 lg:grid-cols-2">
          {(["contributed", "consumed"] as const).map((side) => (
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
      )}
      <Panel title="Ledger">
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
    </section>
  );
}

export function entryLabel(t: string) {
  return ({ demo_grant: "Demo grant", reservation: "Hold placed", reservation_release: "Hold released", job_debit: "Paid for a request", provider_reward: "Earned serving a request", reversal: "Reversal", adjustment: "Adjustment" } as Record<string, string>)[t] ?? t;
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
      <PageHeader title="Settings">Everything that is not the network itself: your API access and how to leave.</PageHeader>
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
