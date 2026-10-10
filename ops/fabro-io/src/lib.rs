//! fabro-io — the Stage I/O MCP server (ADR 0016, docs/stage-io).
//!
//! reads a stage's inputs and writes its output contract with an Input receipt. All
//! stage facts live in the manifest (C2), never in the binary.

pub mod cli;
pub mod code;
pub mod common;
pub mod gitguard;
pub mod gitsafe;
pub mod guard;
pub mod inputs;
pub mod manifest;
pub mod pages;
pub mod runtests;
pub mod serve;
pub mod served;
pub mod stage;
pub mod submit;
pub mod testenv;
