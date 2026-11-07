//! Validation tests: typed output, cross-references and `ocm -t` error reporting.

use ocm_config::{Enroll, ErrorKind, Policy, parse, test_config, validate};

fn fixture() -> &'static str {
    include_str!("fixtures/ocm.conf")
}

#[test]
fn the_example_config_is_valid_and_typed() {
    let cfg = test_config(fixture()).unwrap();

    assert_eq!(cfg.network.name, "Homelab mesh");
    assert_eq!(cfg.network.listen, 8080);
    assert_eq!(cfg.network.data_dir, "/var/lib/ocm");
    assert!(cfg.network.demo_mode);

    let agents = &cfg.tokens["agents"];
    assert_eq!(agents.allow_models, vec!["qwen2.5:1.5b", "llama3.2:3b"]);
    assert_eq!(agents.budget_occ, Some(500));
    let rate = agents.rate.unwrap();
    assert_eq!(rate.count, 60);
    assert_eq!(rate.window_ms, 60_000);

    let friends = &cfg.shares["friends"];
    assert_eq!(friends.enroll, Enroll::Node);
    assert_eq!(friends.expires_ms, Some(86_400_000));
    assert_eq!(friends.grant_occ, Some(100));
    assert_eq!(friends.max_nodes, Some(5));

    assert_eq!(cfg.shares["viewers"].enroll, Enroll::Request);
    assert_eq!(cfg.shares["viewers"].expires_ms, Some(604_800_000));

    assert_eq!(cfg.pools["small"].model, "qwen2.5:1.5b");
    assert_eq!(cfg.pools["small"].policy, Policy::ExpectedMs);
    assert_eq!(cfg.pools["medium"].policy, Policy::FewestActive);

    assert_eq!(cfg.routes["openai"].path, "/v1");
    assert_eq!(cfg.routes["openai"].pool, "small");
    assert!(cfg.routes["openai"].auth);
}

#[test]
fn defaults_apply_when_a_block_omits_values() {
    let text = r#"
network "m" {
    data_dir /tmp/ocm;
}
pool "p" {
    model qwen;
}
route "r" {
    pool p;
}
"#;
    let cfg = test_config(text).unwrap();
    assert_eq!(cfg.network.listen, 8080);
    assert!(cfg.network.demo_mode);
    assert_eq!(cfg.pools["p"].policy, Policy::ExpectedMs);
    assert_eq!(cfg.routes["r"].path, "/r");
    assert!(cfg.routes["r"].auth);
}

#[test]
fn duplicate_pool_is_reported() {
    let text = r#"
network "m" { listen 8080; }
pool "p" { model a; }
pool "p" { model b; }
"#;
    let errors = test_config(text).unwrap_err();
    assert!(errors.iter().any(|e| e.kind == ErrorKind::DuplicateName));
}

#[test]
fn route_referencing_an_undefined_pool_is_reported() {
    let text = r#"
network "m" { listen 8080; }
route "r" { path /v1; pool ghost; }
"#;
    let errors = test_config(text).unwrap_err();
    assert!(errors.iter().any(|e| e.kind == ErrorKind::UndefinedRef));
}

#[test]
fn unknown_directives_are_rejected_at_both_levels() {
    let top = test_config(r#"mystery "x";"#).unwrap_err();
    assert!(top.iter().any(|e| e.kind == ErrorKind::UnknownDirective));

    let nested = test_config(
        r#"
network "m" {
    listen 8080;
    mystery on;
}
"#,
    )
    .unwrap_err();
    assert!(nested.iter().any(|e| e.kind == ErrorKind::UnknownDirective));
}

#[test]
fn bad_values_are_reported() {
    let text = r#"
network "m" {
    listen 0;
    demo_mode perhaps;
}
token "t" {
    allow_models qwen;
    budget 500 USD;
    rate 60;
}
share "s" {
    enroll spectator;
    expires 24;
    grant 100;
}
pool "p" {
    model qwen;
    policy fastest;
}
route "r" {
    path v1;
    pool p;
}
"#;
    let errors = test_config(text).unwrap_err();
    let bad = errors.iter().filter(|e| e.kind == ErrorKind::BadValue).count();
    let arg = errors.iter().filter(|e| e.kind == ErrorKind::ArgCount).count();
    assert!(bad >= 5, "expected several bad values, got {bad}: {errors:?}");
    assert!(arg >= 1, "grant needs two arguments: {errors:?}");
}

#[test]
fn a_missing_network_block_is_reported() {
    let errors = test_config(r#"pool "p" { model qwen; }"#).unwrap_err();
    assert!(errors.iter().any(|e| e.kind == ErrorKind::Missing));
}

#[test]
fn every_problem_is_collected_not_just_the_first() {
    let text = r#"
network "m" { listen 0; }
pool "p" { model a; }
pool "p" { model b; }
route "r" { pool ghost; }
nonsense on;
"#;
    let errors = test_config(text).unwrap_err();
    assert!(errors.len() >= 3, "expected a full report, got {errors:?}");
}

#[test]
fn duplicate_network_block_is_reported() {
    let text = r#"
network "a" { listen 8080; }
network "b" { listen 8081; }
"#;
    let errors = test_config(text).unwrap_err();
    assert!(errors.iter().any(|e| e.kind == ErrorKind::DuplicateName));
}

#[test]
fn errors_carry_a_location_and_a_stable_slug() {
    let doc = parse("ocm.conf", "network \"m\" {\n  mystery on;\n}\n").unwrap();
    let errors = validate(&doc).unwrap_err();
    let err = errors
        .iter()
        .find(|e| e.kind == ErrorKind::UnknownDirective)
        .expect("unknown directive reported");
    assert_eq!(err.line, 2);
    assert!(err.column > 1);
    assert_eq!(err.kind.slug(), "UNKNOWN_DIRECTIVE");
    assert!(err.to_string().contains("ocm.conf:2:"));
}

#[test]
fn parse_errors_surface_through_ocm_t() {
    let errors = test_config("network \"m\" {\n").unwrap_err();
    assert_eq!(errors.len(), 1);
    assert_eq!(errors[0].kind, ErrorKind::UnclosedBlock);
}
