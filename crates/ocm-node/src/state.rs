//! `<state-dir>/node.json`, byte-compatible with the Python agent's state file.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[derive(Debug, Default, Clone, PartialEq, Serialize, Deserialize)]
pub struct State {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub node_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub credential: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coordinator: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub approved_models: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    /// Keys written by other agent versions survive a round-trip.
    #[serde(flatten)]
    pub extra: Map<String, Value>,
}

impl State {
    pub fn path(state_dir: &Path) -> PathBuf {
        state_dir.join("node.json")
    }

    pub fn load(path: &Path) -> Result<State> {
        match fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text).with_context(|| format!("corrupt state file {}", path.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(State::default()),
            Err(e) => Err(e).with_context(|| format!("cannot read {}", path.display())),
        }
    }

    /// Atomic write: tmp file (0600) then rename; parent dir created 0700.
    pub fn save(&self, path: &Path) -> Result<()> {
        let dir = path.parent().context("state path has no parent")?;
        create_private_dir(dir)?;
        let tmp = path.with_extension("tmp");
        let body = serde_json::to_string_pretty(self)?;
        {
            let mut f = open_private(&tmp)?;
            f.write_all(body.as_bytes())?;
            f.sync_all()?;
        }
        fs::rename(&tmp, path).with_context(|| format!("cannot write {}", path.display()))
    }
}

#[cfg(unix)]
fn create_private_dir(dir: &Path) -> Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    if !dir.exists() {
        fs::DirBuilder::new().recursive(true).mode(0o700).create(dir)?;
    }
    Ok(())
}

#[cfg(not(unix))]
fn create_private_dir(dir: &Path) -> Result<()> {
    fs::create_dir_all(dir)?;
    Ok(())
}

#[cfg(unix)]
fn open_private(path: &Path) -> Result<fs::File> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    let f = fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(path)?;
    f.set_permissions(fs::Permissions::from_mode(0o600))?; // tmp may pre-exist with wider bits
    Ok(f)
}

#[cfg(not(unix))]
fn open_private(path: &Path) -> Result<fs::File> {
    Ok(fs::File::create(path)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_python_compat() {
        let dir = std::env::temp_dir().join(format!("ocm-node-test-{}", std::process::id()));
        let path = State::path(&dir.join("nested"));
        let mut s = State {
            node_id: Some("n1".into()),
            credential: Some("ocmn_x".into()),
            coordinator: Some("http://localhost:8031".into()),
            approved_models: vec!["qwen2.5:1.5b".into()],
            display_name: Some("box".into()),
            extra: Map::new(),
        };
        s.extra.insert("future_key".into(), Value::from(7));
        s.save(&path).unwrap();
        assert_eq!(State::load(&path).unwrap(), s);

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
            assert_eq!(fs::metadata(path.parent().unwrap()).unwrap().permissions().mode() & 0o777, 0o700);
        }

        // A file as the Python agent writes it after a revoke (credential/node_id popped).
        fs::write(&path, r#"{"approved_models": ["a"], "coordinator": "http://c"}"#).unwrap();
        let p = State::load(&path).unwrap();
        assert_eq!(p.approved_models, vec!["a".to_string()]);
        assert!(p.credential.is_none());
        assert!(State::load(&dir.join("missing.json")).unwrap() == State::default());
        let _ = fs::remove_dir_all(&dir);
    }
}
