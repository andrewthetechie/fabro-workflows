//! The `serve` subcommand (C5): the rmcp streamable-HTTP MCP server on port 7391.
//!
//! `list_tools`, `get_tool` and `call_tool` each read `stage.json` and the manifest
//! again, so the server stays stateless (ADR 0016 D3): each stage sees only its own
//! tools, and a fresh stage.json on a later request changes what is served. A stage
//! problem is returned as an `is_error` tool result (never a JSON-RPC error), so the
//! model sees and can act on it; a protocol error would end the session.

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

use crate::{common, inputs, manifest, submit};

const DEFAULT_PORT: u16 = 7391;
const INPUTS: &str = "inputs";
const SUBMIT: &str = "submit";

#[derive(Clone)]
struct IoHandler;

impl IoHandler {
    fn inputs_tool() -> Tool {
        let schema: serde_json::Map<String, serde_json::Value> =
            serde_json::from_value(serde_json::json!({
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The input name. Omit to read every input of the stage in order."},
                    "part": {"type": "integer", "minimum": 1, "description": "Return only this page of one input (raw bytes). Requires name."}
                },
                "additionalProperties": false
            }))
            .expect("static inputs schema is valid JSON");
        Tool::new(
            INPUTS,
            "Read the stage's inputs. With no arguments, returns every input in manifest \
             order within one page budget. An input that does not fit prints only its \
             header; the result then ends with one MORE: line for every page not yet \
             shown, naming the exact call that returns it. With name only, returns that \
             input the same way. With name and part, returns that page's raw bytes. An \
             absent optional input prints its (absent: ...) sentence. A required input \
             that is missing is listed as MISSING and the result is an error. An unknown \
             name is an error that lists the valid names.",
            schema,
        )
    }

    fn submit_tool(stage: &manifest::Stage) -> Tool {
        // The submit tool's input schema is the stage's schema, with `_io` removed from
        // its properties if the schema lists it (C5).
        let mut schema: serde_json::Map<String, serde_json::Value> = stage
            .output
            .as_ref()
            .map(|o| o.schema.as_object().cloned().unwrap_or_default())
            .unwrap_or_default();
        if let Some(props) = schema.get_mut("properties").and_then(|p| p.as_object_mut()) {
            props.remove("_io");
        }
        Tool::new(
            SUBMIT,
            "Write this stage's output contract. Pass the full output object as the \
             arguments. Validates it against the stage schema, refuses until every \
             required input has been read in full, stamps the Input receipt (_io) and \
             writes the file atomically.",
            schema,
        )
    }

    /// The stage the server should serve right now, from the current stage.json.
    fn current() -> Option<(String, manifest::Stage)> {
        let (node, _) = common::read_stage()?;
        let m = manifest::load().ok()?;
        let stage = m.stage(&node)?.clone();
        Some((node, stage))
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
            let tools = match Self::current() {
                Some((_, stage)) if stage.output.is_some() => {
                    vec![Self::inputs_tool(), Self::submit_tool(&stage)]
                }
                Some(_) => vec![Self::inputs_tool()],
                None => vec![],
            };
            Ok(ListToolsResult { tools, ..Default::default() })
        }
    }

    fn get_tool(&self, name: &str) -> Option<Tool> {
        let (_, stage) = Self::current()?;
        match name {
            INPUTS => Some(Self::inputs_tool()),
            SUBMIT if stage.output.is_some() => Some(Self::submit_tool(&stage)),
            _ => None,
        }
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _ctx: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, ErrorData> {
        let name = request.name.as_ref();
        if name == INPUTS {
            let params = request.arguments.unwrap_or_default();
            let pname = params.get("name").and_then(|v| v.as_str());
            let part = params.get("part").and_then(|v| v.as_u64()).map(|v| v as u32);
            let res = inputs::build(pname, part);
            if res.is_error {
                return Ok(CallToolResult::error(vec![Content::text(res.text)]));
            }
            return Ok(CallToolResult::success(vec![Content::text(res.text)]));
        }
        if name == SUBMIT {
            let Some((_, stage)) = Self::current() else {
                return Ok(CallToolResult::error(vec![Content::text(
                    "no stage in stage.json".to_string(),
                )]));
            };
            if stage.output.is_none() {
                return Ok(CallToolResult::error(vec![Content::text(
                    "this stage has no output contract".to_string(),
                )]));
            }
            let args = serde_json::Value::Object(request.arguments.unwrap_or_default());
            return Ok(match submit::run(&args) {
                submit::Outcome::Written(msg) => CallToolResult::success(vec![Content::text(msg)]),
                o => CallToolResult::error(vec![Content::text(outcome_text(&o))]),
            });
        }
        Ok(CallToolResult::error(vec![Content::text(format!(
            "unknown tool '{name}'"
        ))]))
    }
}

fn outcome_text(o: &submit::Outcome) -> String {
    match o {
        submit::Outcome::Written(_) => String::new(),
        submit::Outcome::NeedInputs(m)
        | submit::Outcome::Incomplete(m)
        | submit::Outcome::SchemaViolation(m)
        | submit::Outcome::Error(m) => m.clone(),
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
    let service = StreamableHttpService::new(
        || Ok(IoHandler),
        std::sync::Arc::new(LocalSessionManager::default()),
        StreamableHttpServerConfig::default(),
    );
    let app = axum::Router::new().fallback_service(service);
    let addr: SocketAddr = format!("127.0.0.1:{port}").parse()?;
    let listener = tokio::net::TcpListener::bind(addr).await?;
    eprintln!("fabro-io serve: listening on http://127.0.0.1:{port}/mcp");
    axum::serve(listener, app).await?;
    Ok(())
}
