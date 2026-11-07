//! The frame envelope shared by every message.

use std::fmt;

use serde::{Deserialize, Serialize};

use crate::Body;

/// Protocol version carried by every frame.
pub const PROTOCOL_VERSION: u16 = 2;

/// Largest accepted encoded frame in bytes (1 MiB).
pub const MAX_FRAME_BYTES: usize = 1 << 20;

/// One protocol message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Frame {
    /// Protocol version; must equal [`PROTOCOL_VERSION`].
    pub v: u16,
    /// Sender-unique, increasing frame id.
    pub id: u64,
    /// Sender wall-clock time in Unix milliseconds.
    pub ts: u64,
    /// The message payload.
    pub body: Body,
    /// Lowercase hex Ed25519 signature over the canonical bytes of this frame with `sig` omitted.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sig: Option<String>,
}

/// Why a frame was rejected. Messages never include payload content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtoError {
    /// The encoded frame exceeds [`MAX_FRAME_BYTES`]; carries the received length.
    TooLarge(usize),
    /// The bytes are not a valid v2 frame; carries a content-free description.
    Malformed(String),
    /// The frame declares an unsupported protocol version.
    Version(u16),
    /// A required signature is absent.
    MissingSignature,
    /// The signature or key is invalid.
    BadSignature,
}

impl fmt::Display for ProtoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TooLarge(n) => write!(f, "frame of {n} bytes exceeds {MAX_FRAME_BYTES}"),
            Self::Malformed(why) => write!(f, "malformed frame: {why}"),
            Self::Version(v) => write!(
                f,
                "unsupported protocol version {v}; upgrade to version {PROTOCOL_VERSION}"
            ),
            Self::MissingSignature => f.write_str("missing signature"),
            Self::BadSignature => f.write_str("invalid signature"),
        }
    }
}

impl std::error::Error for ProtoError {}

impl Frame {
    /// A new unsigned frame at the current protocol version.
    pub fn new(id: u64, ts: u64, body: Body) -> Self {
        Self {
            v: PROTOCOL_VERSION,
            id,
            ts,
            body,
            sig: None,
        }
    }

    /// Canonical encoding: struct fields serialize in declaration order, so equal frames encode equally.
    ///
    /// # Panics
    /// Never: every field type serializes infallibly.
    pub fn encode(&self) -> Vec<u8> {
        serde_json::to_vec(self).expect("frame fields always serialize")
    }

    /// Decode and validate one frame.
    ///
    /// # Errors
    /// Returns [`ProtoError`] for oversized, malformed or wrong-version frames.
    pub fn decode(bytes: &[u8]) -> Result<Self, ProtoError> {
        if bytes.len() > MAX_FRAME_BYTES {
            return Err(ProtoError::TooLarge(bytes.len()));
        }
        match serde_json::from_slice::<Frame>(bytes) {
            Ok(f) if f.v == PROTOCOL_VERSION => Ok(f),
            Ok(f) => Err(ProtoError::Version(f.v)),
            Err(e) => Err(legacy_version(bytes)
                .map_or_else(|| ProtoError::Malformed(describe(&e)), ProtoError::Version)),
        }
    }
}

/// A content-free description of a JSON error: its category and position only.
fn describe(e: &serde_json::Error) -> String {
    let kind = match e.classify() {
        serde_json::error::Category::Io => "io",
        serde_json::error::Category::Syntax => "syntax",
        serde_json::error::Category::Data => "invalid structure",
        serde_json::error::Category::Eof => "unexpected end",
    };
    format!("{kind} at line {} column {}", e.line(), e.column())
}

/// v1 frames are flat objects with `protocol_version`; report their version so callers can say "upgrade".
fn legacy_version(bytes: &[u8]) -> Option<u16> {
    let v: serde_json::Value = serde_json::from_slice(bytes).ok()?;
    u16::try_from(v.get("protocol_version")?.as_u64()?)
        .ok()
        .filter(|n| *n != PROTOCOL_VERSION)
}
