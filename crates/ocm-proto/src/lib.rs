//! OpenComputeMesh node protocol v2: the frame envelope, message bodies and Ed25519 signing.
//! This crate performs no I/O.

mod body;
mod frame;

pub use body::{Accelerator, Body, Capabilities, DoneReason, ErrorMsg, FailCode, Heartbeat, Hello, JobCompleted, JobFailed, JobRef, JobStart, JobToken, Message, ModelInfo, Role, TokenSource, Usage, Welcome};
pub use frame::{Frame, MAX_FRAME_BYTES, PROTOCOL_VERSION, ProtoError};
