//! Eligibility and expected completion time.

use crate::Ewma;

/// Dense node index assigned by the coordinator.
pub type NodeId = u64;

/// Measured performance of one model on one node.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelStat {
    /// Model digest.
    pub digest: String,
    /// Loaded in memory now.
    pub loaded: bool,
    /// Output tokens per second.
    pub tps: Ewma,
    /// Time to first token in milliseconds.
    pub ttft_ms: Ewma,
}

/// What the scheduler knows about a node at decision time.
#[derive(Debug, Clone, PartialEq)]
pub struct NodeSnapshot {
    /// Node index.
    pub id: NodeId,
    /// Connected with a recent heartbeat.
    pub online: bool,
    /// Owner has sharing enabled.
    pub sharing: bool,
    /// Circuit breaker is open.
    pub breaker_open: bool,
    /// Reputation in [0, 1].
    pub reputation: f64,
    /// Owner's concurrent job limit.
    pub slots: u16,
    /// Jobs running now.
    pub active: u16,
    /// Output tokens already committed to this node.
    pub queued_tokens: u64,
    /// Served models.
    pub models: Vec<ModelStat>,
}

/// Scheduling knobs.
#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    /// Nodes below this reputation receive no work.
    pub reputation_floor: f64,
    /// Added when the model is not loaded.
    pub cold_load_ms: f64,
    /// Throughput assumed for nodes without measurements.
    pub prior_tps: f64,
    /// Time to first token assumed for nodes without measurements.
    pub prior_ttft_ms: f64,
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            reputation_floor: 0.5,
            cold_load_ms: 1_500.0,
            prior_tps: 20.0,
            prior_ttft_ms: 500.0,
        }
    }
}

/// A request to place.
#[derive(Debug, Clone, Copy)]
pub struct Request<'a> {
    /// Required model digest.
    pub digest: &'a str,
    /// Expected output tokens (the request's limit).
    pub est_tokens: u64,
    /// Nodes that must not be chosen (for example, after a failed attempt).
    pub exclude: &'a [NodeId],
    /// Only this node may be chosen.
    pub pin: Option<NodeId>,
}

/// The model stat when `node` may take `req`, else `None`.
pub fn eligible<'n>(node: &'n NodeSnapshot, req: &Request<'_>, policy: &Policy) -> Option<&'n ModelStat> {
    let open = node.online
        && node.sharing
        && !node.breaker_open
        && node.reputation >= policy.reputation_floor
        && node.active < node.slots
        && !req.exclude.contains(&node.id)
        && req.pin.is_none_or(|p| p == node.id);
    if !open {
        return None;
    }
    node.models.iter().find(|m| m.digest == req.digest)
}

/// Expected milliseconds until `req` would finish on `node`.
#[allow(clippy::cast_precision_loss)]
pub fn expected_ms(node: &NodeSnapshot, stat: &ModelStat, req: &Request<'_>, policy: &Policy) -> f64 {
    let tps = stat.tps.get().filter(|v| *v > 0.0).unwrap_or(policy.prior_tps);
    let ttft = stat.ttft_ms.get().unwrap_or(policy.prior_ttft_ms);
    let cold = if stat.loaded { 0.0 } else { policy.cold_load_ms };
    (node.queued_tokens + req.est_tokens) as f64 / tps * 1_000.0 + ttft + cold
}
