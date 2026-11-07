//! Parser tests for the `ocm.conf` language.

use ocm_config::{ErrorKind, MAX_DEPTH, parse};

#[test]
fn parses_directive_with_args() {
    let doc = parse("ocm.conf", "listen 8080;").unwrap();
    assert_eq!(doc.nodes.len(), 1);
    assert_eq!(doc.nodes[0].name, "listen");
    assert_eq!(doc.nodes[0].args, vec!["8080"]);
    assert!(doc.nodes[0].block.is_none());
}

#[test]
fn parses_nested_blocks() {
    let text = r#"
network "mesh" {
    listen 8080;
    pool "small" {
        model qwen2.5:1.5b;
    }
}
"#;
    let doc = parse("ocm.conf", text).unwrap();
    assert_eq!(doc.nodes.len(), 1);
    let network = &doc.nodes[0];
    assert_eq!(network.name, "network");
    assert_eq!(network.args, vec!["mesh"]);
    assert_eq!(network.children().len(), 2);
    assert_eq!(network.children()[1].name, "pool");
    assert_eq!(network.children()[1].children()[0].args, vec!["qwen2.5:1.5b"]);
}

#[test]
fn comments_and_blank_lines_are_ignored() {
    let text = "# a comment\n\nlisten 8080;   # trailing\n# end\n";
    let doc = parse("ocm.conf", text).unwrap();
    assert_eq!(doc.nodes.len(), 1);
    assert_eq!(doc.nodes[0].name, "listen");
}

#[test]
fn quoted_strings_keep_spaces_and_escapes() {
    let text = r#"token "a \"b\" c";"#;
    let doc = parse("ocm.conf", text).unwrap();
    assert_eq!(doc.nodes[0].args, vec![r#"a "b" c"#]);
}

#[test]
fn missing_terminator_is_an_error() {
    let err = parse("ocm.conf", "listen 8080").unwrap_err();
    assert_eq!(err.kind, ErrorKind::UnexpectedEof);
}

#[test]
fn unterminated_string_is_an_error() {
    let err = parse("ocm.conf", "network \"mesh {\n").unwrap_err();
    assert_eq!(err.kind, ErrorKind::UnterminatedString);
}

#[test]
fn unclosed_block_is_an_error() {
    let err = parse("ocm.conf", "network \"mesh\" {\n listen 8080;\n").unwrap_err();
    assert_eq!(err.kind, ErrorKind::UnclosedBlock);
}

#[test]
fn deep_nesting_is_rejected() {
    let mut text = String::new();
    for _ in 0..=MAX_DEPTH {
        text.push_str("a {\n");
    }
    for _ in 0..=MAX_DEPTH {
        text.push_str("}\n");
    }
    let err = parse("ocm.conf", &text).unwrap_err();
    assert_eq!(err.kind, ErrorKind::TooDeep);
}

#[test]
fn stray_closing_brace_is_an_error() {
    let err = parse("ocm.conf", "listen 8080;\n}\n").unwrap_err();
    assert_eq!(err.kind, ErrorKind::UnexpectedChar);
}

#[test]
fn errors_report_source_line_and_column() {
    let err = parse("conf.d/edge.conf", "\n\nlisten 8080\n").unwrap_err();
    assert_eq!(err.source, "conf.d/edge.conf");
    assert_eq!(err.line, 3);
    assert_eq!(err.column, 1);
}

#[test]
fn the_example_config_parses() {
    let text = include_str!("fixtures/ocm.conf");
    let doc = parse("ocm.conf", text).unwrap();
    assert!(doc.nodes.len() >= 6);
}
