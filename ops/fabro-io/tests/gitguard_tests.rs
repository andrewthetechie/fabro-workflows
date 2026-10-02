//! `git-guard` (docs/coder-tweaks C2): which git invocations it blocks, which it lets
//! through, which stages are exempt, and that it never blocks on its own errors.

mod common;

use std::process::ExitCode;

use fabro_io::gitguard::{self, violations};

fn blocked(command: &str) -> Vec<String> {
    violations(command).into_iter().map(|v| v.subcommand).collect()
}

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

fn shell(command: &str) -> String {
    serde_json::json!({"tool_name": "shell", "tool_input": {"command": command}}).to_string()
}

#[test]
fn every_allowlisted_form_passes() {
    for c in [
        "git status",
        "git status --short | head",
        "git diff HEAD^ HEAD --stat",
        "git log --oneline -5 -- src/a.py",
        "git show HEAD:src/a.py",
        "git blame -L1,5 a.py",
        "git grep -n foo",
        "git ls-files | wc -l",
        "git ls-tree -r HEAD",
        "git rev-parse HEAD",
        "git merge-base HEAD origin/main",
        "git cat-file -p HEAD",
        "git range-diff a b c",
        "git describe --tags",
        "git shortlog -sn",
        "git rev-list --count HEAD",
        "git diff-tree --no-commit-id -r HEAD",
        "git name-rev HEAD",
        "git for-each-ref",
        "git check-ignore -v x",
        "git version",
        "git help status",
        "git stash list",
        "git stash show -p",
        "git branch",
        "git branch -a",
        "git branch --show-current",
        "git branch -a --contains abc123",
        "git branch --merged main -v",
        "git worktree list",
        "git config --get user.name",
        "git config --get-regexp 'remote.*'",
        "git -C /w/repo status",
        "git -c core.pager=cat log -1",
        "git --no-pager diff",
        "cd /w && git --no-pager log -3 | head",
    ] {
        assert!(violations(c).is_empty(), "{c} should pass: {:?}", violations(c));
    }
}

#[test]
fn mutating_forms_are_blocked() {
    assert_eq!(blocked("git stash && uv run mypy x.py; git stash pop"), ["stash", "stash"]);
    assert_eq!(blocked("cd a && git checkout -- b"), ["checkout"]);
    assert_eq!(blocked("git -C x commit -m y"), ["commit"]);
    assert_eq!(blocked("git fetch"), ["fetch"]);
    assert_eq!(blocked("git fetch ."), ["fetch"]);
    assert_eq!(blocked("git restore a.py"), ["restore"]);
    assert_eq!(blocked("git reset --hard HEAD"), ["reset"]);
    assert_eq!(blocked("git clean -fd"), ["clean"]);
    assert_eq!(blocked("git add -A && git commit -qm x && git push"), ["add", "commit", "push"]);
    assert_eq!(blocked("git branch -D x"), ["branch"]);
    assert_eq!(blocked("git branch newname"), ["branch"]);
    assert_eq!(blocked("git worktree add /tmp/x HEAD"), ["worktree"]);
    assert_eq!(blocked("git config user.name x"), ["config"]);
    assert_eq!(blocked("git stash push --include-untracked"), ["stash"]);
    assert_eq!(blocked("git merge --no-edit origin/main"), ["merge"]);
    assert_eq!(blocked("git rebase main"), ["rebase"]);
}

#[test]
fn command_position_forms_are_found() {
    assert_eq!(blocked("true || git stash"), ["stash"]);
    assert_eq!(blocked("(git stash)"), ["stash"]);
    assert_eq!(blocked("X=$(git stash)"), ["stash"]);
    assert_eq!(blocked("echo `git stash`"), ["stash"]);
    assert_eq!(blocked("ls | xargs git add"), ["add"]);
    assert_eq!(blocked("ls | xargs -n1 git rm"), ["rm"]);
    assert_eq!(blocked("GIT_EDITOR=true git merge --continue"), ["merge"]);
    assert_eq!(blocked("env FOO=1 git stash"), ["stash"]);
    assert_eq!(blocked("timeout 30 git fetch"), ["fetch"]);
    assert_eq!(blocked("/usr/bin/git stash"), ["stash"]);
    assert_eq!(blocked("git status\ngit stash"), ["stash"]);
    assert_eq!(blocked("git --no-pager -C x checkout -- a"), ["checkout"]);
}

#[test]
fn quoted_and_inert_text_passes() {
    for c in [
        "grep -n 'git stash' f",
        r#"echo "git commit""#,
        r"grep -rn 'git add\|git commit' docs",
        "echo git stash",
        "ls git-stash.txt",
        "cat <<'EOF'\ngit stash\ngit commit -m x\nEOF",
        "cat > notes.md <<EOF\ngit checkout -- a\nEOF\ngit status",
        "git log --grep='git stash'",
    ] {
        assert!(violations(c).is_empty(), "{c} should pass: {:?}", violations(c));
    }
    // A here-document ends at its delimiter; what follows is code again.
    assert_eq!(blocked("cat <<EOF\ngit add\nEOF\ngit stash"), ["stash"]);
}

#[test]
fn reasons_name_the_alternative() {
    let reason = |c: &str| violations(c).remove(0).reason;
    for c in ["git stash", "git checkout -- a", "git restore a"] {
        let r = reason(c);
        assert!(r.contains("restore_file") && r.contains("baseline_check"), "{c}: {r}");
    }
    for c in ["git commit -m x", "git add a", "git push"] {
        assert!(reason(c).contains("checkpoint commits your working tree"), "{c}");
    }
    for c in ["git fetch", "git pull", "git merge x", "git rebase x"] {
        assert!(reason(c).contains("command nodes own branch and remote state"), "{c}");
    }
    assert!(reason("git stash").contains("git stash"), "names the subcommand");
}

#[test]
fn the_hook_blocks_outside_exempt_stages_and_proceeds_inside() {
    let root = common::temp_root("gitguard");
    let manifest = format!(
        r#"{{"version":1,"min_binary":"0.1.0","stages":{{
            "coder": {{"inputs":[]}},
            "resolve_merge": {{"inputs":[],"git":"write"}},
            "reader": {{"inputs":[],"git":"read"}}
        }}}}"#
    );
    common::with_manifest(&root, &manifest, "", || {
        let visit = "a".repeat(32);

        // A stage the manifest lists with no `git` key: blocked.
        common::write_stage(&root, "coder", &visit);
        set_ctx(&shell("git stash && make; git stash pop"));
        assert_eq!(gitguard::run(), ExitCode::from(2));
        set_ctx(&shell("git status && git diff --stat"));
        assert_eq!(gitguard::run(), ExitCode::SUCCESS);

        // Any value but "write" is read-only. An unlisted node is read-only too.
        common::write_stage(&root, "reader", &visit);
        set_ctx(&shell("git fetch"));
        assert_eq!(gitguard::run(), ExitCode::from(2));
        common::write_stage(&root, "ghost", &visit);
        assert_eq!(gitguard::run(), ExitCode::from(2));

        // The exempt stage may run anything.
        common::write_stage(&root, "resolve_merge", &visit);
        set_ctx(&shell("git add a.py && GIT_EDITOR=true git merge --continue"));
        assert_eq!(gitguard::run(), ExitCode::SUCCESS);

        // Only the shell tool is judged.
        common::write_stage(&root, "coder", &visit);
        set_ctx(&serde_json::json!({"tool_name": "read_file", "tool_input": {"command": "git stash"}}).to_string());
        assert_eq!(gitguard::run(), ExitCode::SUCCESS);
        // A context with no tool name but a command still counts as shell.
        set_ctx(&serde_json::json!({"tool_input": {"command": "git stash"}}).to_string());
        assert_eq!(gitguard::run(), ExitCode::from(2));

        // Its own errors never block.
        set_ctx("not json");
        assert_eq!(gitguard::run(), ExitCode::SUCCESS);
        clear_ctx();
        assert_eq!(gitguard::run(), ExitCode::SUCCESS);
        set_ctx(&shell("git stash"));
        std::fs::remove_file(root.join(".io/stage.json")).unwrap();
        assert_eq!(gitguard::run(), ExitCode::SUCCESS, "missing stage.json proceeds");
        common::write_stage(&root, "coder", &visit);
        unsafe {
            std::env::set_var("FABRO_IO_MANIFEST", "{not json");
        }
        assert_eq!(gitguard::run(), ExitCode::SUCCESS, "a broken manifest proceeds");
        clear_ctx();
    });
    let _ = std::fs::remove_dir_all(&root);
}
