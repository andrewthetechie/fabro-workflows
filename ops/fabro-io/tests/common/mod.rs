//! Shared helpers for the integration tests.
//!
//! Each test runs in an isolated `FABRO_IO_ROOT` and sets `FABRO_IO_MANIFEST`. A global
//! mutex serializes them because the manifest and root are process-global environment
//! variables.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub static LOCK: Mutex<()> = Mutex::new(());

/// A fresh temporary root. Caller removes it.
pub fn temp_root(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "fabro-io-test-{tag}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(dir.join(".io")).unwrap();
    dir
}

fn set(key: &str, value: &str) {
    unsafe {
        std::env::set_var(key, value);
    }
}

fn unset(key: &str) {
    unsafe {
        std::env::remove_var(key);
    }
}

/// A manifest built around `root`, with three inputs and one output (a refute-like stage).
/// Inputs in order: `issues` (required), `opt` (optional), `big` (required, large).
pub fn manifest(root: &Path) -> String {
    format!(
        r#"{{"version":1,"min_binary":"0.1.0","stages":{{
          "mystage": {{
            "inputs": [
              {{"name":"issues","path":"{0}/issues.json","required":true,"about":"the issues"}},
              {{"name":"opt","path":"{0}/opt.txt","required":false,"about":"","absent":"improve did not write opt"}},
              {{"name":"big","path":"{0}/big.txt","required":true,"about":"a big diff"}}
            ],
            "output": {{"path":"{0}/out.json","schema":{{
              "type":"object",
              "properties":{{
                "summary":{{"type":"string"}},
                "criteria":{{
                  "type":"array",
                  "items":{{"type":"object","properties":{{"criterion":{{"type":"string"}},"status":{{"enum":["met","not_met","unverifiable"]}}}},"required":["criterion","status"],"additionalProperties":false}}
                }}
              }},
              "required":["summary","criteria"],
              "additionalProperties":false
            }}}},
            "sealed":[ "{0}/standards.json", "{0}/feedback/**" ]
          }}
        }}}}"#,
        root.display()
    )
}

/// Manifest with a single required input and no output (reads-only stage).
pub fn manifest_readonly(root: &Path) -> String {
    format!(
        r#"{{"version":1,"min_binary":"0.1.0","stages":{{
          "reader": {{"inputs":[
            {{"name":"data","path":"{0}/data.txt","required":true,"about":"the data"}}
          ]}}
        }}}}"#,
        root.display()
    )
}

/// Manifest with a fixed min_binary.
pub fn manifest_min_binary(root: &Path, min: &str) -> String {
    let m = manifest(root);
    m.replace("\"min_binary\":\"0.1.0\"", &format!("\"min_binary\":\"{min}\""))
}

/// Run `f` with the environment (root + manifest + node) set; restores afterwards.
pub fn with_manifest<F: FnOnce()>(root: &Path, manifest: &str, node: &str, f: F) {
    let _g = LOCK.lock().unwrap();
    set("FABRO_IO_ROOT", &root.display().to_string());
    set("FABRO_IO_MANIFEST", manifest);
    if !node.is_empty() {
        set("FABRO_NODE_ID", node);
    } else {
        unset("FABRO_NODE_ID");
    }
    set("FABRO_WORKFLOW", "wf");
    f();
    unset("FABRO_IO_ROOT");
    unset("FABRO_IO_MANIFEST");
    unset("FABRO_NODE_ID");
    unset("FABRO_WORKFLOW");
}

/// Write a fixed stage.json with the given node and visit.
pub fn write_stage(root: &Path, node: &str, visit: &str) {
    let json = format!(
        r#"{{"workflow":"wf","node":"{node}","visit":"{visit}","started":"2026-09-27T00:00:00Z"}}"#
    );
    std::fs::write(root.join(".io/stage.json"), json).unwrap();
}

pub fn visit_of(root: &Path) -> String {
    let raw = std::fs::read_to_string(root.join(".io/stage.json")).unwrap();
    let v: serde_json::Value = serde_json::from_str(&raw).unwrap();
    v["visit"].as_str().unwrap().to_string()
}

/// While alive, `PATH` names one directory holding only `sh`, `sleep` and `cat`, so no `kill`
/// binary resolves: the shape of `fabro-python-node:local`, which ships none. Restores `PATH`
/// on drop. Callers hold `LOCK`.
pub struct NoKillPath {
    old: Option<std::ffi::OsString>,
    dir: PathBuf,
}

impl NoKillPath {
    /// Points `PATH` at a fresh directory of symlinks named after `tag`.
    pub fn new(tag: &str) -> Self {
        let old = std::env::var_os("PATH");
        let dir = temp_root(&format!("nokill-{tag}"));
        for tool in ["sh", "sleep", "cat"] {
            let found = std::env::split_paths(old.as_deref().unwrap_or_default())
                .map(|d| d.join(tool))
                .find(|p| p.is_file())
                .unwrap_or_else(|| panic!("no {tool} on PATH"));
            std::os::unix::fs::symlink(found, dir.join(tool)).unwrap();
        }
        // SAFETY: the environment is process-global; callers hold `LOCK`, which every test
        // that reads or writes it takes, so no other thread touches it meanwhile.
        unsafe {
            std::env::set_var("PATH", &dir);
        }
        NoKillPath { old, dir }
    }
}

impl Drop for NoKillPath {
    fn drop(&mut self) {
        // SAFETY: as in `new`: the guard is dropped while its test still holds `LOCK`.
        unsafe {
            match &self.old {
                Some(p) => std::env::set_var("PATH", p),
                None => std::env::remove_var("PATH"),
            }
        }
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

/// True once no process `pid` exists, polled for up to 3 s (an orphan is reaped by init).
pub fn process_gone(pid: &str) -> bool {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
    while std::time::Instant::now() < deadline {
        let alive = std::process::Command::new("/bin/sh")
            .args(["-c", &format!("kill -0 {pid} 2>/dev/null")])
            .status()
            .is_ok_and(|s| s.success());
        if !alive {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    false
}

/// The pid a test step wrote to `path`, trimmed.
pub fn read_pid(path: &Path) -> String {
    std::fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("{}: {e}", path.display()))
        .trim()
        .to_string()
}
