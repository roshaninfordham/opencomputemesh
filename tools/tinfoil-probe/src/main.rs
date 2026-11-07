//! Verify a Tinfoil enclave and print the measurement as JSON.
//!
//! This is the reproducible check behind the confidential-tier claim: the SDK
//! fetches the expected measurements for a pinned repository, fetches the live
//! attestation report, and refuses to continue if they disagree. Success is
//! evidence; a mismatch means nothing should be sent to that endpoint.

use tinfoil::SecureClient;

const ROUTER_HOST: &str = "inference.tinfoil.sh";
const ROUTER_REPO: &str = "tinfoilsh/confidential-model-router";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let repo = std::env::var("TINFOIL_ROUTER_REPO").unwrap_or_else(|_| ROUTER_REPO.to_owned());
    let key =
        std::env::var("TINFOIL_API_KEY").map_err(|_| "TINFOIL_API_KEY is not set; source your .env first")?;

    let mut client = SecureClient::new(ROUTER_HOST, repo.as_str(), key);
    let ground_truth = client.verify().await?;

    println!(
        "{}",
        serde_json::json!({
            "host": client.host(),
            "repo": repo,
            "verified": true,
            "enclave_fingerprint": ground_truth.enclave_fingerprint.to_string(),
            "code_fingerprint": ground_truth.code_fingerprint.to_string(),
        })
    );
    Ok(())
}
