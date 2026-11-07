//! Configuration diagnostics.

use std::fmt;

/// Category of a configuration problem.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// The text ended in the middle of a directive or block.
    UnexpectedEof,
    /// A quoted string was not terminated.
    UnterminatedString,
    /// A byte cannot appear outside a quoted string.
    UnexpectedChar,
    /// A directive was not followed by `;` or `{`.
    ExpectedTerminator,
    /// A block was not closed with `}`.
    UnclosedBlock,
    /// Blocks nested deeper than [`crate::MAX_DEPTH`].
    TooDeep,
    /// The directive is not known in this context.
    UnknownDirective,
    /// Wrong number of arguments.
    ArgCount,
    /// Two directives of the same kind share a name.
    DuplicateName,
    /// A reference to a name that is not defined.
    UndefinedRef,
    /// A value has the wrong shape: duration, rate, port, boolean or enum.
    BadValue,
    /// A required directive is missing.
    Missing,
    /// `include` cycled back to a file already being read.
    IncludeCycle,
    /// `include` nesting exceeded the limit.
    IncludeDepth,
}

impl ErrorKind {
    /// A short stable slug, for tests and machine-readable output.
    pub fn slug(self) -> &'static str {
        match self {
            Self::UnexpectedEof => "UNEXPECTED_EOF",
            Self::UnterminatedString => "UNTERMINATED_STRING",
            Self::UnexpectedChar => "UNEXPECTED_CHAR",
            Self::ExpectedTerminator => "EXPECTED_TERMINATOR",
            Self::UnclosedBlock => "UNCLOSED_BLOCK",
            Self::TooDeep => "TOO_DEEP",
            Self::UnknownDirective => "UNKNOWN_DIRECTIVE",
            Self::ArgCount => "ARG_COUNT",
            Self::DuplicateName => "DUPLICATE_NAME",
            Self::UndefinedRef => "UNDEFINED_REF",
            Self::BadValue => "BAD_VALUE",
            Self::Missing => "MISSING",
            Self::IncludeCycle => "INCLUDE_CYCLE",
            Self::IncludeDepth => "INCLUDE_DEPTH",
        }
    }
}

/// One configuration problem with its location.
///
/// Details never contain file contents: a config file can hold secrets, and a
/// diagnostic must be safe to paste into an issue.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfigError {
    /// What went wrong.
    pub kind: ErrorKind,
    /// File the problem is in.
    pub source: String,
    /// 1-based line.
    pub line: u32,
    /// 1-based column.
    pub column: u32,
    /// Human detail naming the directive or value shape, never its contents.
    pub detail: String,
}

impl ConfigError {
    /// A diagnostic at an explicit location.
    pub fn new(
        kind: ErrorKind,
        source: impl Into<String>,
        line: u32,
        column: u32,
        detail: impl Into<String>,
    ) -> Self {
        Self {
            kind,
            source: source.into(),
            line,
            column,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{}:{}:{}: {} [{}]",
            self.source,
            self.line,
            self.column,
            self.detail,
            self.kind.slug()
        )
    }
}

impl std::error::Error for ConfigError {}
