//! Test 12: `guard` blocks sealed paths and proceeds otherwise.

mod common;

use fabro_io::guard;

fn set_ctx(ctx: &str) {
    unsafe {
        std::env::set_var("FABRO_HOOK_CONTEXT", ctx);
    }
}
fn clear_ctx() {
    unsafe {
        std::env::remove_var("FABRO_HOOK_CONTEXT");
    }
}

#[test]
fn test_12_guard_sealing() {
    let root = common::temp_root("guard");
    let seal_path = root.join("standards.json");
    let m = common::manifest(&root);
    common::with_manifest(&root, &m, "", || {
        let node = "mystage";
        let visit = "a".repeat(32);

        // read_file of a sealed path -> block (exit 2)
        common::write_stage(&root, node, &visit);
        set_ctx(&format!(r#"{{"tool_input":{{"path":"{}"}}}}"#, seal_path.display()));
        assert_eq!(guard::run(), std::process::ExitCode::from(2), "read_file of a sealed path must block");

        // shell cat of it -> block
        set_ctx(&format!(
            r#"{{"tool_input":{{"command":"cat {}"}}}}"#,
            seal_path.display()
        ));
        assert_eq!(guard::run(), std::process::ExitCode::from(2), "shell cat of a sealed path must block");

        // a sealed glob matches a path -> block
        set_ctx(&format!(r#"{{"tool_input":{{"path":"{}/feedback/notes.txt"}}}}"#, root.display()));
        assert_eq!(guard::run(), std::process::ExitCode::from(2), "glob match must block");

        // a nested string -> block
        set_ctx(&format!(
            r#"{{"tool_input":{{"a":{{"b":["skip","{}/feedback/y/z.txt"]}}}}}}"#,
            root.display()
        ));
        assert_eq!(guard::run(), std::process::ExitCode::from(2), "nested sealed string must block");

        // a non-sealed path -> proceed (0)
        set_ctx(&format!(r#"{{"tool_input":{{"path":"{}/other.txt"}}}}"#, root.display()));
        assert_eq!(guard::run(), std::process::ExitCode::from(0));

        // a node with no manifest entry -> proceed
        common::write_stage(&root, "ghost", &visit);
        set_ctx(&format!(r#"{{"tool_input":{{"path":"{}"}}}}"#, seal_path.display()));
        assert_eq!(guard::run(), std::process::ExitCode::from(0));

        // fabro passes a sandbox hook the PATH of the context file, not the JSON.
        common::write_stage(&root, node, &visit);
        let ctx_file = root.join("hook-context.json");
        std::fs::write(&ctx_file, format!(r#"{{"tool_input":{{"path":"{}"}}}}"#, seal_path.display())).unwrap();
        set_ctx(&ctx_file.display().to_string());
        assert_eq!(guard::run(), std::process::ExitCode::from(2), "the path form is read");
        std::fs::write(&ctx_file, format!(r#"{{"tool_input":{{"path":"{}/other.txt"}}}}"#, root.display())).unwrap();
        assert_eq!(guard::run(), std::process::ExitCode::from(0));
        set_ctx(&root.join("missing.json").display().to_string());
        assert_eq!(guard::run(), std::process::ExitCode::from(0), "a missing context file proceeds");

        // a context that cannot be read -> proceed (0)
        clear_ctx();
        assert_eq!(guard::run(), std::process::ExitCode::from(0));
    });
    let _ = std::fs::remove_dir_all(&root);
}
