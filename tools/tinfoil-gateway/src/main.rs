//! P1 of the confidential tier: a standalone attested gateway.
//!
//! Verifies one Tinfoil enclave at startup, then forwards OpenAI-compatible
//! traffic to it with the server-side key attached. Request bodies are forwarded
//! as opaque bytes: the gateway never parses, inspects or decrypts them, so with
//! an EHBP client the plaintext exists only inside the enclave.
//!
//! Fail closed: if verification fails, nothing is served.

use std::{net::SocketAddr, sync::Arc};

use axum::{
    Router,
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, Path, State},
    http::{HeaderMap, HeaderName, HeaderValue, StatusCode, Uri, header},
    response::{IntoResponse, Response},
    routing::{any, get},
};
use tinfoil::SecureClient;

const DEFAULT_HOST: &str = "inference.tinfoil.sh";
const DEFAULT_REPO: &str = "tinfoilsh/confidential-model-router";
const MAX_BODY: usize = 8 * 1024 * 1024;

/// Headers a client may set that survive the hop. Anything else is dropped,
/// including its `Authorization`, which is replaced by the server-side key.
const FORWARDED_REQUEST_HEADERS: [&str; 4] = [
    "content-type",
    "accept",
    "ehbp-encapsulated-key",
    "x-tinfoil-model",
];

/// Headers the enclave needs on the way back: the body content type, the EHBP
/// nonce the client derives its response key from, and billed usage.
const FORWARDED_RESPONSE_HEADERS: [&str; 3] =
    ["content-type", "ehbp-response-nonce", "x-tinfoil-usage-metrics"];

struct App {
    pinned_host: String,
    repo: String,
    base_url: String,
    auth: HeaderValue,
    /// Verified at startup; holds the attestation-pinned client.
    secure: SecureClient,
    enclave_fingerprint: String,
}

/// The host of an `https` URL, or `None` if the URL is unusable as an origin.
///
/// Deliberately strict: a plaintext scheme or embedded credentials is a refusal,
/// not something to normalize.
fn host_of(raw: &str) -> Option<String> {
    let uri: Uri = raw.parse().ok()?;
    if uri.scheme_str() != Some("https") {
        return None;
    }
    let authority = uri.authority()?;
    if authority.as_str().contains('@') {
        return None;
    }
    uri.host().map(str::to_ascii_lowercase)
}

/// Build upstream headers from an inbound request.
///
/// A client credential is never forwarded: the gateway authenticates to the
/// enclave with its own key, so a caller can neither borrow nor spoof it.
fn upstream_headers(inbound: &HeaderMap, auth: &HeaderValue) -> HeaderMap {
    let mut out = HeaderMap::new();
    for name in FORWARDED_REQUEST_HEADERS {
        if let Some(value) = inbound.get(name) {
            out.insert(HeaderName::from_static(name), value.clone());
        }
    }
    out.insert(header::AUTHORIZATION, auth.clone());
    out.insert(
        HeaderName::from_static("x-tinfoil-request-usage-metrics"),
        HeaderValue::from_static("true"),
    );
    out
}

fn error(status: StatusCode, detail: &str) -> Response {
    let body = serde_json::json!({ "error": { "message": detail } }).to_string();
    (
        status,
        [(header::CONTENT_TYPE, HeaderValue::from_static("application/json"))],
        body,
    )
        .into_response()
}

async fn health(State(app): State<Arc<App>>) -> Response {
    let body = serde_json::json!({
        "verified": true,
        "pinned_host": app.pinned_host,
        "repo": app.repo,
        "enclave_fingerprint": app.enclave_fingerprint,
    })
    .to_string();
    (
        [(header::CONTENT_TYPE, HeaderValue::from_static("application/json"))],
        body,
    )
        .into_response()
}

async fn forward(
    State(app): State<Arc<App>>,
    method: axum::http::Method,
    Path(rest): Path<String>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Some(raw) = headers.get("x-tinfoil-enclave-url") {
        let pinned = host_of(raw.to_str().unwrap_or_default()).as_deref() == Some(app.pinned_host.as_str());
        if !pinned {
            return error(
                StatusCode::BAD_REQUEST,
                "X-Tinfoil-Enclave-Url is not the pinned enclave",
            );
        }
    }

    let url = format!("{}/v1/{rest}", app.base_url);
    let Ok(method) = reqwest::Method::from_bytes(method.as_str().as_bytes()) else {
        return error(StatusCode::BAD_REQUEST, "unsupported method");
    };
    let Ok(http) = app.secure.http_client() else {
        return error(StatusCode::BAD_GATEWAY, "gateway is not verified");
    };

    let request = http
        .request(method, url)
        .headers(upstream_headers(&headers, &app.auth));
    let request = if body.is_empty() {
        request
    } else {
        request.body(body.to_vec())
    };

    match request.send().await {
        Ok(upstream) => {
            let status = StatusCode::from_u16(upstream.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
            let mut out = HeaderMap::new();
            for name in FORWARDED_RESPONSE_HEADERS {
                if let Some(value) = upstream.headers().get(name) {
                    out.insert(HeaderName::from_static(name), value.clone());
                }
            }
            let billed = upstream
                .headers()
                .get("x-tinfoil-usage-metrics")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("-");
            eprintln!("proxy /v1/{rest} -> {status} usage={billed}");
            (status, out, Body::from_stream(upstream.bytes_stream())).into_response()
        }
        Err(e) => error(StatusCode::BAD_GATEWAY, &format!("upstream request failed: {e}")),
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let host = std::env::var("TINFOIL_UPSTREAM").unwrap_or_else(|_| DEFAULT_HOST.to_owned());
    let repo = std::env::var("TINFOIL_ROUTER_REPO").unwrap_or_else(|_| DEFAULT_REPO.to_owned());
    let key =
        std::env::var("TINFOIL_API_KEY").map_err(|_| "TINFOIL_API_KEY is not set; source your .env first")?;
    let auth = HeaderValue::from_str(&format!("Bearer {key}"))
        .map_err(|_| "TINFOIL_API_KEY is not valid in an HTTP header")?;
    let bind: SocketAddr = std::env::var("GATEWAY_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8787".to_owned())
        .parse()?;

    // Fail closed: an unverifiable enclave means no gateway at all.
    let mut secure = SecureClient::new(host.as_str(), repo.as_str(), key);
    let ground_truth = secure.verify().await?;
    secure.http_client()?;
    let enclave_fingerprint = ground_truth.enclave_fingerprint.to_string();
    eprintln!("verified {host} against {repo}");
    eprintln!("enclave fingerprint {enclave_fingerprint}");

    let app = Arc::new(App {
        pinned_host: host.clone(),
        repo,
        base_url: format!("https://{host}"),
        auth,
        secure,
        enclave_fingerprint,
    });

    let router = Router::new()
        .route("/healthz", get(health))
        .route("/v1/{*rest}", any(forward))
        .with_state(app)
        .layer(DefaultBodyLimit::max(MAX_BODY));

    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!("listening on {bind}");
    axum::serve(listener, router).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pins_exactly_one_origin() {
        assert_eq!(
            host_of("https://inference.tinfoil.sh").as_deref(),
            Some("inference.tinfoil.sh")
        );
        assert_eq!(
            host_of("https://inference.tinfoil.sh/v1/chat/completions").as_deref(),
            Some("inference.tinfoil.sh")
        );
        // A lookalike is a different origin, not a suffix match.
        assert_ne!(
            host_of("https://evil-inference.tinfoil.sh").as_deref(),
            Some("inference.tinfoil.sh")
        );
        assert_eq!(host_of("http://inference.tinfoil.sh"), None);
        assert_eq!(host_of("https://user:pw@inference.tinfoil.sh"), None);
        assert_eq!(host_of("not a url"), None);
    }

    #[test]
    fn the_client_credential_never_reaches_upstream() {
        let auth = HeaderValue::from_static("Bearer server-side-key");
        let mut inbound = HeaderMap::new();
        inbound.insert(
            header::AUTHORIZATION,
            HeaderValue::from_static("Bearer client-key"),
        );
        inbound.insert(
            HeaderName::from_static("ehbp-encapsulated-key"),
            HeaderValue::from_static("abc"),
        );
        inbound.insert(
            HeaderName::from_static("x-tinfoil-model"),
            HeaderValue::from_static("gpt-oss-120b"),
        );
        inbound.insert(
            HeaderName::from_static("connection"),
            HeaderValue::from_static("keep-alive"),
        );
        inbound.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/json"));

        let out = upstream_headers(&inbound, &auth);
        let get = |name: &str| out.get(name).unwrap().to_str().unwrap().to_owned();

        assert_eq!(get("authorization"), "Bearer server-side-key");
        assert_eq!(get("ehbp-encapsulated-key"), "abc");
        assert_eq!(get("x-tinfoil-model"), "gpt-oss-120b");
        assert_eq!(get("x-tinfoil-request-usage-metrics"), "true");
        assert_eq!(get("content-type"), "application/json");
        assert!(out.get("connection").is_none(), "hop-by-hop header forwarded");
    }
}
