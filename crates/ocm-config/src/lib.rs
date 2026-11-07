//! `OpenComputeMesh` edge configuration: an nginx-style language, parsed and validated.
//!
//! The crate performs no I/O. [`parse`] takes text; include resolution is left to
//! the caller, which keeps parsing deterministic and fuzzable. [`validate`]
//! returns typed configuration and reports every problem it finds, which is what
//! makes `ocm -t` useful.

mod ast;
mod error;
mod lexer;
mod parse;
mod validate;

pub use ast::{Document, Node};
pub use error::{ConfigError, ErrorKind};
pub use parse::{MAX_DEPTH, parse};
pub use validate::{
    Config, Enroll, NetworkCfg, Policy, PoolCfg, Rate, RouteCfg, ShareCfg, TokenCfg, validate,
};

/// The file name used when a caller has no better label for its text.
pub const DEFAULT_SOURCE: &str = "ocm.conf";

/// Parse and validate one configuration document, as `ocm -t` does.
///
/// # Errors
/// Returns every parse or validation problem, location first.
pub fn test_config(text: &str) -> Result<Config, Vec<ConfigError>> {
    match parse(DEFAULT_SOURCE, text) {
        Ok(doc) => validate(&doc),
        Err(e) => Err(vec![e]),
    }
}
