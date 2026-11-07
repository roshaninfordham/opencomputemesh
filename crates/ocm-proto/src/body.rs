//! Message bodies (stub until Task 2).

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
    /// Liveness probe used before the full message set exists.
    Ping,
}
