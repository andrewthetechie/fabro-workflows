//! Test 13: the MCP server lists the right tools per stage and `submit` writes the file.
//!
//! Spawns the built binary and talks streamable-HTTP MCP to it. The server is stateless
//! and reads `stage.json` on every request, so each stage.json change reshapes the next
//! tools/list without a restart.

mod common;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::process::{Command, Stdio};

const PORT: u16 = 17391;
const BASE: &str = "/";

fn combined_manifest(root: &std::path::Path) -> String {
    format!(
        r#"{{"version":1,"min_binary":"0.1.0","stages":{{
          "mystage": {{
            "inputs":[
              {{"name":"issues","path":"{0}/issues.json","required":true,"about":"the issues"}},
              {{"name":"big","path":"{0}/big.txt","required":true,"about":"a diff"}}
            ],
            "output":{{"path":"{0}/out.json","schema":{{
              "type":"object","properties":{{"summary":{{"type":"string"}}}},
              "required":["summary"],"additionalProperties":false
            }}}}
          }},
          "reader": {{"inputs":[
            {{"name":"data","path":"{0}/data.txt","required":true,"about":"the data"}}
          ]}}
        }}}}"#,
        root.display()
    )
}

fn http_json(
    session: Option<&str>,
    obj: &serde_json::Value,
) -> (serde_json::Value, Option<String>) {
    let body = serde_json::to_string(obj).unwrap();
    let mut stream = TcpStream::connect(("127.0.0.1", PORT)).unwrap();
    let mut req = format!(
        "POST {BASE} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: {}\r\n",
        body.len()
    );
    if let Some(s) = session {
        req.push_str(&format!("MCP-Session-Id: {s}\r\n"));
    }
    req.push_str("\r\n");
    req.push_str(&body);
    stream.write_all(req.as_bytes()).unwrap();
    stream.set_read_timeout(Some(std::time::Duration::from_secs(5))).unwrap();

    // Read the status line and headers.
    let status = read_line(&mut stream);
    debug_assert!(status.starts_with("HTTP/"), "status: {status}");
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    let mut new_session: Option<String> = None;
    loop {
        let line = read_line(&mut stream);
        if line.is_empty() {
            break;
        }
        let lower = line.to_ascii_lowercase();
        if let Some(v) = lower.strip_prefix("content-length:") {
            content_length = v.trim().parse().ok();
        }
        if lower.contains("chunked") {
            chunked = true;
        }
        if lower.starts_with("mcp-session-id:") {
            new_session = Some(line.split(':').nth(1).unwrap_or("").trim().to_string());
        }
    }

    // Body: chunked, content-length, or none.
    let content: Vec<u8> = if chunked {
        let mut out = Vec::new();
        loop {
            let size_line = read_line(&mut stream);
            let size = usize::from_str_radix(size_line.trim(), 16).unwrap_or(0);
            if size == 0 {
                let _ = read_line(&mut stream); // trailing CRLF
                break;
            }
            let mut buf = vec![0u8; size];
            stream.read_exact(&mut buf).unwrap();
            out.extend(buf);
            let _ = read_line(&mut stream); // CRLF after chunk
        }
        out
    } else if let Some(n) = content_length {
        let mut buf = vec![0u8; n];
        if n > 0 {
            stream.read_exact(&mut buf).unwrap();
        }
        buf
    } else {
        Vec::new()
    };

    let text = String::from_utf8_lossy(&content).into_owned();
    (extract_json(&text), new_session)
}

/// Read one CRLF-terminated line (without the CRLF).
fn read_line(stream: &mut TcpStream) -> String {
    let mut out = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        match stream.read_exact(&mut byte) {
            Ok(_) => {
                if byte[0] == b'\n' {
                    break;
                }
                if byte[0] != b'\r' {
                    out.push(byte[0]);
                }
            }
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Extract the first balanced JSON object from a body that may be an SSE stream.
fn extract_json(text: &str) -> serde_json::Value {
    let start = text.find('{').unwrap_or(text.len());
    if start == text.len() {
        return serde_json::Value::Null;
    }
    let mut depth = 0usize;
    let mut in_str = false;
    for (i, c) in text[start..].char_indices() {
        match c {
            '{' if !in_str => depth += 1,
            '}' if !in_str => {
                depth -= 1;
                if depth == 0 {
                    let end = start + i + c.len_utf8();
                    if let Ok(v) = serde_json::from_str(&text[start..end]) {
                        return v;
                    }
                    return serde_json::Value::Null;
                }
            }
            '"' if !in_str => in_str = true,
            '"' if in_str && !text[start..start + i].ends_with('\\') => in_str = false,
            _ => {}
        }
    }
    serde_json::Value::Null
}

fn rpc(session: &str, id: u64, method: &str, params: serde_json::Value) -> serde_json::Value {
    http_json(
        Some(session),
        &serde_json::json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}),
    )
    .0
}

fn start_session() -> String {
    let (init, sess) = http_json(
        None,
        &serde_json::json!({"jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26","capabilities":{},
                      "clientInfo":{"name":"t","version":"1"}}}),
    );
    assert!(init.get("result").is_some(), "initialize succeeds: {init}");
    let session = sess.expect("a session id");
    let _ = rpc(&session, 99, "notifications/initialized", serde_json::json!({}));
    session
}

fn tool_names(v: &serde_json::Value) -> Vec<String> {
    v["result"]["tools"]
        .as_array()
        .map(|a| a.iter().map(|t| t["name"].as_str().unwrap_or("").to_string()).collect())
        .unwrap_or_default()
}

#[test]
fn test_13_serve_tools_and_submit() {
    let root = common::temp_root("serve");
    let m = combined_manifest(&root);
    let mut child = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
        .args(["serve", "--port", &PORT.to_string()])
        .env("FABRO_IO_ROOT", &root)
        .env("FABRO_IO_MANIFEST", &m)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    for _ in 0..100 {
        if TcpStream::connect(("127.0.0.1", PORT)).is_ok() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }

    common::with_manifest(&root, &m, "", || {
        let visit = "f".repeat(32);

        // Stage with an output -> inputs + submit.
        common::write_stage(&root, "mystage", &visit);
        let session = start_session();
        let list = rpc(&session, 2, "tools/list", serde_json::json!({}));
        let names = tool_names(&list);
        assert!(names.iter().any(|n| n == "inputs"));
        assert!(names.iter().any(|n| n == "submit"), "submit listed for an output stage");
        for tool in ["code_def", "code_show", "code_search", "code_callers", "code_callees", "code_impact", "code_tests", "restore_file", "baseline_check"] {
            assert!(names.iter().any(|n| n == tool), "{tool} listed for a known stage");
        }

        // Reads-only stage -> inputs only (same server, new stage.json).
        common::write_stage(&root, "reader", &visit);
        let list = rpc(&session, 2, "tools/list", serde_json::json!({}));
        let names = tool_names(&list);
        assert!(names.iter().any(|n| n == "inputs"));
        assert!(!names.iter().any(|n| n == "submit"), "no submit for a reads-only stage");
        assert!(names.iter().any(|n| n == "code_show"), "code tools for a reads-only stage");

        // A code tool call with a bad argument comes back as a tool error, not a
        // protocol error, so the model can act on it.
        let call = rpc(&session, 5, "tools/call", serde_json::json!({"name":"code_def","arguments":{}}));
        assert_eq!(call["result"]["isError"], serde_json::json!(true), "code_def error: {call}");
        let text = call["result"]["content"][0]["text"].as_str().unwrap_or("");
        assert!(text.contains("non-empty `name`"), "code_def error text: {text}");

        // Unknown node -> no tools.
        common::write_stage(&root, "ghost", &visit);
        let list = rpc(&session, 2, "tools/list", serde_json::json!({}));
        assert!(tool_names(&list).is_empty(), "no tools for an unknown node");

        // submit through the client writes the same file as the CLI.
        common::write_stage(&root, "mystage", &visit);
        std::fs::write(root.join("issues.json"), r#"{"issue":1205}"#).unwrap();
        std::fs::write(root.join("big.txt"), "small\n").unwrap();
        // serve inputs via MCP (batch -> small files are complete).
        let _ = rpc(&session, 3, "tools/call", serde_json::json!({"name":"inputs","arguments":{}}));
        let submit_obj = serde_json::json!({"summary":"fixed"});
        let call = rpc(
            &session,
            4,
            "tools/call",
            serde_json::json!({"name":"submit","arguments":submit_obj.clone()}),
        );
        let text = call["result"]["content"][0]["text"].as_str().unwrap_or("");
        assert!(text.starts_with("written:"), "submit via client writes: {text}");
        let via_mcp = std::fs::read(root.join("out.json")).unwrap();

        // Same file via the CLI binary.
        std::fs::remove_file(root.join("out.json")).unwrap();
        let tmp = root.join("tmp.json");
        std::fs::write(&tmp, serde_json::to_string(&submit_obj).unwrap()).unwrap();
        let cli = Command::new(env!("CARGO_BIN_EXE_fabro-io"))
            .args(["submit", "--file", tmp.to_str().unwrap()])
            .env("FABRO_IO_ROOT", &root)
            .env("FABRO_IO_MANIFEST", &m)
            .output()
            .unwrap();
        assert!(cli.status.success(), "CLI submit exit: {}", cli.status);
        let via_cli = std::fs::read(root.join("out.json")).unwrap();
        assert_eq!(via_mcp, via_cli, "MCP and CLI submit write the same file");
    });

    child.kill().unwrap();
    child.wait().unwrap();
    let _ = std::fs::remove_dir_all(&root);
}
