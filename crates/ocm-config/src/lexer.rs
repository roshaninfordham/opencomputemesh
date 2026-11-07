//! Tokenizer for the `ocm.conf` language.
//!
//! The syntax is nginx's: bare words, double-quoted strings, `{`, `}` and `;`
//! and `#` comments. Newlines are whitespace and carry no meaning.

use crate::error::{ConfigError, ErrorKind};

/// One lexical token.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Tok {
    /// A bare word or a quoted string's contents.
    Word(String),
    /// `{`, opening a block.
    LBrace,
    /// `}`, closing a block.
    RBrace,
    /// `;`, ending a directive.
    Semi,
}

/// A token with its 1-based position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Spanned {
    /// The token itself.
    pub tok: Tok,
    /// Line the token starts on.
    pub line: u32,
    /// Column the token starts at.
    pub column: u32,
}

/// Byte cursor that tracks the line and column of what it is looking at.
struct Lexer<'a> {
    source: &'a str,
    text: &'a str,
    bytes: &'a [u8],
    i: usize,
    line: u32,
    column: u32,
}

impl<'a> Lexer<'a> {
    fn new(source: &'a str, text: &'a str) -> Self {
        Self {
            source,
            text,
            bytes: text.as_bytes(),
            i: 0,
            line: 1,
            column: 1,
        }
    }

    fn run(mut self) -> Result<Vec<Spanned>, ConfigError> {
        let mut out = Vec::new();
        while let Some(&byte) = self.bytes.get(self.i) {
            match byte {
                b' ' | b'\t' | b'\r' => self.advance(1),
                b'\n' => self.newline(),
                b'#' => self.skip_comment(),
                b'{' => out.push(self.punct(Tok::LBrace)),
                b'}' => out.push(self.punct(Tok::RBrace)),
                b';' => out.push(self.punct(Tok::Semi)),
                b'"' => out.push(self.quoted()?),
                _ => out.push(self.word()),
            }
        }
        Ok(out)
    }

    fn advance(&mut self, n: usize) {
        self.i += n;
        self.column = self.column.saturating_add(u32::try_from(n).unwrap_or(u32::MAX));
    }

    fn newline(&mut self) {
        self.i += 1;
        self.line += 1;
        self.column = 1;
    }

    fn skip_comment(&mut self) {
        while self.bytes.get(self.i).is_some_and(|&b| b != b'\n') {
            self.i += 1;
            self.column += 1;
        }
    }

    fn punct(&mut self, tok: Tok) -> Spanned {
        let (line, column) = (self.line, self.column);
        self.advance(1);
        Spanned { tok, line, column }
    }

    fn fail(&self, kind: ErrorKind, line: u32, column: u32, detail: &str) -> ConfigError {
        ConfigError::new(kind, self.source, line, column, detail)
    }

    fn word(&mut self) -> Spanned {
        let (line, column) = (self.line, self.column);
        let start = self.i;
        while let Some(&byte) = self.bytes.get(self.i) {
            if byte.is_ascii_whitespace() || matches!(byte, b'{' | b'}' | b';' | b'#' | b'"') {
                break;
            }
            self.i += 1;
            self.column += 1;
        }
        Spanned {
            tok: Tok::Word(self.text[start..self.i].to_owned()),
            line,
            column,
        }
    }

    fn quoted(&mut self) -> Result<Spanned, ConfigError> {
        let (line, column) = (self.line, self.column);
        self.advance(1);
        let mut buf: Vec<u8> = Vec::new();
        loop {
            let Some(&byte) = self.bytes.get(self.i) else {
                return Err(self.fail(
                    ErrorKind::UnterminatedString,
                    line,
                    column,
                    "quoted string is not terminated",
                ));
            };
            match byte {
                b'"' => {
                    self.advance(1);
                    break;
                }
                b'\\' => {
                    let Some(&escaped) = self.bytes.get(self.i + 1) else {
                        return Err(self.fail(
                            ErrorKind::UnterminatedString,
                            line,
                            column,
                            "quoted string ends in a backslash",
                        ));
                    };
                    buf.push(match escaped {
                        b'n' => b'\n',
                        b't' => b'\t',
                        other => other,
                    });
                    self.advance(2);
                }
                b'\n' => {
                    buf.push(b'\n');
                    self.newline();
                }
                other => {
                    buf.push(other);
                    self.advance(1);
                }
            }
        }
        Ok(Spanned {
            tok: Tok::Word(String::from_utf8_lossy(&buf).into_owned()),
            line,
            column,
        })
    }
}

/// Split `text` into tokens, rejecting unterminated strings.
pub(crate) fn lex(source: &str, text: &str) -> Result<Vec<Spanned>, ConfigError> {
    Lexer::new(source, text).run()
}
