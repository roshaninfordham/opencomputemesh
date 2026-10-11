//! Host facts for capabilities. GPU detection is best-effort and labelled by source;
//! an empty list means unknown, not "no GPU".

use std::time::Duration;

use serde_json::{Value, json};
use sysinfo::System;

use crate::ollama::truncate;

pub struct Host {
    pub hostname: String,
    pub platform: Option<String>,
    pub architecture: String,
    pub cpu_model: Option<String>,
    pub logical_cpu_count: usize,
    pub memory_total_bytes: u64,
    pub gpus: Vec<Value>,
}

impl Host {
    pub async fn detect() -> Host {
        let mut sys = System::new();
        sys.refresh_cpu_all();
        sys.refresh_memory();
        let cpu_model = sys.cpus().first().map(|c| c.brand().trim().to_owned()).filter(|b| !b.is_empty());
        let platform = match (System::name(), System::kernel_version()) {
            (Some(n), Some(k)) => Some(format!("{n} {k}")),
            (n, k) => n.or(k),
        };
        Host {
            hostname: System::host_name().unwrap_or_else(|| "node".into()).split('.').next().unwrap_or("node").to_owned(),
            platform: platform.map(|p| truncate(&p, 64)),
            architecture: truncate(&System::cpu_arch(), 32),
            cpu_model: cpu_model.map(|c| truncate(&c, 128)),
            logical_cpu_count: sys.cpus().len(),
            memory_total_bytes: sys.total_memory(),
            gpus: gpu_inventory().await,
        }
    }
}

pub fn memory_available_bytes() -> u64 {
    let mut sys = System::new();
    sys.refresh_memory();
    sys.available_memory()
}

async fn run(cmd: &str, args: &[&str]) -> Option<String> {
    let child = tokio::process::Command::new(cmd).args(args).kill_on_drop(true).output();
    let out = tokio::time::timeout(Duration::from_secs(5), child).await.ok()?.ok()?;
    let s = String::from_utf8_lossy(&out.stdout).trim().to_owned();
    (out.status.success() && !s.is_empty()).then_some(s)
}

async fn gpu_inventory() -> Vec<Value> {
    if let Some(out) = run("nvidia-smi", &["--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).await {
        return parse_nvidia_smi(&out);
    }
    if cfg!(target_os = "macos")
        && let Some(out) = run("system_profiler", &["SPDisplaysDataType", "-json"]).await {
            return parse_system_profiler(&out);
        }
    Vec::new()
}

fn parse_nvidia_smi(out: &str) -> Vec<Value> {
    out.lines()
        .take(16)
        .map(|line| {
            let (name, mem) = line.split_once(',').unwrap_or((line, ""));
            let mem = mem.trim().parse::<f64>().ok().map(|mib| (mib as u64) * 1024 * 1024);
            json!({ "name": truncate(name.trim(), 128), "memory_total_bytes": mem, "source": "nvidia-smi" })
        })
        .collect()
}

fn parse_system_profiler(out: &str) -> Vec<Value> {
    let Ok(v) = serde_json::from_str::<Value>(out) else { return Vec::new() };
    v.get("SPDisplaysDataType")
        .and_then(Value::as_array)
        .map(|gpus| {
            gpus.iter()
                .take(16)
                .map(|g| {
                    let name = g.get("sppci_model").and_then(Value::as_str).unwrap_or("unknown");
                    json!({ "name": truncate(name, 128), "memory_total_bytes": null,
                            "source": "system_profiler (unified memory; shares system RAM)" })
                })
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gpu_parsers() {
        let g = parse_nvidia_smi("NVIDIA GeForce RTX 4090, 24564\nTesla T4, ");
        assert_eq!(g[0]["memory_total_bytes"], 24564u64 * 1024 * 1024);
        assert!(g[1]["memory_total_bytes"].is_null());
        let m = parse_system_profiler(r#"{"SPDisplaysDataType":[{"sppci_model":"Apple M2"}]}"#);
        assert_eq!(m[0]["name"], "Apple M2");
        assert!(parse_system_profiler("garbage").is_empty());
    }
}
