//! OpenComputeMesh load manager. Pure functions over node snapshots: no I/O, no clocks, no global state.
//! Callers pass time as `now_ms` and randomness as an explicit [`Rng`].

mod ewma;
mod rng;
mod score;

pub use ewma::Ewma;
pub use rng::Rng;
pub use score::{ModelStat, NodeId, NodeSnapshot, Policy, Request, eligible, expected_ms};
