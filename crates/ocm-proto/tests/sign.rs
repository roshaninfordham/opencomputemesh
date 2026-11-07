//! Integration tests.

use ocm_proto::*;

fn completed(text_tokens: u32) -> Frame {
    Frame::new(
        1,
        1,
        Body::JobCompleted(JobCompleted {
            job: "j1".into(),
            usage: Usage {
                input_tokens: Some(30),
                output_tokens: Some(text_tokens),
                source: TokenSource::Runtime,
                load_ms: None,
                prompt_ms: None,
                generation_ms: None,
            },
            worker_ms: 10,
            ttft_ms: None,
            done_reason: Some(DoneReason::Stop),
        }),
    )
}

#[test]
fn signed_frame_verifies_after_wire_roundtrip() {
    let key = NodeKey::generate().unwrap();
    let mut f = completed(10);
    key.sign(&mut f);
    let received = Frame::decode(&f.encode()).unwrap();
    assert_eq!(verify(&received, &key.public_hex()), Ok(()));
}

#[test]
fn tampered_usage_is_rejected() {
    let key = NodeKey::generate().unwrap();
    let mut f = completed(10);
    key.sign(&mut f);
    let mut forged = completed(1_000_000);
    forged.sig = f.sig.clone();
    assert_eq!(verify(&forged, &key.public_hex()), Err(ProtoError::BadSignature));
}

#[test]
fn wrong_key_missing_signature_and_garbage_are_rejected() {
    let (a, b) = (NodeKey::generate().unwrap(), NodeKey::generate().unwrap());
    let mut f = completed(10);
    assert_eq!(verify(&f, &a.public_hex()), Err(ProtoError::MissingSignature));
    a.sign(&mut f);
    assert_eq!(verify(&f, &b.public_hex()), Err(ProtoError::BadSignature));
    assert_eq!(verify(&f, "zz"), Err(ProtoError::BadSignature));
    f.sig = Some("00".into());
    assert_eq!(verify(&f, &a.public_hex()), Err(ProtoError::BadSignature));
}

#[test]
fn secret_roundtrip_preserves_identity() {
    let key = NodeKey::generate().unwrap();
    assert_eq!(NodeKey::from_secret(key.secret()).public_hex(), key.public_hex());
    assert_eq!(key.public_hex().len(), 64);
}
