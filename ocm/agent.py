"""OpenComputeMesh node agent: outbound-only WebSocket to the coordinator, loopback-only runtime access."""

import argparse
import asyncio
import getpass
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import psutil
import websockets

from .protocol import PROTOCOL_VERSION
from .runtime import InferenceRequest, OllamaAdapter, RuntimeAdapter

FATAL_CLOSE_CODES = {4003: "node revoked by owner", 4401: "credential or enrollment token rejected"}


def say(msg: str) -> None:
    print(f"[ocm-agent] {msg}", flush=True)


def _run(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


def cpu_model() -> str | None:
    if sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def gpu_inventory() -> list[dict]:
    """Best-effort and labelled by source. Empty list means unknown, not 'no GPU'."""
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if out:
        gpus = []
        for line in out.splitlines():
            name, _, mem = line.partition(",")
            gpus.append({"name": name.strip()[:128], "memory_total_bytes": int(float(mem)) * 1024 * 1024 if mem.strip() else None,
                         "source": "nvidia-smi"})
        return gpus
    if sys.platform == "darwin":
        out = _run(["system_profiler", "SPDisplaysDataType", "-json"])
        try:
            return [{"name": g.get("sppci_model", "unknown")[:128], "memory_total_bytes": None,
                     "source": "system_profiler (unified memory; shares system RAM)"}
                    for g in json.loads(out or "{}").get("SPDisplaysDataType", [])]
        except ValueError:
            pass
    return []


class Agent:
    def __init__(self, coordinator: str, state_dir: Path, runtime: RuntimeAdapter, models_flag: str | None,
                 name: str | None, max_jobs: int, heartbeat: int):
        self.coordinator = coordinator.rstrip("/")
        self.state_path = state_dir / "node.json"
        self.runtime = runtime
        self.models_flag = models_flag
        self.name = name
        self.max_jobs = max_jobs
        self.heartbeat = heartbeat
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.cancels: dict[str, asyncio.Event] = {}
        self.send_lock = asyncio.Lock()
        self.ws = None
        self.gpus = gpu_inventory()

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.state_path)

    @property
    def ws_url(self) -> str:
        base = self.coordinator.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/ws/node"

    async def approve_models(self) -> None:
        if not await self.runtime.health():
            raise SystemExit("Ollama is not reachable on loopback. Start it with `ollama serve` and retry.")
        installed = {m.model_id for m in await self.runtime.list_models()}
        if not installed:
            raise SystemExit("Ollama has no models installed. Pull one yourself first, e.g. `ollama pull qwen2.5:1.5b`.")
        if self.models_flag:
            chosen = [m.strip() for m in self.models_flag.split(",") if m.strip()]
        elif self.state.get("approved_models"):
            return
        elif sys.stdin.isatty():
            say("Installed models: " + ", ".join(sorted(installed)))
            chosen = [m.strip() for m in input("Models to share (comma-separated): ").split(",") if m.strip()]
        else:
            raise SystemExit("Pass --models to approve which installed models this node shares.")
        missing = [m for m in chosen if m not in installed]
        if missing or not chosen:
            raise SystemExit(f"Not installed locally: {', '.join(missing) or '(none chosen)'}. Installed: {', '.join(sorted(installed))}")
        self.state["approved_models"] = chosen
        self.save()

    async def capabilities(self) -> dict:
        version = await self.runtime.health()
        models = []
        if version:
            approved = set(self.state.get("approved_models", []))
            models = [{"model_id": m.model_id, "digest": m.digest, "runtime": self.runtime.name, "family": m.family,
                       "parameter_size": m.parameter_size, "quantization": m.quantization, "size_bytes": m.size_bytes}
                      for m in await self.runtime.list_models() if m.model_id in approved]
        vm = psutil.virtual_memory()
        return {"display_name": (self.name or self.state.get("display_name") or socket.gethostname().split(".")[0])[:48],
                "platform": f"{platform.system()} {platform.release()}", "architecture": platform.machine(),
                "cpu_model": cpu_model(), "logical_cpu_count": psutil.cpu_count(), "memory_total_bytes": vm.total,
                "memory_available_bytes": vm.available, "gpus": self.gpus, "runtime_name": self.runtime.name,
                "runtime_version": version, "runtime_healthy": bool(version), "models": models,
                "max_concurrent_jobs": self.max_jobs}

    async def send(self, type_: str, **payload) -> None:
        msg = {"type": type_, "protocol_version": PROTOCOL_VERSION, "node_id": self.state.get("node_id"), **payload}
        async with self.send_lock:
            await self.ws.send(json.dumps(msg))

    async def run_forever(self) -> None:
        await self.approve_models()
        if self.name:
            self.state["display_name"] = self.name
        backoff = 1.0
        while True:
            try:
                await self.session()
                backoff = 1.0
            except websockets.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                if code in FATAL_CLOSE_CODES:
                    if code == 4003 or self.state.get("credential"):
                        self.state.pop("credential", None)
                        self.state.pop("node_id", None)
                        self.save()
                    raise SystemExit(f"Coordinator closed the connection: {FATAL_CLOSE_CODES[code]}.")
                say(f"connection closed ({code}); reconnecting")
            except (OSError, websockets.InvalidHandshake, asyncio.TimeoutError) as e:
                say(f"cannot reach coordinator ({type(e).__name__}); retrying in {backoff:.0f}s")
            for ev in self.cancels.values():
                ev.set()
            await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
            backoff = min(backoff * 2, 60)

    async def session(self) -> None:
        async with websockets.connect(self.ws_url, max_size=2**20, open_timeout=15, ping_interval=20) as ws:
            self.ws = ws
            caps = await self.capabilities()
            if self.state.get("credential"):
                hello = {"type": "node.auth", "node_id": self.state["node_id"], "credential": self.state["credential"]}
            else:
                token = os.environ.pop("OCM_ENROLLMENT_TOKEN", None)
                if not token:
                    if not sys.stdin.isatty():
                        raise SystemExit("No saved credential: set OCM_ENROLLMENT_TOKEN or run interactively.")
                    token = getpass.getpass("One-time enrollment token (from the Share my compute page): ")
                hello = {"type": "node.enroll", "enrollment_token": token.strip()}
            await ws.send(json.dumps({**hello, "protocol_version": PROTOCOL_VERSION, "capabilities": caps}))
            reg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if reg.get("credential"):
                self.state.update(node_id=reg["node_id"], credential=reg["credential"], coordinator=self.coordinator)
                self.save()
                say(f"enrolled as node {reg['node_id']}; credential saved to {self.state_path} (0600)")
            say(f"online; sharing {', '.join(m['model_id'] for m in caps['models']) or 'nothing (runtime unhealthy)'}")
            hb = asyncio.create_task(self.heartbeat_loop())
            try:
                async for raw in ws:
                    await self.handle(json.loads(raw))
            finally:
                hb.cancel()

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat)
            await self.send("node.heartbeat", runtime_healthy=bool(await self.runtime.health()),
                            memory_available_bytes=psutil.virtual_memory().available)

    async def handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "job.start":
            asyncio.create_task(self.run_job(msg))
        elif t == "job.cancel" and msg.get("job_id") in self.cancels:
            self.cancels[msg["job_id"]].set()
        elif t == "model.refresh":
            await self.send("node.capabilities", capabilities=await self.capabilities())
            say("model inventory refreshed")
        elif t == "node.sharing":
            say("sharing resumed" if msg.get("enabled") else "sharing paused by owner; no new jobs will arrive")
        elif t == "node.revoked":
            say("this node was revoked by its owner")

    async def run_job(self, msg: dict) -> None:
        job_id = msg["job_id"]
        if msg.get("model") not in self.state.get("approved_models", []):
            await self.send("job.failed", job_id=job_id, code="MODEL_NOT_SHARED", started=False)
            return
        cancel = self.cancels[job_id] = asyncio.Event()
        seq = 0
        opts = msg.get("options") or {}
        try:
            await self.send("job.started", job_id=job_id)

            async def on_token(text: str):
                nonlocal seq
                seq += 1
                await self.send("job.token", job_id=job_id, text=text, seq=seq)

            req = InferenceRequest(model_id=msg["model"], messages=msg["messages"], max_tokens=int(opts.get("max_tokens") or 512),
                                   temperature=opts.get("temperature"), top_p=opts.get("top_p"))
            try:
                res = await asyncio.wait_for(self.runtime.generate_stream(req, on_token, cancel),
                                             timeout=msg.get("deadline_seconds") or 180)
            except TimeoutError:
                await self.send("job.failed", job_id=job_id, code="WORKER_TIMEOUT", started=True)
                return
            if res.ok:
                await self.send("job.completed", job_id=job_id, final_stats=res.final_stats,
                                worker_elapsed_ms=res.worker_elapsed_ms, worker_ttft_ms=res.worker_ttft_ms, tokens_sent=seq)
            else:
                await self.send("job.failed", job_id=job_id, code="CANCELLED" if res.cancelled else "RUNTIME_ERROR",
                                cancelled=res.cancelled, started=True, worker_elapsed_ms=res.worker_elapsed_ms,
                                message=(res.error or "")[:200])
            say(f"job {job_id[:8]} {'done' if res.ok else 'ended: ' + (res.error or '')[:80]}")
        except websockets.ConnectionClosed:
            cancel.set()
        finally:
            self.cancels.pop(job_id, None)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="ocm-agent", description="Share local Ollama models with an OpenComputeMesh network.")
    p.add_argument("--coordinator", default=os.environ.get("OCM_COORDINATOR"),
                   help="Coordinator origin, e.g. https://example.trycloudflare.com (env OCM_COORDINATOR)")
    p.add_argument("--models", help="Comma-separated installed model IDs to share (otherwise asked interactively)")
    p.add_argument("--name", help="Display name shown to the network")
    p.add_argument("--state-dir", default=os.environ.get("OCM_AGENT_STATE_DIR", str(Path.home() / ".opencomputemesh")),
                   help="Where the node credential is stored (use a separate dir per simulated node)")
    p.add_argument("--ollama", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    p.add_argument("--max-jobs", type=int, default=1)
    p.add_argument("--heartbeat", type=int, default=int(os.environ.get("OCM_NODE_HEARTBEAT_INTERVAL_SECONDS", 20)))
    a = p.parse_args(argv)
    host = a.ollama.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
    if host not in ("127.0.0.1", "localhost", "[::1]"):
        raise SystemExit("Refusing a non-loopback Ollama URL: the runtime must stay on this machine.")
    state_dir = Path(a.state_dir).expanduser()
    saved = json.loads((state_dir / "node.json").read_text()) if (state_dir / "node.json").exists() else {}
    coordinator = a.coordinator or saved.get("coordinator")
    if not coordinator:
        raise SystemExit("Pass --coordinator https://<coordinator-origin> (or set OCM_COORDINATOR).")
    if saved.get("coordinator") and saved["coordinator"] != coordinator.rstrip("/") and saved.get("credential"):
        say("coordinator URL changed; re-using saved node credential (tunnel URLs change on restart)")
    agent = Agent(coordinator, state_dir, OllamaAdapter(a.ollama), a.models, a.name, a.max_jobs, a.heartbeat)
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        say("stopped; this node now shows offline. Run again to resume.")


if __name__ == "__main__":
    main()
