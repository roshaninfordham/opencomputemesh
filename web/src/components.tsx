import { useEffect, useState, type ReactNode } from "react";
import QRCode from "qrcode";
import { ms, occ, type Receipt } from "./lib/api";

export function Button({ variant = "primary", className = "", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "quiet" | "danger" }) {
  const v = {
    primary: "bg-mesh text-white hover:bg-[#1c47b3] disabled:bg-[#9db1e6]",
    quiet: "bg-surface text-ink border border-line hover:border-ink/40 disabled:text-muted",
    danger: "bg-surface text-bad border border-bad/30 hover:border-bad",
  }[variant];
  return <button {...p} className={`inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${v} ${className}`} />;
}

const STATUS: Record<string, string> = { ONLINE: "bg-good", PAUSED: "bg-warn", DEGRADED: "bg-warn", OFFLINE: "bg-seg-queue", PENDING: "bg-seg-queue", REVOKED: "bg-bad" };
export function NodeStatus({ status }: { status: string }) {
  const label = status.charAt(0) + status.slice(1).toLowerCase();
  return <span className="inline-flex items-center gap-1.5 text-sm"><span aria-hidden className={`h-2 w-2 rounded-full ${STATUS[status] ?? "bg-seg-queue"}`} />{label}</span>;
}

export function Panel({ title, action, children, className = "" }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-line bg-surface ${className}`}>
      {title && <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3"><h2 className="text-[15px] font-semibold">{title}</h2>{action}</header>}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Metric({ label, value, hint, unavailable }: { label: string; value: ReactNode; hint?: string; unavailable?: string }) {
  return (
    <div title={hint}>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold leading-tight">{value ?? <span className="text-sm font-normal text-muted">{unavailable ?? "Unavailable"}</span>}</dd>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</p>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <p role="alert" className="rounded-md border border-bad/30 bg-[#fdf1f1] px-3 py-2 text-sm text-bad">{children}</p>;
}

export function Qr({ text, size = 168 }: { text: string; size?: number }) {
  const [svg, setSvg] = useState("");
  useEffect(() => { QRCode.toString(text, { type: "svg", margin: 1, color: { dark: "#15201d", light: "#ffffff" } }).then(setSvg); }, [text]);
  return <div role="img" aria-label={`QR code for ${text}`} style={{ width: size, height: size }} dangerouslySetInnerHTML={{ __html: svg }} />;
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <Button variant="quiet" type="button" onClick={() => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}>{done ? "Copied" : label}</Button>;
}

/** Where the time went, drawn only from measured segments. */
export function Waterfall({ r, browserMs }: { r: Receipt; browserMs?: number | null }) {
  const t = r.timings_ms;
  const total = t.coordinator_end_to_end;
  if (!total) return null;
  const known = (t.queue_wait ?? 0) + (t.runtime_load ?? 0) + (t.prompt_eval ?? 0) + (t.generation ?? 0);
  const segs = [
    { k: "Queue wait", v: t.queue_wait, c: "bg-seg-queue", d: "Coordinator: queued until dispatched" },
    { k: "Model load", v: t.runtime_load, c: "bg-seg-load", d: "Runtime-reported load_duration" },
    { k: "Prompt eval", v: t.prompt_eval, c: "bg-seg-prompt", d: "Runtime-reported prompt_eval_duration" },
    { k: "Generation", v: t.generation, c: "bg-seg-gen", d: "Runtime-reported eval_duration" },
    { k: "Transport & coordination", v: Math.max(0, total - known), c: "bg-seg-other", d: "Remainder of coordinator end-to-end time: relay, dispatch, runtime overhead" },
  ].filter((s) => s.v !== null && s.v !== undefined && s.v > 0) as { k: string; v: number; c: string; d: string }[];
  return (
    <figure>
      <div className="flex h-3 w-full overflow-hidden rounded-sm bg-paper" aria-hidden>
        {segs.map((s) => <div key={s.k} className={s.c} style={{ width: `${(s.v / total) * 100}%` }} title={`${s.k}: ${ms(s.v)}`} />)}
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {segs.map((s) => <span key={s.k} title={s.d} className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 rounded-sm ${s.c}`} />{s.k} <span className="text-ink">{ms(s.v)}</span></span>)}
        <span>of {ms(total)} coordinator end-to-end{browserMs ? `; ${ms(browserMs)} seen by this browser` : ""}</span>
      </figcaption>
    </figure>
  );
}

const SOURCE: Record<string, string> = { runtime_reported: "runtime-reported", partial_runtime_reported: "partially runtime-reported", unavailable: "unavailable" };

export function ReceiptView({ r, browser }: { r: Receipt; browser?: { e2e: number | null; ttft: number | null } }) {
  const t = r.timings_ms;
  const entries = r.credits.entries.filter((e) => e.entry_type === "job_debit" || e.entry_type === "provider_reward");
  const summary = [`Request ${r.request_id}`, `${r.model_id} on ${r.node?.display_name ?? "no worker"}`, `status ${r.status}`,
    `tokens ${r.tokens.input ?? "?"} in / ${r.tokens.output ?? "?"} out (${SOURCE[r.tokens.source ?? "unavailable"]})`,
    `end-to-end ${ms(t.coordinator_end_to_end) ?? "?"}`, `credits ${occ(r.credits.settled_micro_occ)} OCC (${r.credits.settlement_status})`].join("\n");
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-muted">Ran <span className="font-medium text-ink">{r.model_id}</span>{r.node && <> on <span className="font-medium text-ink">{r.node.display_name}</span>, owned by {r.node.owner_nickname} ({r.node.runtime})</>}</p>
          {r.route_reason && <p className="mt-0.5 text-xs text-muted">Routing: {r.route_reason}{r.retry_count ? `; retried ${r.retry_count}×` : ""}</p>}
        </div>
        <div className="flex gap-2"><CopyButton text={r.request_id} label="Copy request ID" /><CopyButton text={summary} label="Copy summary" /></div>
      </div>
      <Waterfall r={r} browserMs={browser?.e2e} />
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <Metric label="Input tokens" value={r.tokens.input} hint={`Source: ${SOURCE[r.tokens.source ?? "unavailable"]}`} />
        <Metric label="Output tokens" value={r.tokens.output} hint={`Source: ${SOURCE[r.tokens.source ?? "unavailable"]}`} />
        <Metric label="Generation speed" value={r.tokens_per_second ? `${r.tokens_per_second} tok/s` : null} hint="eval_count / eval_duration, runtime-derived" />
        <Metric label="Time to first token" value={ms(t.time_to_first_token)} hint="Coordinator: dispatch until first token arrived" />
        <Metric label="Queue wait" value={ms(t.queue_wait)} />
        <Metric label="Worker wall time" value={ms(t.worker_elapsed)} hint="Measured by the node agent; not CPU or GPU active time" />
        <Metric label="Runtime total" value={ms(t.runtime_total)} hint="Ollama total_duration" />
        <Metric label="Model load" value={ms(t.runtime_load)} hint="Ollama load_duration; large values mean a cold start" />
      </dl>
      <p className="text-xs text-muted">Token counts are {SOURCE[r.tokens.source ?? "unavailable"]}.{browser?.ttft ? ` This browser saw the first token after ${ms(browser.ttft)}.` : ""}</p>
      <div className="rounded-md bg-paper px-4 py-3">
        <h3 className="text-sm font-semibold">Credits</h3>
        {r.credits.settlement_status === "settled" ? (
          <>
            <ul className="mt-2 space-y-1 text-sm">
              {entries.map((e) => <li key={e.id} className="flex justify-between gap-4"><span>{e.entry_type === "job_debit" ? `${e.nickname} paid` : `${e.nickname} earned`}</span><span className={e.amount_micro_occ < 0 ? "text-bad" : "text-good"}>{e.amount_micro_occ > 0 ? "+" : ""}{occ(e.amount_micro_occ)} OCC</span></li>)}
            </ul>
            {r.credits.tariff && <p className="mt-2 text-xs text-muted">{r.tokens.input} × {r.credits.tariff.input_occ_per_1k}/1k + {r.tokens.output} × {r.credits.tariff.output_occ_per_1k}/1k OCC, rounded up to the micro-OCC (tariff {r.credits.tariff.version}, demo policy). A hold of {occ(r.credits.reserved_micro_occ)} OCC was placed first and released.</p>}
          </>
        ) : (
          <p className="mt-1 text-sm text-muted">{r.credits.settlement_status === "unsettled_missing_usage" ? "Not settled: the runtime did not report token usage, so no credits moved." : `No credits moved; the hold of ${occ(r.credits.reserved_micro_occ)} OCC was released.`}</p>
        )}
      </div>
      <p className="text-xs text-muted">
        {r.hosted_api_equivalent.available
          ? <>Hypothetical hosted API equivalent: ${r.hosted_api_equivalent.usd} at {r.hosted_api_equivalent.provider} {r.hosted_api_equivalent.provider_model_id} list prices (<a className="underline" href={r.hosted_api_equivalent.source_url} target="_blank" rel="noreferrer">source</a>, checked {r.hosted_api_equivalent.source_checked_at}). Different model, not a savings claim.</>
          : <>Hosted API equivalent: {r.hosted_api_equivalent.configured ? r.hosted_api_equivalent.message : "not configured (needs a sourced, dated reference price)."}</>}
      </p>
    </div>
  );
}
