import { useEffect, useRef, useState } from "react";
import { api, occ, type Hold, type Member } from "./lib/api";
import { useMesh } from "./lib/mesh";
import { buildMessages, loadThreads, newThread, saveThreads, titleFor, type Thread, type Turn } from "./lib/threads";
import { Button, Empty, ErrorNote, ReceiptView } from "./components";

const CONSENT_KEY = "ocm-consent-v1";
const MAX_TURNS = 40;
const PHASE: Record<string, string> = { sending: "Sending…", queued: "Waiting for a worker…", routed: "Routed…", running: "Generating…" };

type Active = { ref: string; threadId: string; turnId: string; requestId?: string };

export function RunPage({ member, initialModel, go }: { member: Member; initialModel?: string; go: (p: string) => void }) {
  const { network, send, subscribe, connected } = useMesh();
  const models = network?.models ?? [];
  const maxPromptChars = network?.limits?.max_prompt_chars ?? 12000;
  const [threads, setThreads] = useState<Thread[]>(() => {
    const saved = loadThreads();
    return saved.length ? saved : [newThread(initialModel ?? "")];
  });
  const [activeId, setActiveId] = useState<string>(() => threads[0]?.id ?? "");
  const [draft, setDraft] = useState("");
  const [maxTokens, setMaxTokens] = useState(256);
  const [temperature, setTemperature] = useState(0.7);
  const [target, setTarget] = useState("");
  const [consent, setConsent] = useState(() => localStorage.getItem(CONSENT_KEY) === "yes");
  const [hold, setHold] = useState<Hold | null>(null);
  const [holdError, setHoldError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [omitted, setOmitted] = useState(0);
  const [busy, setBusy] = useState(false);
  const active = useRef<Active | null>(null);
  const thread = threads.find((t) => t.id === activeId) ?? threads[0]!;

  useEffect(() => {
    const first = initialModel || models[0]?.model_id;
    if (!first) return;
    setThreads((ts) => (ts.every((t) => t.model) ? ts : ts.map((t) => (t.model ? t : { ...t, model: first }))));
  }, [models, initialModel]);

  useEffect(() => { saveThreads(threads); }, [threads]);

  const patch = (threadId: string, turnId: string, fn: (t: Turn) => Turn) =>
    setThreads((ts) => ts.map((t) => (t.id === threadId ? { ...t, turns: t.turns.map((x) => (x.id === turnId ? fn(x) : x)) } : t)));
  const finish = () => { active.current = null; setBusy(false); };
  const fail = (message: string) => {
    const a = active.current;
    if (!a) return;
    patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "failed", error: message }));
    finish();
  };

  useEffect(() => subscribe((m) => {
    const a = active.current;
    if (!a) return;
    if (m.client_ref === a.ref && (m.type === "request.created" || m.type === "request.state")) {
      a.requestId = m.request_id;
      patch(a.threadId, a.turnId, (t) => ({
        ...t, requestId: m.request_id, phase: m.receipt ? "done" : "queued", ...(m.receipt ? { receipt: m.receipt } : {}),
      }));
      if (m.receipt) finish();
      return;
    }
    if (m.type === "request.failed" && m.client_ref === a.ref && !m.request_id) { fail(m.message); return; }
    if (!a.requestId || m.request_id !== a.requestId) return;
    switch (m.type) {
      case "request.queued": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "queued" })); break;
      case "request.routed": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "routed" })); break;
      case "request.started": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "running" })); break;
      case "request.token": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "running", text: t.text + m.text })); break;
      case "request.completed": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "done", receipt: m.receipt })); finish(); break;
      case "request.failed": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "failed", error: m.message, receipt: m.receipt })); finish(); break;
      case "request.cancelled": patch(a.threadId, a.turnId, (t) => ({ ...t, phase: "cancelled", receipt: m.receipt })); finish(); break;
    }
  }), [subscribe]);

  useEffect(() => {
    if (!thread.model || !draft.trim() || busy) { setHold(null); setHoldError(null); return; }
    const { messages } = buildMessages(thread.turns, draft.trim(), maxPromptChars);
    const timer = setTimeout(() => {
      api<Hold>("/api/v1/jobs/estimate", { method: "POST", json: { model: thread.model, messages, max_tokens: maxTokens, temperature } })
        .then((h) => { setHold(h); setHoldError(null); })
        .catch((e: Error) => { setHold(null); setHoldError(e.message); });
    }, 300);
    return () => clearTimeout(timer);
  }, [draft, maxTokens, temperature, thread.id, thread.model, thread.turns.length, busy, maxPromptChars]);

  const workers = (network?.nodes ?? []).filter((n) => n.status === "ONLINE" && n.models.some((x) => x.model_id === thread.model));
  const canSend = !!consent && !!draft.trim() && !busy && connected && models.length > 0 && !holdError && (hold ? hold.affordable : true);

  const start = () => {
    if (!thread || !canSend) return;
    const model = thread.model || models[0]?.model_id;
    if (!model) return;
    const text = draft.trim();
    const { messages, omitted: dropped } = buildMessages(thread.turns, text, maxPromptChars);
    const ref = crypto.randomUUID();
    const at = Date.now();
    const userTurn: Turn = { id: crypto.randomUUID(), role: "user", text, at };
    const replyTurn: Turn = { id: crypto.randomUUID(), role: "assistant", text: "", at, phase: "sending" };
    active.current = { ref, threadId: thread.id, turnId: replyTurn.id };
    setThreads((ts) => ts.map((t) => (t.id !== thread.id ? t : {
      ...t, model, title: t.turns.length ? t.title : titleFor(text),
      turns: [...t.turns, userTurn, replyTurn].slice(-MAX_TURNS),
    })));
    setOmitted(dropped);
    setDraft("");
    setRunError(null);
    setBusy(true);
    if (!send({ type: "request.start", idempotency_key: ref, model, messages, max_tokens: maxTokens, temperature, ...(target ? { node_id: target } : {}) })) {
      fail("Not connected to the coordinator. Reconnecting…");
    }
  };

  const cancel = () => {
    const requestId = active.current?.requestId;
    if (requestId) send({ type: "request.cancel", request_id: requestId });
  };

  const startNew = () => {
    if (busy) return;
    const fresh = newThread(models[0]?.model_id ?? thread.model);
    setThreads((ts) => [fresh, ...ts].slice(0, 20));
    setActiveId(fresh.id);
    setOmitted(0); setRunError(null); setHold(null); setHoldError(null);
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Chat</h1>
          <p className="mt-1 max-w-[70ch] text-muted">Follow-ups are resent with the next request. History stays in this browser; the coordinator stores no prompt.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Conversation" value={thread.id} disabled={busy} onChange={(e) => setActiveId(e.target.value)}
            className="max-w-[14rem] rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm">
            {threads.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
          <Button variant="quiet" onClick={startNew} disabled={busy}>New chat</Button>
        </div>
      </header>

      <section aria-label="Conversation" className="space-y-6 rounded-lg border border-line bg-surface p-4 md:p-5">
        {thread.turns.length === 0 ? (
          <Empty>
            Ask a question to start. The answer streams back with a receipt you can open under it.
            {!models.length && <> Nobody is sharing a model right now — <button className="text-mesh underline" onClick={() => go("/nodes?share=1")}>share your compute</button> to get started.</>}
          </Empty>
        ) : thread.turns.map((turn) => (
          <div key={turn.id} className={turn.role === "user" ? "flex justify-end" : ""}>
            {turn.role === "user" ? (
              <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-lg bg-mesh-soft px-4 py-2.5">{turn.text}</p>
            ) : (
              <div className="space-y-3">
                {(turn.phase && turn.phase !== "done") && <p className="text-xs text-muted">{PHASE[turn.phase] ?? turn.phase}</p>}
                {turn.text && <pre className={`whitespace-pre-wrap break-words font-sans text-[15px] leading-relaxed ${turn.phase === "running" ? "caret" : ""}`}>{turn.text}</pre>}
                {turn.error && <ErrorNote>{turn.error}</ErrorNote>}
                {turn.receipt && (
                  <details className="rounded-md border border-line px-3 py-2 text-sm">
                    <summary className="cursor-pointer text-muted">
                      Receipt · {turn.receipt.node?.display_name ?? "no worker"} · {turn.receipt.tokens.total ?? "?"} tokens
                    </summary>
                    <div className="pt-4"><ReceiptView r={turn.receipt} /></div>
                  </details>
                )}
              </div>
            )}
          </div>
        ))}
      </section>

      <form className="rounded-lg border border-line bg-surface p-4 md:p-5" onSubmit={(e) => { e.preventDefault(); start(); }}>
        <label className="block text-sm font-medium" htmlFor="prompt">Message</label>
        <textarea id="prompt" value={draft} onChange={(e) => setDraft(e.target.value)} rows={3} maxLength={12000}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); start(); } }}
          placeholder="Ask anything. Cmd/Ctrl + Enter sends."
          className="mt-1.5 w-full resize-y rounded-md border border-line bg-surface px-3 py-2" />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select aria-label="Model" value={thread.model} disabled={busy || thread.turns.length > 0}
            onChange={(e) => setThreads((ts) => ts.map((t) => (t.id === thread.id ? { ...t, model: e.target.value } : t)))}
            className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm disabled:text-muted">
            {models.length === 0 && <option value={thread.model}>{thread.model || "No models online"}</option>}
            {models.map((m) => <option key={m.model_id} value={m.model_id}>{m.model_id}</option>)}
            {models.length > 0 && thread.model && !models.some((m) => m.model_id === thread.model) &&
              <option value={thread.model}>{thread.model} (offline)</option>}
          </select>
          <details className="text-sm">
            <summary className="cursor-pointer rounded-md border border-line px-2.5 py-1.5">Options</summary>
            <div className="mt-2 w-72 space-y-3 rounded-md border border-line bg-surface p-3">
              <label className="block text-xs text-muted">Max output tokens <span className="text-ink">{maxTokens}</span>
                <input type="range" min={16} max={network?.limits?.max_output_tokens ?? 2048} step={16} value={maxTokens}
                  onChange={(e) => setMaxTokens(+e.target.value)} className="mt-1 w-full accent-mesh" />
              </label>
              <label className="block text-xs text-muted">Temperature <span className="text-ink">{temperature.toFixed(1)}</span>
                <input type="range" min={0} max={1.5} step={0.1} value={temperature}
                  onChange={(e) => setTemperature(+e.target.value)} className="mt-1 w-full accent-mesh" />
              </label>
              <label className="block text-xs text-muted">Worker
                <select value={target} onChange={(e) => setTarget(e.target.value)}
                  className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink">
                  <option value="">Best available worker</option>
                  {workers.map((n) => <option key={n.id} value={n.id}>{n.display_name} ({n.owner_nickname})</option>)}
                </select>
              </label>
            </div>
          </details>
          <span className="text-xs text-muted">
            {hold ? `Hold up to ${occ(hold.hold_micro_occ)} OCC · balance ${occ(hold.balance_micro_occ)}` : holdError ?? `Balance ${occ(member.balance_micro_occ)} OCC`}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {busy && <Button type="button" variant="quiet" onClick={cancel}>Cancel</Button>}
            <Button disabled={!canSend}>{busy ? "Running…" : "Send"}</Button>
          </div>
        </div>
        {hold && !hold.affordable && (
          <p className="mt-2 text-xs text-bad">This needs a hold of {occ(hold.hold_micro_occ)} OCC; your balance is {occ(hold.balance_micro_occ)} OCC.</p>
        )}
        {omitted > 0 && <p className="mt-2 text-xs text-muted">Earlier turns were not sent: the context limit is {maxPromptChars.toLocaleString()} characters.</p>}
        {!consent && (
          <div className="mt-3 rounded-md border border-warn/30 bg-[#fdf6ec] p-3 text-sm">
            <p>The selected computer's owner runs the model and receives the messages sent with each turn. Only send what you are comfortable sharing with that participant.</p>
            <Button type="button" variant="quiet" className="mt-2" onClick={() => { localStorage.setItem(CONSENT_KEY, "yes"); setConsent(true); }}>I understand</Button>
          </div>
        )}
        {runError && <div className="mt-2"><ErrorNote>{runError}</ErrorNote></div>}
        {!connected && <p className="mt-2 text-sm text-warn">Reconnecting to the coordinator…</p>}
      </form>
    </div>
  );
}
