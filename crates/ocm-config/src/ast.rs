//! Parsed configuration tree.

/// One directive: a name, positional arguments and an optional block.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Node {
    /// Directive name, for example `pool`.
    pub name: String,
    /// Positional arguments after the name.
    pub args: Vec<String>,
    /// Nested directives, when the directive opened a block.
    pub block: Option<Vec<Node>>,
    /// 1-based line where the directive starts.
    pub line: u32,
    /// 1-based column where the directive starts.
    pub column: u32,
    /// Source label for diagnostics, such as `ocm.conf`.
    pub source: String,
}

impl Node {
    /// The nested directives, or an empty slice when this directive has no block.
    pub fn children(&self) -> &[Node] {
        self.block.as_deref().unwrap_or(&[])
    }

    /// Whether this directive opened a block.
    pub fn has_block(&self) -> bool {
        self.block.is_some()
    }
}

/// A whole configuration file.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Document {
    /// Top-level directives, in file order.
    pub nodes: Vec<Node>,
}
