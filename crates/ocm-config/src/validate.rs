//! Typed configuration and whole-file validation.
//!
//! Validation collects every problem instead of failing on the first, because
//! `ocm -t` is only useful if it reports the whole list.

use std::collections::BTreeMap;

use crate::ast::{Document, Node};
use crate::error::{ConfigError, ErrorKind};

/// Routing policy for a pool.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Policy {
    /// Fewest expected milliseconds to completion.
    ExpectedMs,
    /// Fewest active jobs, then freshest heartbeat.
    FewestActive,
}

/// A sliding-window rate limit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rate {
    /// Allowed events per window.
    pub count: u32,
    /// Window length in milliseconds.
    pub window_ms: u64,
}

/// What a share link lets a visitor do.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Enroll {
    /// Offer compute by enrolling a node.
    Node,
    /// Only send requests.
    Request,
}

/// The single `network` block.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetworkCfg {
    /// Display name.
    pub name: String,
    /// TCP port to bind.
    pub listen: u16,
    /// Directory for the database and join code.
    pub data_dir: String,
    /// Grant new members demo credits.
    pub demo_mode: bool,
}

/// A key an agent authenticates with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TokenCfg {
    /// Key name.
    pub name: String,
    /// Models the key may reach; `None` allows every model.
    ///
    /// An `allow_models` directive with no arguments is an error rather than an
    /// allow-all wildcard, so a typo can never widen access.
    pub allow_models: Option<Vec<String>>,
    /// Credit budget in whole OCC; `None` is unlimited.
    ///
    /// `Some(0)` is meaningful: it suspends the key without deleting it.
    pub budget_occ: Option<u64>,
    /// Requests per window.
    pub rate: Option<Rate>,
}

/// A shareable onboarding link.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShareCfg {
    /// Link name, used in the URL.
    pub name: String,
    /// What the visitor may do.
    pub enroll: Enroll,
    /// Lifetime in milliseconds.
    pub expires_ms: Option<u64>,
    /// Credits granted to the visitor.
    pub grant_occ: Option<u64>,
    /// Nodes the link may enroll.
    pub max_nodes: Option<u32>,
}

/// Workers serving one exact model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PoolCfg {
    /// Pool name.
    pub name: String,
    /// Model id this pool serves.
    pub model: String,
    /// Selection policy.
    pub policy: Policy,
}

/// An HTTP surface mapped to a pool.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteCfg {
    /// Route name.
    pub name: String,
    /// Path prefix.
    pub path: String,
    /// Pool serving this route.
    pub pool: String,
    /// Require a bearer token.
    pub auth: bool,
}

/// A validated configuration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    /// The network block.
    pub network: NetworkCfg,
    /// Agent keys by name.
    pub tokens: BTreeMap<String, TokenCfg>,
    /// Share links by name.
    pub shares: BTreeMap<String, ShareCfg>,
    /// Worker pools by name.
    pub pools: BTreeMap<String, PoolCfg>,
    /// HTTP routes by name.
    pub routes: BTreeMap<String, RouteCfg>,
}

#[derive(Default)]
struct Ctx {
    errors: Vec<ConfigError>,
}

impl Ctx {
    fn err(&mut self, node: &Node, kind: ErrorKind, detail: impl Into<String>) {
        self.errors.push(ConfigError::new(
            kind,
            node.source.clone(),
            node.line,
            node.column,
            detail,
        ));
    }

    fn args(&mut self, node: &Node, want: usize) -> bool {
        if node.args.len() == want {
            return true;
        }
        let detail = format!(
            "`{}` takes {want} argument(s), found {}",
            node.name,
            node.args.len()
        );
        self.err(node, ErrorKind::ArgCount, detail);
        false
    }

    fn block(&mut self, node: &Node) -> bool {
        if node.has_block() {
            return true;
        }
        let detail = format!("`{}` must open a block", node.name);
        self.err(node, ErrorKind::Missing, detail);
        false
    }

    fn only(&mut self, node: &Node, allowed: &[&str]) {
        for child in node.children() {
            if !allowed.contains(&child.name.as_str()) {
                let detail = format!("`{}` is not valid inside `{}`", child.name, node.name);
                self.err(child, ErrorKind::UnknownDirective, detail);
            }
        }
    }
}

/// Parse a duration such as `500ms`, `10s`, `30m`, `24h` or `7d` into milliseconds.
fn parse_duration(text: &str) -> Option<u64> {
    let split = text.find(|c: char| !c.is_ascii_digit())?;
    let (digits, unit) = text.split_at(split);
    let n: u64 = digits.parse().ok()?;
    let mult = match unit {
        "ms" => 1,
        "s" => 1_000,
        "m" => 60_000,
        "h" => 3_600_000,
        "d" => 86_400_000,
        _ => return None,
    };
    n.checked_mul(mult)
}

/// Parse a rate such as `60/min`, `10/s` or `1000/h`.
fn parse_rate(text: &str) -> Option<Rate> {
    let (count, unit) = text.split_once('/')?;
    let count: u32 = count.parse().ok()?;
    let window_ms = match unit {
        "s" => 1_000,
        "min" => 60_000,
        "h" => 3_600_000,
        _ => return None,
    };
    if count == 0 {
        return None;
    }
    Some(Rate { count, window_ms })
}

fn parse_bool(text: &str) -> Option<bool> {
    match text {
        "on" | "true" | "yes" => Some(true),
        "off" | "false" | "no" => Some(false),
        _ => None,
    }
}

fn insert_unique<T>(ctx: &mut Ctx, node: &Node, map: &mut BTreeMap<String, T>, name: String, value: T) {
    if map.contains_key(&name) {
        let detail = format!("duplicate `{}` named `{name}`", node.name);
        ctx.err(node, ErrorKind::DuplicateName, detail);
        return;
    }
    map.insert(name, value);
}

fn parse_network(ctx: &mut Ctx, node: &Node) -> NetworkCfg {
    ctx.args(node, 1);
    ctx.block(node);
    ctx.only(node, &["listen", "data_dir", "demo_mode"]);

    let mut listen = None;
    let mut data_dir = None;
    let mut demo_mode = None;

    for child in node.children() {
        if !ctx.args(child, 1) {
            continue;
        }
        let value = child.args.first().map_or("", String::as_str);
        match child.name.as_str() {
            "listen" => match value.parse::<u16>() {
                Ok(port) if port > 0 => listen = Some(port),
                _ => ctx.err(child, ErrorKind::BadValue, "`listen` must be a port 1-65535"),
            },
            "data_dir" => data_dir = Some(value.to_owned()),
            "demo_mode" => match parse_bool(value) {
                Some(flag) => demo_mode = Some(flag),
                None => ctx.err(child, ErrorKind::BadValue, "`demo_mode` must be on/off"),
            },
            _ => {}
        }
    }

    let name = node
        .args
        .first()
        .cloned()
        .unwrap_or_else(|| "OpenComputeMesh".to_owned());
    NetworkCfg {
        name,
        listen: listen.unwrap_or(8080),
        data_dir: data_dir.unwrap_or_else(|| "/var/lib/ocm".to_owned()),
        demo_mode: demo_mode.unwrap_or(true),
    }
}

fn parse_token(ctx: &mut Ctx, node: &Node) -> Option<TokenCfg> {
    ctx.args(node, 1);
    ctx.block(node);
    ctx.only(node, &["allow_models", "budget", "rate"]);

    let name = node.args.first()?.clone();
    let mut allow_models = None;
    let mut budget_occ = None;
    let mut rate = None;

    for child in node.children() {
        match child.name.as_str() {
            "allow_models" => {
                if child.args.is_empty() {
                    // Never fall through to `None`: that means every model, and a
                    // malformed allowlist must not widen access.
                    ctx.err(child, ErrorKind::ArgCount, "`allow_models` needs a model id");
                    continue;
                }
                allow_models = Some(child.args.clone());
            }
            "budget" => {
                if !ctx.args(child, 2) {
                    continue;
                }
                let amount = child.args.first().and_then(|v| v.parse::<u64>().ok());
                match (amount, child.args.get(1).map(String::as_str)) {
                    (Some(amount), Some("OCC")) => budget_occ = Some(amount),
                    _ => ctx.err(child, ErrorKind::BadValue, "`budget` must look like `500 OCC`"),
                }
            }
            "rate" => {
                if !ctx.args(child, 1) {
                    continue;
                }
                let value = child.args.first().map_or("", String::as_str);
                match parse_rate(value) {
                    Some(parsed) => rate = Some(parsed),
                    None => ctx.err(child, ErrorKind::BadValue, "`rate` must look like `60/min`"),
                }
            }
            _ => {}
        }
    }

    Some(TokenCfg {
        name,
        allow_models,
        budget_occ,
        rate,
    })
}

fn parse_share(ctx: &mut Ctx, node: &Node) -> Option<ShareCfg> {
    ctx.args(node, 1);
    ctx.block(node);
    ctx.only(node, &["enroll", "expires", "grant", "max_nodes"]);

    let name = node.args.first()?.clone();
    let mut enroll = Enroll::Node;
    let mut expires_ms = None;
    let mut grant_occ = None;
    let mut max_nodes = None;

    for child in node.children() {
        if child.name == "grant" {
            if !ctx.args(child, 2) {
                continue;
            }
            let amount = child.args.first().and_then(|v| v.parse::<u64>().ok());
            match (amount, child.args.get(1).map(String::as_str)) {
                (Some(amount), Some("OCC")) => grant_occ = Some(amount),
                _ => ctx.err(child, ErrorKind::BadValue, "`grant` must look like `100 OCC`"),
            }
            continue;
        }
        if !ctx.args(child, 1) {
            continue;
        }
        let value = child.args.first().map_or("", String::as_str);
        match child.name.as_str() {
            "enroll" => match value {
                "node" => enroll = Enroll::Node,
                "request" => enroll = Enroll::Request,
                _ => ctx.err(child, ErrorKind::BadValue, "`enroll` must be `node` or `request`"),
            },
            "expires" => match parse_duration(value) {
                Some(ms) if ms > 0 => expires_ms = Some(ms),
                Some(_) => ctx.err(child, ErrorKind::BadValue, "`expires` must be longer than 0s"),
                None => ctx.err(child, ErrorKind::BadValue, "`expires` must look like `24h`"),
            },
            "max_nodes" => match value.parse::<u32>() {
                Ok(n) if n > 0 => max_nodes = Some(n),
                Ok(_) => ctx.err(child, ErrorKind::BadValue, "`max_nodes` must be at least 1"),
                Err(_) => ctx.err(child, ErrorKind::BadValue, "`max_nodes` must be a number"),
            },
            _ => {}
        }
    }

    Some(ShareCfg {
        name,
        enroll,
        expires_ms,
        grant_occ,
        max_nodes,
    })
}

fn parse_pool(ctx: &mut Ctx, node: &Node) -> Option<PoolCfg> {
    ctx.args(node, 1);
    ctx.block(node);
    ctx.only(node, &["model", "policy"]);

    let name = node.args.first()?.clone();
    let mut model = None;
    let mut policy = Policy::ExpectedMs;

    for child in node.children() {
        if !ctx.args(child, 1) {
            continue;
        }
        let value = child.args.first().map_or("", String::as_str);
        match child.name.as_str() {
            "model" => model = Some(value.to_owned()),
            "policy" => match value {
                "expected_ms" => policy = Policy::ExpectedMs,
                "fewest_active" => policy = Policy::FewestActive,
                _ => ctx.err(
                    child,
                    ErrorKind::BadValue,
                    "`policy` must be `expected_ms` or `fewest_active`",
                ),
            },
            _ => {}
        }
    }

    if let Some(model) = model {
        Some(PoolCfg { name, model, policy })
    } else {
        ctx.err(node, ErrorKind::Missing, "`pool` needs a `model` directive");
        None
    }
}

fn parse_route(ctx: &mut Ctx, node: &Node) -> Option<RouteCfg> {
    ctx.args(node, 1);
    ctx.block(node);
    ctx.only(node, &["path", "pool", "auth"]);

    let name = node.args.first()?.clone();
    let mut path = format!("/{name}");
    let mut pool = None;
    let mut auth = true;

    for child in node.children() {
        if !ctx.args(child, 1) {
            continue;
        }
        let value = child.args.first().map_or("", String::as_str);
        match child.name.as_str() {
            "path" if value.starts_with('/') => value.clone_into(&mut path),
            "path" => ctx.err(child, ErrorKind::BadValue, "`path` must start with '/'"),
            "pool" => pool = Some(value.to_owned()),
            "auth" => match value {
                "token" => auth = true,
                "none" => auth = false,
                _ => ctx.err(child, ErrorKind::BadValue, "`auth` must be `token` or `none`"),
            },
            _ => {}
        }
    }

    if let Some(pool) = pool {
        Some(RouteCfg {
            name,
            path,
            pool,
            auth,
        })
    } else {
        ctx.err(node, ErrorKind::Missing, "`route` needs a `pool` directive");
        None
    }
}

/// Validate a parsed document and build typed configuration.
///
/// # Errors
/// Returns every problem found, in file order.
pub fn validate(doc: &Document) -> Result<Config, Vec<ConfigError>> {
    let mut ctx = Ctx::default();
    let mut network: Option<NetworkCfg> = None;
    let mut tokens = BTreeMap::new();
    let mut shares = BTreeMap::new();
    let mut pools = BTreeMap::new();
    let mut routes = BTreeMap::new();
    // Cross-references are resolved after the whole file is read (declaration
    // order must not matter), but each error still has to carry the line the
    // offending route was written on.
    let mut route_refs: Vec<(String, String, String, u32, u32)> = Vec::new();

    for node in &doc.nodes {
        match node.name.as_str() {
            "network" => {
                if network.is_some() {
                    ctx.err(
                        node,
                        ErrorKind::DuplicateName,
                        "only one `network` block is allowed",
                    );
                    continue;
                }
                network = Some(parse_network(&mut ctx, node));
            }
            "token" => {
                if let Some(parsed) = parse_token(&mut ctx, node) {
                    insert_unique(&mut ctx, node, &mut tokens, parsed.name.clone(), parsed);
                }
            }
            "share" => {
                if let Some(parsed) = parse_share(&mut ctx, node) {
                    insert_unique(&mut ctx, node, &mut shares, parsed.name.clone(), parsed);
                }
            }
            "pool" => {
                if let Some(parsed) = parse_pool(&mut ctx, node) {
                    insert_unique(&mut ctx, node, &mut pools, parsed.name.clone(), parsed);
                }
            }
            "route" => {
                if let Some(parsed) = parse_route(&mut ctx, node) {
                    route_refs.push((
                        parsed.name.clone(),
                        parsed.pool.clone(),
                        node.source.clone(),
                        node.line,
                        node.column,
                    ));
                    insert_unique(&mut ctx, node, &mut routes, parsed.name.clone(), parsed);
                }
            }
            // Recognised and retained; expansion lands with P1 include resolution.
            "include" => {
                ctx.args(node, 1);
                if node.has_block() {
                    ctx.err(node, ErrorKind::BadValue, "`include` takes a path, not a block");
                }
            }
            other => {
                let detail = format!("unknown top-level directive `{other}`");
                ctx.err(node, ErrorKind::UnknownDirective, detail);
            }
        }
    }

    if network.is_none() {
        ctx.errors.push(ConfigError::new(
            ErrorKind::Missing,
            crate::DEFAULT_SOURCE,
            1,
            1,
            "a `network` block is required",
        ));
    }

    for (name, pool, source, line, column) in route_refs {
        if !pools.contains_key(&pool) {
            let detail = format!("route `{name}` references undefined pool `{pool}`");
            ctx.errors.push(ConfigError::new(
                ErrorKind::UndefinedRef,
                source,
                line,
                column,
                detail,
            ));
        }
    }

    if ctx.errors.is_empty() {
        if let Some(network) = network {
            return Ok(Config {
                network,
                tokens,
                shares,
                pools,
                routes,
            });
        }
    }
    Err(ctx.errors)
}
