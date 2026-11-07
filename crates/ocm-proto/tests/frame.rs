use ocm_proto::{Body, Frame, MAX_FRAME_BYTES, PROTOCOL_VERSION, ProtoError};

#[test]
fn roundtrip() {
    let f = Frame::new(7, 1_700_000_000_000, Body::Ping);
    assert_eq!(Frame::decode(&f.encode()).unwrap(), f);
    assert_eq!(f.v, PROTOCOL_VERSION);
}

#[test]
fn rejects_unknown_top_level_field() {
    let raw = br#"{"v":2,"id":1,"ts":1,"body":{"type":"ping"},"extra":1}"#;
    assert!(matches!(Frame::decode(raw), Err(ProtoError::Malformed(_))));
}

#[test]
fn rejects_oversized_frame() {
    let raw = vec![b' '; MAX_FRAME_BYTES + 1];
    assert_eq!(Frame::decode(&raw), Err(ProtoError::TooLarge(MAX_FRAME_BYTES + 1)));
}

#[test]
fn rejects_wrong_version() {
    let raw = br#"{"v":3,"id":1,"ts":1,"body":{"type":"ping"}}"#;
    assert_eq!(Frame::decode(raw), Err(ProtoError::Version(3)));
}

#[test]
fn identifies_v1_clients() {
    let raw = br#"{"type":"node.hello","protocol_version":1}"#;
    assert_eq!(Frame::decode(raw), Err(ProtoError::Version(1)));
}

#[test]
fn errors_never_echo_payload() {
    let raw = br#"{"v":2,"id":1,"ts":1,"body":{"type":"ping","data":"SECRET-PROMPT"}}"#;
    let err = Frame::decode(raw).unwrap_err();
    assert!(!err.to_string().contains("SECRET-PROMPT"), "{err}");
    let raw = br#"{"v":"SECRET-PROMPT","id":1,"ts":1,"body":{"type":"ping"}}"#;
    assert!(!Frame::decode(raw).unwrap_err().to_string().contains("SECRET-PROMPT"));
}

#[test]
fn deep_nesting_is_malformed_not_a_crash() {
    let mut raw = String::from(r#"{"v":2,"id":1,"ts":1,"body":"#);
    raw.push_str(&"[".repeat(10_000));
    raw.push_str(&"]".repeat(10_000));
    raw.push('}');
    assert!(matches!(Frame::decode(raw.as_bytes()), Err(ProtoError::Malformed(_))));
}
