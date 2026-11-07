//! Node identity and result-frame signatures.

use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};

use crate::{Frame, ProtoError};

/// A node's Ed25519 identity. The secret never leaves the node.
pub struct NodeKey(SigningKey);

impl NodeKey {
    /// Generate a key from operating-system randomness.
    ///
    /// # Errors
    /// Returns the OS error when randomness is unavailable.
    pub fn generate() -> Result<Self, getrandom::Error> {
        let mut secret = [0u8; 32];
        getrandom::fill(&mut secret)?;
        Ok(Self::from_secret(secret))
    }

    /// Restore a key from its 32-byte secret.
    pub fn from_secret(secret: [u8; 32]) -> Self {
        Self(SigningKey::from_bytes(&secret))
    }

    /// The 32-byte secret, for storage with owner-only file permissions.
    pub fn secret(&self) -> [u8; 32] {
        self.0.to_bytes()
    }

    /// Lowercase hex public key.
    pub fn public_hex(&self) -> String {
        to_hex(self.0.verifying_key().as_bytes())
    }

    /// Sign the canonical bytes of `frame` with `sig` omitted, and set `sig`.
    pub fn sign(&self, frame: &mut Frame) {
        frame.sig = None;
        let sig = self.0.sign(&frame.encode());
        frame.sig = Some(to_hex(&sig.to_bytes()));
    }
}

/// Verify a frame's signature against a lowercase hex public key.
///
/// # Errors
/// [`ProtoError::MissingSignature`] when unsigned; [`ProtoError::BadSignature`] for any invalid key or signature.
pub fn verify(frame: &Frame, public_hex: &str) -> Result<(), ProtoError> {
    let sig_hex = frame.sig.as_deref().ok_or(ProtoError::MissingSignature)?;
    let key = from_hex::<32>(public_hex)
        .and_then(|b| VerifyingKey::from_bytes(&b).ok())
        .ok_or(ProtoError::BadSignature)?;
    let sig = from_hex::<64>(sig_hex)
        .map(|b| Signature::from_bytes(&b))
        .ok_or(ProtoError::BadSignature)?;
    let mut unsigned = frame.clone();
    unsigned.sig = None;
    key.verify(&unsigned.encode(), &sig)
        .map_err(|_| ProtoError::BadSignature)
}

fn to_hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(char::from(DIGITS[usize::from(b >> 4)]));
        out.push(char::from(DIGITS[usize::from(b & 0x0f)]));
    }
    out
}

fn from_hex<const N: usize>(s: &str) -> Option<[u8; N]> {
    let raw = s.as_bytes();
    if raw.len() != N * 2 {
        return None;
    }
    let nibble = |c: u8| match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        _ => None,
    };
    let mut out = [0u8; N];
    for (i, pair) in raw.chunks_exact(2).enumerate() {
        out[i] = (nibble(pair[0])? << 4) | nibble(pair[1])?;
    }
    Some(out)
}
