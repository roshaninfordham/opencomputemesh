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

  const joinMatch = path.match(/^\/join\/([^/?#]+)/);
  useEffect(() => { if (member === null) document.title = "Join - OpenComputeMesh"; }, [member]);
  if (member === undefined) return <p className="p-8 text-muted">Loading…</p>;
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
  const page = route === "/run" ? <RunPage member={member} initialModel={query.get("model") ?? undefined} go={go} />
    : route === "/nodes" ? <NodesPage share={query.get("share") === "1"} />
    : route === "/usage" ? <UsagePage />
    : route === "/activity" ? <ActivityPage member={member} />
    : route === "/settings" ? <SettingsPage member={member} onLeft={onLeft} />
    : <Overview member={member} go={go} />;
  const label = NAV.find(([href]) => href === route)?.[1] ?? "Overview";
  useEffect(() => { document.title = `${label} - OpenComputeMesh`; }, [label]);
  useEffect(() => { document.querySelector('nav[aria-label="Main"] [aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" }); }, [route]);
  const status = <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${connected ? "bg-good" : "bg-warn"}`} />;
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[232px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:shadow">Skip to content</a>
      <aside className="border-b border-line bg-surface md:sticky md:top-0 md:flex md:h-dvh md:flex-col md:overflow-y-auto md:border-r md:border-b-0">
        <div className="flex items-center justify-between gap-3 px-5 py-3 md:block md:py-4"><Logo />
          <div className="min-w-0 text-right text-xs leading-tight md:hidden">
            <p className="flex items-center justify-end gap-1.5 truncate font-medium">{status}<span className="sr-only">{connected ? "Live," : "Reconnecting,"}</span>{member.nickname}</p>
            <p className="mt-0.5 text-muted">{occ(member.balance_micro_occ)} OCC</p>
          </div>
          <p className="hidden text-xs text-muted md:mt-5 md:block">Signed in as <span className="font-medium text-ink">{member.nickname}</span></p>
        </div>
        <nav aria-label="Main" className="flex scroll-px-10 gap-1 overflow-x-auto px-3 pr-10 pb-2 [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_calc(100%-2.5rem),transparent)] md:flex-col md:pr-3 md:pb-0 md:[mask-image:none]">
          {NAV.map(([href, l]) => {
            const active = route === href;
            return <a key={href} href={href} aria-current={active ? "page" : undefined} onClick={(e) => { e.preventDefault(); go(href); }}
              className={`shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-sm ${active ? "bg-mesh-soft font-medium text-mesh shadow-[inset_0_-2px_0_var(--color-mesh)] md:shadow-none" : "text-ink hover:bg-paper"}`}>{l}</a>;
          })}
        </nav>
        <div className="hidden px-5 py-5 text-sm md:mt-auto md:block">
          <p className="text-muted">Balance</p><p className="text-lg font-semibold">{occ(member.balance_micro_occ)} OCC</p>
          <p className="mt-3 flex items-center gap-1.5 text-xs text-muted">{status}{connected ? "Live" : "Reconnecting…"}</p>
        </div>
      </aside>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl px-5 py-6 focus:outline-none md:px-8 md:py-8">{page}</main>
    </div>
  );
}
