/** Browser-only conversation state. The coordinator never stores prompts, so threads live here. */
import type { Receipt } from "./api";

export type TurnPhase = "sending" | "queued" | "routed" | "running" | "done" | "failed" | "cancelled";
export type Turn = {
  id: string; role: "user" | "assistant"; text: string; at: number;
  phase?: TurnPhase; requestId?: string; receipt?: Receipt; error?: string;
};
export type Thread = { id: string; model: string; title: string; createdAt: number; turns: Turn[] };
export type ChatMessage = { role: "user" | "assistant"; content: string };

const KEY = "ocm-threads-v1";
const MAX_THREADS = 20;
const MAX_TURNS = 40;
const PENDING: TurnPhase[] = ["sending", "queued", "routed", "running"];

export function newThread(model: string): Thread {
  return { id: crypto.randomUUID(), model, title: "New chat", createdAt: Date.now(), turns: [] };
}

export function titleFor(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > 48 ? `${t.slice(0, 47)}…` : t || "New chat";
}

export function loadThreads(): Thread[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isThread).map((t) => ({
      ...t,
      // A run cannot survive a reload. Say so rather than spinning for ever.
      turns: t.turns.map((turn) => (turn.phase && PENDING.includes(turn.phase)
        ? { ...turn, phase: "failed" as TurnPhase, error: "Interrupted by a page reload; the run was not resumed." }
        : turn)),
    }));
  } catch { return []; }
}

function isThread(t: unknown): t is Thread {
  const v = t as Thread;
  return !!v && typeof v.id === "string" && typeof v.model === "string" && Array.isArray(v.turns);
}

export function saveThreads(threads: Thread[]): void {
  const capped = threads.slice(0, MAX_THREADS).map((t) => ({ ...t, turns: t.turns.slice(-MAX_TURNS) }));
  try {
    localStorage.setItem(KEY, JSON.stringify(capped));
  } catch {
    // Quota hit: keep the newest few rather than losing the conversation in view.
    try { localStorage.setItem(KEY, JSON.stringify(capped.slice(0, 3))); } catch { /* keep in memory */ }
  }
}

/**
 * The newest whole turns that fit `limit` characters, plus the draft.
 * The coordinator caps the sum of every message's content, so this mirrors it.
 */
export function buildMessages(turns: Turn[], draft: string, limit: number): { messages: ChatMessage[]; omitted: number } {
  const budget = Math.max(0, limit - draft.length - 64);
  const kept: ChatMessage[] = [];
  let used = 0;
  let omitted = 0;
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const turn = turns[i];
    if (!turn.text.trim()) continue;
    if (kept.length >= 48) { omitted = i + 1; break; }
    if (used + turn.text.length > budget) { omitted = i + 1; break; }
    kept.unshift({ role: turn.role, content: turn.text });
    used += turn.text.length;
  }
  return { messages: [...kept, { role: "user", content: draft }], omitted };
}
