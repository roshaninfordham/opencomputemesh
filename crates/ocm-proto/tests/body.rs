//! Integration tests.

use ocm_proto::*;

fn caps() -> Capabilities {
    Capabilities {
        name: "studio".into(),
        platform: "macOS 26".into(),
        arch: "arm64".into(),
        cpu: Some("Apple M5".into()),
        cores: Some(10),
        mem_total: Some(16 << 30),
        accelerators: vec![Accelerator {
            name: "Apple M5".into(),
            mem: None,
            source: "system_profiler".into(),
        }],
        models: vec![ModelInfo {
            id: "qwen2.5:1.5b".into(),
            digest: "65ec0654".into(),
            runtime: "ollama".into(),
            loaded: true,
            params: Some("1.5B".into()),
            quant: Some("Q4_K_M".into()),
        }],
        slots: 1,
    }
}

fn samples() -> Vec<Body> {
    vec![
        Body::Hello(Hello {
            enrollment_token: Some("ocme_x".into()),
            node_id: None,
            public_key: "ab".repeat(32),
            caps: caps(),
        }),
        Body::Welcome(Welcome { node_id: "n1".into() }),
        Body::Heartbeat(Heartbeat {
            healthy: true,
            mem_free: Some(1 << 30),
            loaded: vec!["65ec0654".into()],
        }),
        Body::Capabilities(caps()),
        Body::JobStart(JobStart {
            job: "j1".into(),
            model: "qwen2.5:1.5b".into(),
            digest: "65ec0654".into(),
            messages: vec![Message {
                role: Role::User,
                content: "hi".into(),
            }],
            max_tokens: 64,
            temperature: Some(0.0),
            top_p: None,
            seed: Some(7),
            deadline_ms: 180_000,
        }),
        Body::JobCancel(JobRef { job: "j1".into() }),
        Body::JobStarted(JobRef { job: "j1".into() }),
        Body::JobToken(JobToken {
            job: "j1".into(),
            seq: 1,
            text: "Hel".into(),
        }),
        Body::JobCompleted(JobCompleted {
            job: "j1".into(),
            usage: Usage {
                input_tokens: Some(30),
                output_tokens: Some(10),
                source: TokenSource::Runtime,
                load_ms: Some(1),
                prompt_ms: Some(2),
                generation_ms: Some(3),
            },
            worker_ms: 12,
            ttft_ms: Some(5),
            done_reason: Some(DoneReason::Stop),
        }),
        Body::JobFailed(JobFailed {
            job: "j1".into(),
            code: FailCode::Runtime,
            started: true,
            worker_ms: Some(4),
        }),
        Body::Error(ErrorMsg {
            code: "RETRY_LATER".into(),
            message: "queue full".into(),
        }),
        Body::Ping,
        Body::Pong,
    ]
}

#[test]
fn every_body_roundtrips() {
    for (i, body) in samples().into_iter().enumerate() {
        let f = Frame::new(i as u64, 1, body);
        assert_eq!(Frame::decode(&f.encode()).unwrap(), f);
    }
}

#[test]
fn unknown_field_inside_data_is_rejected() {
    let raw = br#"{"v":2,"id":1,"ts":1,"body":{"type":"job_cancel","data":{"job":"j1","x":1}}}"#;
    assert!(matches!(Frame::decode(raw), Err(ProtoError::Malformed(_))));
}

#[test]
fn unknown_type_is_rejected() {
    let raw = br#"{"v":2,"id":1,"ts":1,"body":{"type":"shell_exec","data":{}}}"#;
    assert!(matches!(Frame::decode(raw), Err(ProtoError::Malformed(_))));
}

#[test]
fn result_bodies_require_signatures() {
    assert_eq!(samples().iter().filter(|b| b.requires_signature()).count(), 2);
}
