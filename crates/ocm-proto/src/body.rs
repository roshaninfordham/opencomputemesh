//! Message bodies exchanged between nodes and a coordinator.

use serde::{Deserialize, Serialize};

/// A frame payload, tagged by `type` with its fields under `data`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    tag = "type",
    content = "data",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Body {
    /// Node to coordinator, first frame: enroll with a token or authenticate with a node id.
    Hello(Hello),
    /// Coordinator to node: accepted; carries the assigned node id.
    Welcome(Welcome),
    /// Node to coordinator: periodic liveness and resource snapshot.
    Heartbeat(Heartbeat),
    /// Node to coordinator: refreshed capabilities after a model change.
    Capabilities(Capabilities),
    /// Coordinator to node: run a job.
    JobStart(JobStart),
    /// Coordinator to node: stop a job.
    JobCancel(JobRef),
    /// Node to coordinator: the runtime accepted the job.
    JobStarted(JobRef),
    /// Node to coordinator: one streamed chunk of output.
    JobToken(JobToken),
    /// Node to coordinator: final statistics. Must be signed.
    JobCompleted(JobCompleted),
    /// Node to coordinator: the job ended without a result. Must be signed.
    JobFailed(JobFailed),
    /// Either direction: a refusal with a stable code and a content-free message.
    Error(ErrorMsg),
    /// Either direction: application-level liveness probe.
    Ping,
    /// Reply to [`Body::Ping`].
    Pong,
}

impl Body {
    /// Whether frames carrying this body must be signed by the node key.
    pub fn requires_signature(&self) -> bool {
        matches!(self, Self::JobCompleted(_) | Self::JobFailed(_))
    }
}

/// First node frame.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hello {
    /// Single-use enrollment token, for a first connection.
    pub enrollment_token: Option<String>,
    /// Previously assigned node id, for reconnection.
    pub node_id: Option<String>,
    /// Lowercase hex Ed25519 public key (32 bytes).
    pub public_key: String,
    /// What the node offers.
    pub caps: Capabilities,
}

/// Enrollment or authentication accepted.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Welcome {
    /// The node's id on this coordinator.
    pub node_id: String,
}

/// Liveness and resource snapshot.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Heartbeat {
    /// Whether the node's runtimes answered their health check.
    pub healthy: bool,
    /// Free memory in bytes, when known.
    pub mem_free: Option<u64>,
    /// Digests of models currently loaded in memory.
    pub loaded: Vec<String>,
}

/// Hardware and models a node offers.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Capabilities {
    /// Display name chosen by the owner.
    pub name: String,
    /// Operating system and version.
    pub platform: String,
    /// CPU architecture.
    pub arch: String,
    /// CPU model, when known.
    pub cpu: Option<String>,
    /// Logical CPU count, when known.
    pub cores: Option<u16>,
    /// Total memory in bytes, when known.
    pub mem_total: Option<u64>,
    /// Detected accelerators, each labelled with its detection source.
    pub accelerators: Vec<Accelerator>,
    /// Owner-approved models.
    pub models: Vec<ModelInfo>,
    /// Concurrent job slots the owner allows.
    pub slots: u16,
}

/// A detected GPU or neural accelerator.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Accelerator {
    /// Device name.
    pub name: String,
    /// Dedicated memory in bytes, when known.
    pub mem: Option<u64>,
    /// How it was detected.
    pub source: String,
}

/// One model a node serves.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelInfo {
    /// Model id as the runtime names it.
    pub id: String,
    /// Content digest identifying the exact weights.
    pub digest: String,
    /// Runtime adapter serving it.
    pub runtime: String,
    /// Whether it is loaded in memory now.
    pub loaded: bool,
    /// Parameter count label, when known.
    pub params: Option<String>,
    /// Quantization label, when known.
    pub quant: Option<String>,
}

/// A job dispatched to a node.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobStart {
    /// Job id.
    pub job: String,
    /// Model id.
    pub model: String,
    /// Required digest; the node refuses a mismatch.
    pub digest: String,
    /// Chat messages; the last is from the user.
    pub messages: Vec<Message>,
    /// Output token limit.
    pub max_tokens: u32,
    /// Sampling temperature.
    pub temperature: Option<f32>,
    /// Nucleus sampling.
    pub top_p: Option<f32>,
    /// Sampling seed, used by spot checks.
    pub seed: Option<u64>,
    /// Hard deadline in milliseconds.
    pub deadline_ms: u32,
}

/// One chat message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Message {
    /// Author role.
    pub role: Role,
    /// Text content.
    pub content: String,
}

/// Chat message author.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    /// System instructions.
    System,
    /// The requester.
    User,
    /// The model.
    Assistant,
}

/// Refers to a job.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobRef {
    /// Job id.
    pub job: String,
}

/// A streamed output chunk.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobToken {
    /// Job id.
    pub job: String,
    /// Chunk sequence number starting at 1.
    pub seq: u32,
    /// Chunk text.
    pub text: String,
}

/// Final statistics for a finished job.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobCompleted {
    /// Job id.
    pub job: String,
    /// Token usage with provenance.
    pub usage: Usage,
    /// Wall time on the node in milliseconds.
    pub worker_ms: u32,
    /// Time to first token on the node in milliseconds.
    pub ttft_ms: Option<u32>,
    /// Why generation stopped.
    pub done_reason: Option<DoneReason>,
}

/// Token usage and runtime timings. Unknown values stay `None`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Usage {
    /// Prompt tokens.
    pub input_tokens: Option<u32>,
    /// Generated tokens.
    pub output_tokens: Option<u32>,
    /// Where the counts came from.
    pub source: TokenSource,
    /// Model load time in milliseconds.
    pub load_ms: Option<u32>,
    /// Prompt evaluation time in milliseconds.
    pub prompt_ms: Option<u32>,
    /// Generation time in milliseconds.
    pub generation_ms: Option<u32>,
}

/// Provenance of token counts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TokenSource {
    /// Reported by the runtime.
    Runtime,
    /// Estimated by the node.
    Estimated,
    /// Not available.
    Unavailable,
}

/// Why generation stopped.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DoneReason {
    /// Natural end.
    Stop,
    /// Hit `max_tokens`.
    Length,
}

/// A job that ended without a result.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobFailed {
    /// Job id.
    pub job: String,
    /// Failure category.
    pub code: FailCode,
    /// Whether the runtime had started generating.
    pub started: bool,
    /// Wall time on the node in milliseconds, when known.
    pub worker_ms: Option<u32>,
}

/// Failure categories.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailCode {
    /// Model not shared by this node.
    ModelNotShared,
    /// Digest differs from the request.
    DigestMismatch,
    /// The runtime returned an error.
    Runtime,
    /// Deadline exceeded.
    Timeout,
    /// Cancelled on request.
    Cancelled,
}

/// A refusal.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ErrorMsg {
    /// Stable machine-readable code.
    pub code: String,
    /// Content-free human message.
    pub message: String,
}
