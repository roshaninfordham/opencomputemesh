import { useCallback, useEffect, useState } from "react";
import { api, occ, type Member } from "./lib/api";
import { MeshProvider, useMesh } from "./lib/mesh";
import { ActivityPage, JoinPage, Logo, NodesPage, Overview, RunPage, SettingsPage, UsagePage, type Go } from "./pages";

const NAV = [["/", "Overview"], ["/run", "Run a model"], ["/nodes", "Nodes"], ["/usage", "My usage"], ["/activity", "Requests"], ["/settings", "Settings"]] as const;

function usePath(): [string, Go] {
  const [path, setPath] = useState(location.pathname + location.search);
  useEffect(() => { const f = () => setPath(location.pathname + location.search); addEventListener("popstate", f); return () => removeEventListener("popstate", f); }, []);
  const go: Go = (p) => { history.pushState(null, "", p); setPath(p); window.scrollTo(0, 0); };
  return [path, go];
}

export default function App() {
  const [path, go] = usePath();
  const [member, setMember] = useState<Member | null | undefined>(undefined);
  const refresh = useCallback(() => api<{ member: Member | null }>("/api/v1/session").then((r) => setMember(r.member)).catch(() => setMember(null)), []);
  useEffect(() => { refresh(); }, [refresh]);

  if (member === undefined) return <p className="p-8 text-muted">Loading…</p>;
  const joinMatch = path.match(/^\/join\/([^/?#]+)/);
  if (!member) return <JoinPage code={joinMatch ? decodeURIComponent(joinMatch[1]) : ""} onJoined={(m) => { setMember(m); go("/"); }} />;
  if (joinMatch) { history.replaceState(null, "", "/"); }
  return (
    <MeshProvider onSnapshot={refresh}>
      <Shell member={member} path={joinMatch ? "/" : path} go={go} onLeft={() => { setMember(null); go("/"); }} />
    </MeshProvider>
  );
}

function Shell({ member, path, go, onLeft }: { member: Member; path: string; go: Go; onLeft: () => void }) {
  const { connected } = useMesh();
  const route = path.split("?")[0];
  const query = new URLSearchParams(path.split("?")[1] ?? "");
  const page = route === "/run" ? <RunPage member={member} initialModel={query.get("model") ?? undefined} />
    : route === "/nodes" ? <NodesPage />
    : route === "/usage" ? <UsagePage />
    : route === "/activity" ? <ActivityPage member={member} />
    : route === "/settings" ? <SettingsPage member={member} onLeft={onLeft} />
    : <Overview member={member} go={go} />;
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[232px_1fr]">
      <aside className="border-b border-line bg-surface md:sticky md:top-0 md:h-dvh md:border-r md:border-b-0">
        <div className="flex items-center justify-between px-5 py-4 md:block"><Logo />
          <p className="text-xs text-muted md:mt-5">Signed in as <span className="font-medium text-ink">{member.nickname}</span></p>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:pb-0">
          {NAV.map(([href, label]) => {
            const active = route === href;
            return <a key={href} href={href} aria-current={active ? "page" : undefined} onClick={(e) => { e.preventDefault(); go(href); }}
              className={`whitespace-nowrap rounded-md px-3 py-2 text-sm ${active ? "bg-mesh-soft font-medium text-mesh" : "text-ink hover:bg-paper"}`}>{label}</a>;
          })}
        </nav>
        <div className="hidden px-5 py-5 text-sm md:absolute md:bottom-0 md:block">
          <p className="text-muted">Balance</p><p className="text-lg font-semibold">{occ(member.balance_micro_occ)} OCC</p>
          <p className="mt-3 flex items-center gap-1.5 text-xs text-muted"><span aria-hidden className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-good" : "bg-warn"}`} />{connected ? "Live" : "Reconnecting…"}</p>
        </div>
      </aside>
      <main className="mx-auto w-full max-w-6xl px-5 py-6 md:px-8 md:py-8">{page}</main>
    </div>
  );
}
