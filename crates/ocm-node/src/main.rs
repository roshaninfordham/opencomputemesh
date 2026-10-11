//! OpenComputeMesh node agent: outbound-only WebSocket to the coordinator,
//! loopback-only access to the local Ollama runtime. Wire-compatible with `ocm/agent.py`.

mod ollama;
mod state;
mod telemetry;

use std::collections::HashMap;
use std::io::{BufRead, IsTerminal};
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use clap::Parser;
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::sync::{Notify, mpsc};
use tokio::time::timeout;
use tokio_tungstenite::tungstenite::Message;

use ollama::{ChatRequest, Ollama, truncate};
use state::State;
use telemetry::Host;

const PROTOCOL_VERSION: u64 = 1;
const OPEN_TIMEOUT: Duration = Duration::from_secs(15);
const PING_INTERVAL: Duration = Duration::from_secs(20);
/// No frame at all (server pings every 20s, and pongs answer ours) for this long = dead link.
const IDLE_TIMEOUT: Duration = Duration::from_secs(60);

fn say(msg: impl AsRef<str>) {
    println!("[ocm-node] {}", msg.as_ref());
}

fn fatal_close_reason(code: Option<u16>) -> Option<&'static str> {
    match code {
        Some(4003) => Some("node revoked by owner"),
        Some(4401) => Some("credential or enrollment token rejected"),
        _ => None,
    }
}

#[derive(Parser)]
#[command(name = "ocm-node", version, about = "Share local Ollama models with an OpenComputeMesh network.")]
struct Args {
    /// Coordinator origin, e.g. https://example.trycloudflare.com
    #[arg(long, env = "OCM_COORDINATOR")]
    coordinator: Option<String>,
    /// Comma-separated installed model IDs to share (otherwise asked interactively)
    #[arg(long)]
    models: Option<String>,
    /// Display name shown to the network
    #[arg(long)]
    name: Option<String>,
    /// Where the node credential is stored [default: ~/.opencomputemesh]
    #[arg(long, env = "OCM_AGENT_STATE_DIR")]
    state_dir: Option<String>,
    /// Ollama base URL; must be a loopback address
    #[arg(long, env = "OLLAMA_BASE_URL", default_value = "http://127.0.0.1:11434")]
    ollama: String,
    #[arg(long, default_value_t = 1, value_parser = clap::value_parser!(u32).range(1..=8))]
    max_jobs: u32,
    /// Heartbeat interval in seconds
    #[arg(long, env = "OCM_NODE_HEARTBEAT_INTERVAL_SECONDS", default_value_t = 20, value_parser = clap::value_parser!(u64).range(1..))]
    heartbeat: u64,
}

/// The runtime must stay on this machine (same host set as the Python agent).
fn is_loopback_url(url: &str) -> bool {
    reqwest::Url::parse(url)
        .ok()
        .filter(|u| matches!(u.scheme(), "http" | "https"))
        .and_then(|u| u.host_str().map(|h| matches!(h, "127.0.0.1" | "localhost" | "[::1]")))
        .unwrap_or(false)
}

fn ws_url(coordinator: &str) -> String {
    let base = coordinator.replacen("https://", "wss://", 1).replacen("http://", "ws://", 1);
    format!("{base}/ws/node")
}

fn expand_home(p: &str) -> Result<PathBuf> {
    let home = || std::env::home_dir().context("cannot determine home directory; pass --state-dir");
    Ok(match p {
        "~" => home()?,
        _ if p.starts_with("~/") => home()?.join(&p[2..]),
        _ => PathBuf::from(p),
    })
}

/// Outbound sender for one WebSocket session. Every message carries protocol_version and node_id.
#[derive(Clone)]
struct Out {
    tx: mpsc::UnboundedSender<Message>,
    node_id: Option<String>,
}

impl Out {
    fn send(&self, ty: &str, payload: Value) {
        let mut msg = json!({ "type": ty, "protocol_version": PROTOCOL_VERSION, "node_id": self.node_id });
        if let (Some(m), Value::Object(p)) = (msg.as_object_mut(), payload) {
            m.extend(p);
        }
        // A closed session just drops the message; the job is cancelled on reconnect.
        let _ = self.tx.send(Message::text(msg.to_string()));
    }
}

type Cancels = Arc<Mutex<HashMap<String, Arc<Notify>>>>;

enum End {
    Closed { code: Option<u16>, registered: bool },
    Unreachable(String),
}

struct Agent {
    coordinator: String,
    state_path: PathBuf,
    state: State,
    ollama: Ollama,
    models_flag: Option<String>,
    name: Option<String>,
    max_jobs: u32,
    heartbeat: Duration,
    host: Host,
    enrollment_token: Option<String>,
    cancels: Cancels,
}

impl Agent {
    fn save(&self) -> Result<()> {
        self.state.save(&self.state_path)
    }

    async fn approve_models(&mut self) -> Result<()> {
        if self.ollama.health().await.is_none() {
            bail!("Ollama is not reachable on loopback. Start it with `ollama serve` and retry.");
        }
        let mut installed: Vec<String> =
            self.ollama.list_models().await.context("cannot list Ollama models")?.into_iter().map(|m| m.model_id).collect();
        installed.sort();
        if installed.is_empty() {
            bail!("Ollama has no models installed. Pull one yourself first, e.g. `ollama pull qwen2.5:1.5b`.");
        }
        let split = |s: &str| s.split(',').map(str::trim).filter(|m| !m.is_empty()).map(String::from).collect::<Vec<_>>();
        let chosen = if let Some(flag) = &self.models_flag {
            split(flag)
        } else if !self.state.approved_models.is_empty() {
            return Ok(());
        } else if std::io::stdin().is_terminal() {
            say(format!("Installed models: {}", installed.join(", ")));
            print!("Models to share (comma-separated): ");
            std::io::Write::flush(&mut std::io::stdout())?;
            let mut line = String::new();
            std::io::stdin().lock().read_line(&mut line)?;
            split(&line)
        } else {
            bail!("Pass --models to approve which installed models this node shares.");
        };
        let missing: Vec<&str> = chosen.iter().filter(|m| !installed.contains(m)).map(String::as_str).collect();
        if chosen.is_empty() || !missing.is_empty() {
            let missing = if missing.is_empty() { "(none chosen)".to_string() } else { missing.join(", ") };
            bail!("Not installed locally: {missing}. Installed: {}", installed.join(", "));
        }
        self.state.approved_models = chosen;
        self.save()
    }

    async fn capabilities(&self) -> Value {
        let version = self.ollama.health().await;
        let mut models = Vec::new();
        if version.is_some()
            && let Ok(list) = self.ollama.list_models().await {
                models = list
                    .into_iter()
                    .filter(|m| self.state.approved_models.contains(&m.model_id))
                    .map(|m| {
                        json!({ "model_id": m.model_id, "digest": m.digest, "runtime": "ollama", "family": m.family,
                                "parameter_size": m.parameter_size, "quantization": m.quantization,
                                "size_bytes": m.size_bytes })
                    })
                    .collect();
            }
        let display_name = self.state.display_name.clone().unwrap_or_else(|| self.host.hostname.clone());
        let h = &self.host;
        json!({
            "display_name": truncate(&display_name, 48), "platform": h.platform, "architecture": h.architecture,
            "cpu_model": h.cpu_model, "logical_cpu_count": h.logical_cpu_count,
            "memory_total_bytes": h.memory_total_bytes, "memory_available_bytes": telemetry::memory_available_bytes(),
            "gpus": h.gpus, "runtime_name": "ollama", "runtime_version": version.as_deref().map(|v| truncate(v, 32)),
            "runtime_healthy": version.is_some(), "models": models, "max_concurrent_jobs": self.max_jobs,
        })
    }

    async fn run_forever(mut self) -> Result<()> {
        self.approve_models().await?;
        if let Some(n) = &self.name {
            self.state.display_name = Some(n.clone());
        }
        let mut backoff = 1.0f64;
        loop {
            match self.session().await? {
                End::Closed { code, registered } => {
                    if let Some(reason) = fatal_close_reason(code) {
                        if code == Some(4003) || self.state.credential.is_some() {
                            self.state.credential = None;
                            self.state.node_id = None;
                            self.save()?;
                        }
                        bail!("Coordinator closed the connection: {reason}.");
                    }
                    say(format!("connection closed ({}); reconnecting", code.map_or("none".into(), |c| c.to_string())));
                    if registered {
                        backoff = 1.0;
                    }
                }
                End::Unreachable(why) => say(format!("cannot reach coordinator ({why}); retrying in {backoff:.0}s")),
            }
            for n in self.cancels.lock().expect("cancels lock").values() {
                n.notify_one();
            }
            tokio::time::sleep(Duration::from_secs_f64(backoff + rand::random_range(0.0..backoff / 2.0))).await;
            backoff = (backoff * 2.0).min(60.0);
        }
    }

    fn hello(&mut self) -> Result<Value> {
        if let (Some(node_id), Some(credential)) = (&self.state.node_id, &self.state.credential) {
            return Ok(json!({ "type": "node.auth", "node_id": node_id, "credential": credential }));
        }
        let token = match self.enrollment_token.take() {
            Some(t) => t,
            None if std::io::stdin().is_terminal() => {
                rpassword::prompt_password("One-time enrollment token (from the Share my compute page): ")?
            }
            None => bail!("No saved credential: set OCM_ENROLLMENT_TOKEN or run interactively."),
        };
        Ok(json!({ "type": "node.enroll", "enrollment_token": token.trim() }))
    }

    /// One connection. `Err` is fatal for the process; `Ok(End)` means reconnect (unless a fatal close code).
    async fn session(&mut self) -> Result<End> {
        // Resolve the hello (possibly prompting) before connecting: the coordinator allows 15s for it.
        let mut hello = self.hello()?;
        let ws = match timeout(OPEN_TIMEOUT, tokio_tungstenite::connect_async(ws_url(&self.coordinator))).await {
            Ok(Ok((ws, _))) => ws,
            Ok(Err(e)) => return Ok(End::Unreachable(e.to_string())),
            Err(_) => return Ok(End::Unreachable("connect timeout".into())),
        };
        let (mut sink, mut stream) = ws.split();
        let caps = self.capabilities().await;
        hello["protocol_version"] = json!(PROTOCOL_VERSION);
        hello["capabilities"] = caps.clone();
        if let Err(e) = sink.send(Message::text(hello.to_string())).await {
            return Ok(End::Unreachable(e.to_string()));
        }

        let reg: Value = loop {
            match timeout(OPEN_TIMEOUT, stream.next()).await {
                Err(_) => return Ok(End::Unreachable("no registration reply".into())),
                Ok(None) => return Ok(End::Closed { code: None, registered: false }),
                Ok(Some(Err(e))) => return Ok(End::Unreachable(e.to_string())),
                Ok(Some(Ok(Message::Close(f)))) => {
                    return Ok(End::Closed { code: f.map(|f| u16::from(f.code)), registered: false });
                }
                Ok(Some(Ok(Message::Text(t)))) => match serde_json::from_str(&t) {
                    Ok(v) => break v,
                    Err(_) => return Ok(End::Unreachable("malformed registration reply".into())),
                },
                Ok(Some(Ok(_))) => continue,
            }
        };
        if let Some(cred) = reg.get("credential").and_then(Value::as_str) {
            let node_id = reg.get("node_id").and_then(Value::as_str).context("registration missing node_id")?;
            self.state.node_id = Some(node_id.to_owned());
            self.state.credential = Some(cred.to_owned());
            self.state.coordinator = Some(self.coordinator.clone());
            self.save()?;
            say(format!("enrolled as node {node_id}; credential saved to {} (0600)", self.state_path.display()));
        }
        let shared: Vec<&str> = caps["models"].as_array().into_iter().flatten().filter_map(|m| m["model_id"].as_str()).collect();
        say(format!(
            "online; sharing {}",
            if shared.is_empty() { "nothing (runtime unhealthy)".to_string() } else { shared.join(", ") }
        ));

        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        let writer = tokio::spawn(async move {
            while let Some(m) = rx.recv().await {
                if sink.send(m).await.is_err() {
                    break;
                }
            }
        });
        let out = Out { tx, node_id: self.state.node_id.clone() };
        let heartbeat = tokio::spawn(heartbeat_loop(out.clone(), self.ollama.clone(), self.heartbeat));

        let end = loop {
            match timeout(IDLE_TIMEOUT, stream.next()).await {
                Err(_) => break End::Unreachable("connection idle; no traffic from coordinator".into()),
                Ok(None) | Ok(Some(Err(_))) => break End::Closed { code: None, registered: true },
                Ok(Some(Ok(Message::Close(f)))) => {
                    break End::Closed { code: f.map(|f| u16::from(f.code)), registered: true };
                }
                Ok(Some(Ok(Message::Text(t)))) => {
                    if let Ok(msg) = serde_json::from_str::<Value>(&t) {
                        self.handle(msg, &out).await;
                    }
                }
                Ok(Some(Ok(_))) => {}
            }
        };
        heartbeat.abort();
        writer.abort();
        Ok(end)
    }

    async fn handle(&self, msg: Value, out: &Out) {
        match msg["type"].as_str().unwrap_or("") {
            "job.start" => {
                let job = Job {
                    out: out.clone(),
                    ollama: self.ollama.clone(),
                    approved: self.state.approved_models.clone(),
                    cancels: self.cancels.clone(),
                };
                tokio::spawn(job.run(msg));
            }
            "job.cancel" => {
                if let Some(id) = msg["job_id"].as_str()
                    && let Some(n) = self.cancels.lock().expect("cancels lock").get(id) {
                        n.notify_one();
                    }
            }
            "model.refresh" => {
                out.send("node.capabilities", json!({ "capabilities": self.capabilities().await }));
                say("model inventory refreshed");
            }
            "node.sharing" => say(if msg["enabled"].as_bool() == Some(true) {
                "sharing resumed"
            } else {
                "sharing paused by owner; no new jobs will arrive"
            }),
            "node.revoked" => say("this node was revoked by its owner"),
            _ => {}
        }
    }
}

async fn heartbeat_loop(out: Out, ollama: Ollama, every: Duration) {
    let start = tokio::time::Instant::now();
    let mut hb = tokio::time::interval_at(start + every, every);
    let mut ping = tokio::time::interval_at(start + PING_INTERVAL, PING_INTERVAL);
    loop {
        tokio::select! {
            _ = hb.tick() => out.send("node.heartbeat", json!({
                "runtime_healthy": ollama.health().await.is_some(),
                "memory_available_bytes": telemetry::memory_available_bytes(),
            })),
            _ = ping.tick() => { let _ = out.tx.send(Message::Ping(Default::default())); }
        }
    }
}

struct Job {
    out: Out,
    ollama: Ollama,
    approved: Vec<String>,
    cancels: Cancels,
}

impl Job {
    async fn run(self, msg: Value) {
        let Some(job_id) = msg["job_id"].as_str().map(str::to_owned) else { return };
        let model = msg["model"].as_str().unwrap_or_default();
        if !self.approved.iter().any(|m| m == model) {
            self.out.send("job.failed", json!({ "job_id": job_id, "code": "MODEL_NOT_SHARED", "started": false }));
            return;
        }
        let cancel = Arc::new(Notify::new());
        self.cancels.lock().expect("cancels lock").insert(job_id.clone(), cancel.clone());
        self.out.send("job.started", json!({ "job_id": job_id }));

        let opts = &msg["options"];
        let req = ChatRequest {
            model: model.to_owned(),
            messages: msg["messages"].clone(),
            max_tokens: opts["max_tokens"].as_f64().filter(|&n| n >= 1.0).map_or(512, |n| n as u64),
            temperature: opts["temperature"].as_f64(),
            top_p: opts["top_p"].as_f64(),
        };
        let deadline = msg["deadline_seconds"].as_f64().filter(|&d| d > 0.0 && d.is_finite()).unwrap_or(180.0);
        let start = Instant::now();
        let mut seq = 0u64;
        let on_token = |text: &str| {
            seq += 1;
            self.out.send("job.token", json!({ "job_id": job_id, "text": text, "seq": seq }));
        };
        let res = timeout(Duration::from_secs_f64(deadline), self.ollama.chat(req, on_token, &cancel)).await;
        let short_id = truncate(&job_id, 8);
        match res {
            Err(_) => {
                self.out.send("job.failed", json!({
                    "job_id": job_id, "code": "WORKER_TIMEOUT", "cancelled": false, "started": true,
                    "worker_elapsed_ms": start.elapsed().as_millis() as u64, "message": "worker deadline exceeded",
                }));
                say(format!("job {short_id} ended: worker deadline exceeded"));
            }
            Ok(r) if r.ok => {
                self.out.send("job.completed", json!({
                    "job_id": job_id, "final_stats": r.final_stats, "worker_elapsed_ms": r.worker_elapsed_ms,
                    "worker_ttft_ms": r.worker_ttft_ms, "tokens_sent": seq,
                }));
                say(format!("job {short_id} done"));
            }
            Ok(r) => {
                let err = r.error.unwrap_or_default();
                self.out.send("job.failed", json!({
                    "job_id": job_id, "code": if r.cancelled { "CANCELLED" } else { "RUNTIME_ERROR" },
                    "cancelled": r.cancelled, "started": true, "worker_elapsed_ms": r.worker_elapsed_ms,
                    "message": truncate(&err, 200),
                }));
                say(format!("job {short_id} ended: {}", truncate(&err, 80)));
            }
        }
        self.cancels.lock().expect("cancels lock").remove(&job_id);
    }
}

async fn build_agent(a: Args) -> Result<Agent> {
    if !is_loopback_url(&a.ollama) {
        bail!("Refusing a non-loopback Ollama URL: the runtime must stay on this machine.");
    }
    let state_dir = match &a.state_dir {
        Some(d) => expand_home(d)?,
        None => expand_home("~/.opencomputemesh")?,
    };
    let state_path = State::path(&state_dir);
    let state = State::load(&state_path)?;
    let coordinator = a
        .coordinator
        .or_else(|| state.coordinator.clone())
        .context("Pass --coordinator https://<coordinator-origin> (or set OCM_COORDINATOR).")?
        .trim_end_matches('/')
        .to_owned();
    if state.credential.is_some() && state.coordinator.as_deref().is_some_and(|c| c != coordinator) {
        say("coordinator URL changed; re-using saved node credential (tunnel URLs change on restart)");
    }
    Ok(Agent {
        coordinator,
        state_path,
        state,
        ollama: Ollama::new(&a.ollama)?,
        models_flag: a.models,
        name: a.name,
        max_jobs: a.max_jobs,
        heartbeat: Duration::from_secs(a.heartbeat),
        host: Host::detect().await,
        enrollment_token: std::env::var("OCM_ENROLLMENT_TOKEN").ok().filter(|t| !t.trim().is_empty()),
        cancels: Cancels::default(),
    })
}

#[tokio::main]
async fn main() -> ExitCode {
    let args = Args::parse();
    let result = tokio::select! {
        r = async { build_agent(args).await?.run_forever().await } => r,
        _ = tokio::signal::ctrl_c() => {
            say("stopped; this node now shows offline. Run again to resume.");
            Ok(())
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("[ocm-node] {e:#}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_only() {
        for ok in ["http://127.0.0.1:11434", "http://localhost:11434/", "http://[::1]:11434", "https://localhost"] {
            assert!(is_loopback_url(ok), "{ok}");
        }
        for bad in [
            "http://10.0.0.5:11434",
            "http://example.com",
            "http://127.0.0.1.evil.com",
            "http://localhost@evil.com",
            "file:///etc/passwd",
            "127.0.0.1:11434",
        ] {
            assert!(!is_loopback_url(bad), "{bad}");
        }
    }

    #[test]
    fn ws_url_mapping() {
        assert_eq!(ws_url("https://x.trycloudflare.com"), "wss://x.trycloudflare.com/ws/node");
        assert_eq!(ws_url("http://localhost:8031"), "ws://localhost:8031/ws/node");
    }

    #[test]
    fn fatal_codes() {
        assert!(fatal_close_reason(Some(4003)).is_some());
        assert!(fatal_close_reason(Some(4401)).is_some());
        assert!(fatal_close_reason(Some(1000)).is_none());
        assert!(fatal_close_reason(None).is_none());
    }
}
