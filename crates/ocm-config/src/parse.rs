//! Recursive-descent parser.
//!
//! Grammar:
//!
//! ```text
//! document  := directive*
//! directive := WORD arg* ( ';' | '{' directive* '}' )
//! arg       := WORD
//! ```
//!
//! `include` is *not* expanded here: the crate stays I/O-free and the caller
//! resolves includes through a loader.

use crate::ast::{Document, Node};
use crate::error::{ConfigError, ErrorKind};
use crate::lexer::{Spanned, Tok, lex};

/// Maximum block nesting depth.
pub const MAX_DEPTH: usize = 32;

/// Parse one configuration document.
///
/// # Errors
/// Returns the first syntax error, located at `file:line:column`.
pub fn parse(source: &str, text: &str) -> Result<Document, ConfigError> {
    let toks = lex(source, text)?;
    let mut parser = Parser {
        toks,
        i: 0,
        source,
        depth: 0,
    };
    let nodes = parser.block(false)?;
    Ok(Document { nodes })
}

struct Parser<'a> {
    toks: Vec<Spanned>,
    i: usize,
    source: &'a str,
    depth: usize,
}

impl Parser<'_> {
    fn peek(&self) -> Option<&Spanned> {
        self.toks.get(self.i)
    }

    fn here(&self) -> (u32, u32) {
        match self.peek() {
            Some(s) => (s.line, s.column),
            None => self.toks.last().map_or((1, 1), |s| (s.line, s.column)),
        }
    }

    fn fail(&self, kind: ErrorKind, detail: &str) -> ConfigError {
        let (line, column) = self.here();
        ConfigError::new(kind, self.source, line, column, detail)
    }

    fn block(&mut self, nested: bool) -> Result<Vec<Node>, ConfigError> {
        if nested {
            self.depth += 1;
            if self.depth > MAX_DEPTH {
                return Err(self.fail(ErrorKind::TooDeep, "blocks are nested too deeply"));
            }
        }

        let mut nodes = Vec::new();
        loop {
            let Some(spanned) = self.peek().cloned() else {
                if nested {
                    return Err(self.fail(ErrorKind::UnclosedBlock, "block is not closed with '}'"));
                }
                break;
            };

            match spanned.tok {
                Tok::RBrace => {
                    if nested {
                        self.i += 1;
                        self.depth -= 1;
                        break;
                    }
                    return Err(self.fail(ErrorKind::UnexpectedChar, "unexpected '}' outside a block"));
                }
                Tok::LBrace | Tok::Semi => {
                    return Err(self.fail(
                        ErrorKind::ExpectedTerminator,
                        "expected a directive name before a block or terminator",
                    ));
                }
                Tok::Word(name) => {
                    self.i += 1;
                    let mut args = Vec::new();
                    while let Some(Spanned {
                        tok: Tok::Word(word), ..
                    }) = self.peek()
                    {
                        args.push(word.clone());
                        self.i += 1;
                    }

                    let terminator = self.peek().cloned();
                    match terminator {
                        Some(Spanned { tok: Tok::Semi, .. }) => {
                            self.i += 1;
                            nodes.push(Node {
                                name,
                                args,
                                block: None,
                                line: spanned.line,
                                column: spanned.column,
                                source: self.source.to_owned(),
                            });
                        }
                        Some(Spanned { tok: Tok::LBrace, .. }) => {
                            self.i += 1;
                            let inner = self.block(true)?;
                            nodes.push(Node {
                                name,
                                args,
                                block: Some(inner),
                                line: spanned.line,
                                column: spanned.column,
                                source: self.source.to_owned(),
                            });
                        }
                        Some(_) => {
                            return Err(self.fail(
                                ErrorKind::ExpectedTerminator,
                                "directive must end with ';' or open a block with '{'",
                            ));
                        }
                        None => {
                            // Point at the directive that is missing its `;`,
                            // not at the last argument it managed to read.
                            return Err(ConfigError::new(
                                ErrorKind::UnexpectedEof,
                                self.source,
                                spanned.line,
                                spanned.column,
                                "file ends inside a directive",
                            ));
                        }
                    }
                }
            }
        }

        Ok(nodes)
    }
}
