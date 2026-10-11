import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Network } from "./api";

type Msg = { type: string; [k: string]: any };
type Listener = (m: Msg) => void;
type Mesh = { network: Network | null; connected: boolean; send: (m: Msg) => boolean; subscribe: (l: Listener) => () => void };

const Ctx = createContext<Mesh>({ network: null, connected: false, send: () => false, subscribe: () => () => {} });
export const useMesh = () => useContext(Ctx);

/** One authenticated browser WebSocket per tab, re-established with backoff. */
export function MeshProvider({ children, onSnapshot }: { children: ReactNode; onSnapshot?: () => void }) {
  const [network, setNetwork] = useState<Network | null>(null);
  const [connected, setConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const listeners = useRef(new Set<Listener>());
  const snap = useRef(onSnapshot);
  snap.current = onSnapshot;

  useEffect(() => {
    let stopped = false, backoff = 500, timer: number | undefined;
    const connect = async () => {
      try {
        const { ticket } = await api<{ ticket: string }>("/api/v1/ws-ticket", { method: "POST" });
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const sock = new WebSocket(`${proto}://${location.host}/ws/client?ticket=${encodeURIComponent(ticket)}`);
        ws.current = sock;
        sock.onopen = () => { setConnected(true); backoff = 500; };
        sock.onmessage = (ev) => {
          const m = JSON.parse(ev.data) as Msg;
          if (m.type === "network.snapshot") { setNetwork(m.network); snap.current?.(); }
          listeners.current.forEach((l) => l(m));
        };
        sock.onclose = () => { setConnected(false); if (!stopped) retry(); };
      } catch { retry(); }
    };
    const retry = () => { timer = window.setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15_000); };
    connect();
    return () => { stopped = true; window.clearTimeout(timer); ws.current?.close(); };
  }, []);

  const send = (m: Msg) => {
    if (ws.current?.readyState !== WebSocket.OPEN) return false;
    ws.current.send(JSON.stringify(m));
    return true;
  };
  const subscribe = (l: Listener) => { listeners.current.add(l); return () => { listeners.current.delete(l); }; };
  return <Ctx.Provider value={{ network, connected, send, subscribe }}>{children}</Ctx.Provider>;
}
