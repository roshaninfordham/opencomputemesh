//! Loopback Ollama adapter: /api/version, /api/tags, streaming /api/chat.

use std::time::{Duration, Instant};

use anyhow::Result;
use futures_util::StreamExt;
use serde_json::{Map, Value, json};
use tokio::sync::Notify;

const FINAL_STAT_KEYS: [&str; 7] = [
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
    "done_reason",
];

/// Incremental newline-delimited JSON over raw bytes (UTF-8 may split across chunks).
/// Malformed lines are counted and skipped, not fatal.
#[derive(Default)]
pub struct NdjsonDecoder {
    buf: Vec<u8>,
    pub malformed: usize,
}

impl NdjsonDecoder {
    pub fn feed(&mut self, chunk: &[u8]) -> Vec<Value> {
        self.buf.extend_from_slice(chunk);
        let Some(last_nl) = self.buf.iter().rposition(|&b| b == b'\n') else {
            return Vec::new();
        };
        let rest = self.buf.split_off(last_nl + 1);
        let complete = std::mem::replace(&mut self.buf, rest);
        let mut out = Vec::new();
        for line in complete.split(|&b| b == b'\n') {
            if line.iter().all(u8::is_ascii_whitespace) {
                continue;
            }
            match serde_json::from_slice(line) {
                Ok(v) => out.push(v),
                Err(_) => self.malformed += 1,
            }
        }
        out
    }

    pub fn flush(&mut self) -> Vec<Value> {
        self.feed(b"\n")
    }
}

pub struct Model {
    pub model_id: String,
    pub digest: Option<String>,
    pub family: Option<String>,
    pub parameter_size: Option<String>,
    pub quantization: Option<String>,
    pub size_bytes: Option<u64>,
}

pub struct ChatRequest {
    pub model: String,
    pub messages: Value,
    pub max_tokens: u64,
    pub temperature: Option<f64>,
    pub top_p: Option<f64>,
}

#[derive(Debug, Default)]
pub struct ChatResult {
    pub ok: bool,
    pub final_stats: Map<String, Value>,
    pub worker_elapsed_ms: Option<u64>,
    pub worker_ttft_ms: Option<u64>,
    pub error: Option<String>,
    pub cancelled: bool,
}

impl ChatResult {
    fn err(msg: impl Into<String>) -> Self {
        ChatResult { error: Some(msg.into()), ..Default::default() }
    }
}

#[derive(Clone)]
pub struct Ollama {
    base: String,
    client: reqwest::Client,
}

fn ms_since(start: Instant) -> u64 {
    start.elapsed().as_millis() as u64
}

fn str_field(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(str::to_owned)
}

impl Ollama {
    pub fn new(base: &str) -> Result<Self> {
        let client = reqwest::Client::builder()
            .no_proxy() // loopback only; never route through a proxy
            .connect_timeout(Duration::from_secs(5))
            .read_timeout(Duration::from_secs(180))
            .build()?;
        Ok(Ollama { base: base.trim_end_matches('/').to_owned(), client })
    }

    /// Runtime version, or None if unreachable.
    pub async fn health(&self) -> Option<String> {
        let r = self.client.get(format!("{}/api/version", self.base)).timeout(Duration::from_secs(10)).send().await.ok()?;
        let v: Value = r.error_for_status().ok()?.json().await.ok()?;
        str_field(&v, "version")
    }

    pub async fn list_models(&self) -> Result<Vec<Model>> {
        let r = self.client.get(format!("{}/api/tags", self.base)).timeout(Duration::from_secs(30)).send().await?;
        let v: Value = r.error_for_status()?.json().await?;
        let models = v.get("models").and_then(Value::as_array).cloned().unwrap_or_default();
        Ok(models
            .iter()
            .filter_map(|m| {
                let d = m.get("details").cloned().unwrap_or(Value::Null);
                Some(Model {
                    model_id: str_field(m, "name")?,
                    digest: str_field(m, "digest"),
                    family: str_field(&d, "family"),
                    parameter_size: str_field(&d, "parameter_size"),
                    quantization: str_field(&d, "quantization_level"),
                    size_bytes: m.get("size").and_then(Value::as_u64),
                })
            })
            .collect())
    }

    /// Streams a chat completion. Returning (or dropping this future) drops the HTTP
    /// connection, which makes Ollama abort generation.
    pub async fn chat(&self, req: ChatRequest, mut on_token: impl FnMut(&str), cancel: &Notify) -> ChatResult {
        let mut options = json!({ "num_predict": req.max_tokens });
        if let Some(t) = req.temperature {
            options["temperature"] = json!(t);
        }
        if let Some(p) = req.top_p {
            options["top_p"] = json!(p);
        }
        // think=false keeps reasoning models from spending the budget on a hidden phase.
        let body = json!({ "model": req.model, "messages": req.messages, "stream": true, "think": false, "options": options });
        let start = Instant::now();
        let mut ttft = None;
        let resp = match self.client.post(format!("{}/api/chat", self.base)).json(&body).send().await {
            Ok(r) => r,
            Err(e) => return ChatResult::err(format!("runtime connection error: {}", reqwest_kind(&e))),
        };
        let status = resp.status();
        if status != reqwest::StatusCode::OK {
            let detail = resp.text().await.unwrap_or_default();
            return ChatResult::err(format!("runtime HTTP {}: {}", status.as_u16(), truncate(&detail, 200)));
        }
        let mut decoder = NdjsonDecoder::default();
        let mut stream = resp.bytes_stream();
        loop {
            let mut eof = false;
            let chunks = tokio::select! {
                _ = cancel.notified() => {
                    return ChatResult { cancelled: true, error: Some("cancelled".into()),
                                        worker_elapsed_ms: Some(ms_since(start)), ..Default::default() };
                }
                next = stream.next() => match next {
                    Some(Ok(bytes)) => decoder.feed(&bytes),
                    Some(Err(e)) => return ChatResult::err(format!("runtime connection error: {}", reqwest_kind(&e))),
                    None => { eof = true; decoder.flush() }
                },
            };
            for chunk in chunks {
                if let Some(err) = chunk.get("error") {
                    let msg = err.as_str().map(str::to_owned).unwrap_or_else(|| err.to_string());
                    return ChatResult::err(format!("runtime error: {}", truncate(&msg, 200)));
                }
                let content = chunk.pointer("/message/content").and_then(Value::as_str).unwrap_or("");
                if !content.is_empty() {
                    ttft.get_or_insert_with(|| ms_since(start));
                    on_token(content);
                }
                if chunk.get("done").and_then(Value::as_bool) == Some(true) {
                    let final_stats =
                        FINAL_STAT_KEYS.iter().filter_map(|&k| chunk.get(k).map(|v| (k.to_owned(), v.clone()))).collect();
                    return ChatResult {
                        ok: true,
                        final_stats,
                        worker_elapsed_ms: Some(ms_since(start)),
                        worker_ttft_ms: ttft,
                        ..Default::default()
                    };
                }
            }
            if eof {
                return ChatResult::err("runtime stream ended without final statistics");
            }
        }
    }
}

fn reqwest_kind(e: &reqwest::Error) -> &'static str {
    if e.is_timeout() {
        "timeout"
    } else if e.is_connect() {
        "connect"
    } else if e.is_body() || e.is_decode() {
        "body"
    } else {
        "request"
    }
}

/// Char-boundary-safe truncation.
pub fn truncate(s: &str, max_chars: usize) -> String {
    s.chars().take(max_chars).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &[u8] = include_bytes!("../../../tests/fixtures/ollama_chat_stream.ndjson");

    #[test]
    fn fixture_in_small_splits() {
        for split in [1usize, 3, 7, 64] {
            let mut d = NdjsonDecoder::default();
            let mut out = Vec::new();
            for piece in FIXTURE.chunks(split) {
                out.extend(d.feed(piece));
            }
            out.extend(d.flush());
            assert_eq!(d.malformed, 0);
            let last = out.last().unwrap();
            assert_eq!(last["done"], true);
            assert_eq!(last["eval_count"], 10);
            let text: String = out.iter().filter_map(|c| c.pointer("/message/content")?.as_str()).collect();
            assert!(text.starts_with("Hello!"));
        }
    }

    #[test]
    fn malformed_line_skipped() {
        let mut d = NdjsonDecoder::default();
        let mut out = d.feed(b"{\"a\":1}\nnot json\n\n{\"b\":");
        out.extend(d.feed("2, \"s\":\"\u{e9}\"}".as_bytes()));
        out.extend(d.flush());
        assert_eq!(out.len(), 2);
        assert_eq!(out[1]["b"], 2);
        assert_eq!(d.malformed, 1);
    }
}
