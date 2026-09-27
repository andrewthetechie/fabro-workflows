//! The `serve` subcommand (C5): the rmcp streamable-HTTP MCP server on port 7391.
//!
//! Task-02 spike: a single `whoami` tool that reports the current `stage.json`, the
//! server pid, its start time and its own `FABRO_IO_MANIFEST` env state. `serve`
//! reads `stage.json` on every request and stays stateless (ADR 0016 D3).

use std::net::SocketAddr;
use std::process::ExitCode;

use rmcp::handler::server::ServerHandler;
use rmcp::model::{
    CallToolRequestParams, CallToolResult, Content, ListToolsResult, PaginatedRequestParams,
    ServerCapabilities, ServerInfo, Tool,
};
use rmcp::service::{MaybeSendFuture, RequestContext};
use rmcp::transport::streamable_http_server::session::local::LocalSessionManager;
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService,
};
use rmcp::{ErrorData, RoleServer};

use crate::common;

const DEFAULT_PORT: u16 = 7391;
const WHOAMI: &str = "whoami";

#[derive(Clone)]
struct IoHandler {
    pid: u32,
    started: String,
}

impl IoHandler {
    fn new() -> Self {
        Self {
            pid: std::process::id(),
            started: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
        }
    }

    fn whoami_tool() -> Tool {
        let schema: serde_json::Map<String, serde_json::Value> =
            serde_json::from_value(serde_json::json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false
            }))
            .expect("static whoami schema is valid JSON");
        Tool::new(
            WHOAMI,
            "Report the running fabro-io server: the current /tmp/fabro/.io/stage.json, \
             the server pid, its start time, and whether FABRO_IO_MANIFEST is set in its \
             own environment.",
            schema,
        )
    }

    fn log(&self, note: &str) {
        let node = std::env::var("FABRO_NODE_ID").unwrap_or_else(|_| "-".to_string());
        common::spike_log(&node, note);
    }
}

impl ServerHandler for IoHandler {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().build())
    }

    fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> impl Future<Output = Result<ListToolsResult, ErrorData>> + MaybeSendFuture + '_ {
        async move {
            Ok(ListToolsResult {
                tools: vec![IoHandler::whoami_tool()],
                ..Default::default()
            })
        }
    }

    fn get_tool(&self, name: &str) -> Option<Tool> {
        if name == WHOAMI {
            Some(IoHandler::whoami_tool())
        } else {
            None
        }
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        if request.name.as_ref() != WHOAMI {
            return Ok(CallToolResult::error(vec![Content::text(format!(
                "unknown tool '{}'",
                request.name
            ))]));
        }
        self.log("whoami");
        let stage_path = common::io_dir().join("stage.json");
        let stage = std::fs::read_to_string(&stage_path)
            .unwrap_or_else(|_| "<no stage.json yet>".to_string());
        let manifest = std::env::var("FABRO_IO_MANIFEST");
        let manifest_state = match &manifest {
            Ok(v) => format!("yes ({} bytes)", v.len()),
            Err(_) => "no".to_string(),
        };
        let body = format!(
            "stage.json ({}):\n{stage}\n\npid: {}\nstarted: {}\nFABRO_IO_MANIFEST in server env: {}\n",
            stage_path.display(),
            self.pid,
            self.started,
            manifest_state
        );
        Ok(CallToolResult::success(vec![Content::text(body)]))
    }
}

/// Parse `--port N` from the remaining args; default 7391.
fn parse_port(args: &[String]) -> Result<u16, String> {
    let mut port: Option<u16> = None;
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--port" && i + 1 < args.len() {
            port = Some(
                args[i + 1]
                    .parse::<u16>()
                    .map_err(|_| format!("invalid --port '{}'", args[i + 1]))?,
            );
            i += 2;
        } else if let Ok(p) = args[i].parse::<u16>() {
            port = Some(p);
            i += 1;
        } else {
            return Err(format!("unexpected argument '{}'", args[i]));
        }
    }
    Ok(port.unwrap_or(DEFAULT_PORT))
}

pub fn run(args: &[String]) -> ExitCode {
    let port = match parse_port(args) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("serve: {e}");
            return ExitCode::from(64);
        }
    };
    let rt = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("serve: could not start tokio runtime: {e}");
            return ExitCode::FAILURE;
        }
    };
    match rt.block_on(serve_async(port)) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("serve: {e}");
            ExitCode::FAILURE
        }
    }
}

async fn serve_async(port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let handler = IoHandler::new();
    let session_manager = std::sync::Arc::new(LocalSessionManager::default());
    let service = StreamableHttpService::new(
        move || Ok(handler.clone()),
        session_manager,
        StreamableHttpServerConfig::default(),
    );
    let app = axum::Router::new().fallback_service(service);
    let addr: SocketAddr = format!("127.0.0.1:{port}").parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    eprintln!("fabro-io serve: listening on http://127.0.0.1:{port}/mcp");
    axum::serve(listener, app).await?;
    Ok(())
}
