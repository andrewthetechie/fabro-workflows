//! `restore_file` and `baseline_check` against a real-git temp repository.
//!
//! What is under test is that the tools undo or check what they say and never touch
//! anything else: the index, other files, and the agent's checkout.

mod common;

use std::path::Path;
use std::process::Command;

use fabro_io::gitsafe;

fn git(dir: &Path, args: &[&str]) -> String {
    let out = Command::new("git")
        .args(["-c", "user.email=t@t", "-c", "user.name=t"])
        .args(args)
        .current_dir(dir)
        .output()
        .unwrap();
    assert!(out.status.success(), "git {args:?}: {}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// A checkout with a base commit holding `a.txt`, `bin/run.sh` (executable) and
/// `.github/workflows/ci.yml`, then a second commit the agent's work sits on.
fn repo(root: &Path) -> (std::path::PathBuf, String) {
    let dir = root.join("workspace").join("repo");
    std::fs::create_dir_all(dir.join("bin")).unwrap();
    std::fs::create_dir_all(dir.join(".github/workflows")).unwrap();
    git(&dir, &["init", "-q", "-b", "main", "."]);
    std::fs::write(dir.join("a.txt"), "base a\n").unwrap();
    std::fs::write(dir.join("bin/run.sh"), "#!/bin/sh\necho base\n").unwrap();
    std::fs::write(dir.join(".github/workflows/ci.yml"), "on: push\n").unwrap();
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(dir.join("bin/run.sh"), std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-qm", "base"]);
    let base = git(&dir, &["rev-parse", "HEAD"]);
    std::fs::write(dir.join("a.txt"), "agent a\n").unwrap();
    std::fs::write(dir.join("new.txt"), "agent new\n").unwrap();
    git(&dir, &["add", "-A"]);
    git(&dir, &["commit", "-qm", "checkpoint"]);
    (dir, base)
}

#[test]
fn restore_file_restores_deletes_and_refuses() {
    let root = common::temp_root("gs-restore");
    let (dir, base) = repo(&root);
    std::fs::write(dir.join("a.txt"), "agent a, edited again\n").unwrap();
    std::fs::write(dir.join("other.txt"), "untouched\n").unwrap();

    // An existing file goes back to its base content; nothing else changes.
    let msg = gitsafe::restore_file(&dir, &base, "a.txt").unwrap();
    assert!(msg.contains("restored a.txt"), "{msg}");
    assert_eq!(std::fs::read_to_string(dir.join("a.txt")).unwrap(), "base a\n");
    assert_eq!(std::fs::read_to_string(dir.join("other.txt")).unwrap(), "untouched\n");
    assert_eq!(git(&dir, &["stash", "list"]), "", "no stash is made");
    assert!(git(&dir, &["status", "--porcelain"]).contains("a.txt"), "the restore is a working-tree change only");

    // Restoring twice is a no-op with its own message.
    let msg = gitsafe::restore_file(&dir, &base, "a.txt").unwrap();
    assert!(msg.contains("already matches"), "{msg}");

    // A file that did not exist at base is deleted; an absolute path inside the checkout works.
    let abs = dir.join("new.txt").display().to_string();
    let msg = gitsafe::restore_file(&dir, &base, &abs).unwrap();
    assert!(msg.contains("deleted new.txt"), "{msg}");
    assert!(!dir.join("new.txt").exists());
    let msg = gitsafe::restore_file(&dir, &base, "new.txt").unwrap();
    assert!(msg.contains("already absent"), "{msg}");

    // A deleted file that existed at base comes back, with its executable bit.
    std::fs::remove_file(dir.join("bin/run.sh")).unwrap();
    gitsafe::restore_file(&dir, &base, "bin/run.sh").unwrap();
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(std::fs::metadata(dir.join("bin/run.sh")).unwrap().permissions().mode() & 0o111, 0o111);

    // Refusals.
    for bad in ["../x", "a/../../x", "/etc/passwd", ".github/workflows/ci.yml", ".git/config", "", "bin"] {
        let e = gitsafe::restore_file(&dir, &base, bad).unwrap_err();
        assert!(!e.is_empty(), "{bad}");
    }
    assert_eq!(
        std::fs::read_to_string(dir.join(".github/workflows/ci.yml")).unwrap(),
        "on: push\n",
        "a workflow file is never written"
    );
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn restore_base_is_the_task_base_in_the_task_loop_and_head_after_it() {
    let root = common::temp_root("gs-restore-base");
    let (dir, base) = repo(&root);
    let head = git(&dir, &["rev-parse", "HEAD"]);
    let io = root.join("io");
    std::fs::create_dir_all(&io).unwrap();

    // arch-review and issue-triage: no base file, so the start of the stage.
    assert_eq!(gitsafe::restore_base(&io, &dir).unwrap(), head);

    // backlog's task loop: the task base, which also undoes the coder's edit in rework.
    std::fs::write(io.join("task_base_sha"), format!("{base}\n")).unwrap();
    assert_eq!(gitsafe::restore_base(&io, &dir).unwrap(), base);

    // Once run_base_sha exists (backlog's merge phase keeps a stale task_base_sha, and
    // pr-review has a base_ref), it is HEAD: never the last task's base, never main.
    std::fs::write(io.join("run_base_sha"), format!("{base}\n")).unwrap();
    std::fs::write(io.join("base_ref"), "main\n").unwrap();
    git(&dir, &["update-ref", "refs/remotes/origin/main", &base]);
    assert_eq!(gitsafe::restore_base(&io, &dir).unwrap(), head);
    std::fs::remove_file(io.join("task_base_sha")).unwrap();
    assert_eq!(gitsafe::restore_base(&io, &dir).unwrap(), head);

    // In pr-review, restoring a file the PR changed keeps the PR's content.
    std::fs::write(dir.join("a.txt"), "fixer's edit\n").unwrap();
    let to = gitsafe::restore_base(&io, &dir).unwrap();
    gitsafe::restore_file(&dir, &to, "a.txt").unwrap();
    assert_eq!(std::fs::read_to_string(dir.join("a.txt")).unwrap(), "agent a\n");
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn baseline_base_prefers_run_base_then_task_base_then_merge_base() {
    let root = common::temp_root("gs-base");
    let (dir, base) = repo(&root);
    let head = git(&dir, &["rev-parse", "HEAD"]);
    let io = root.join("io");
    std::fs::create_dir_all(&io).unwrap();

    assert!(gitsafe::baseline_base(&io, &dir).is_err(), "no file is an error that says to move on");

    // With no run or task base: the merge base of HEAD and origin/<base_ref>.
    std::fs::write(io.join("base_ref"), "main\n").unwrap();
    git(&dir, &["update-ref", "refs/remotes/origin/main", &base]);
    assert_eq!(gitsafe::baseline_base(&io, &dir).unwrap(), base);

    // backlog's task loop.
    std::fs::write(io.join("task_base_sha"), format!("{head}\n")).unwrap();
    assert_eq!(gitsafe::baseline_base(&io, &dir).unwrap(), head);

    // backlog's merge phase and pr-review: run_base_sha wins over a stale task base.
    std::fs::write(io.join("run_base_sha"), format!("{base}\n")).unwrap();
    assert_eq!(gitsafe::baseline_base(&io, &dir).unwrap(), base);
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn baseline_check_runs_on_the_base_and_never_touches_the_checkout() {
    let root = common::temp_root("gs-baseline");
    let (dir, base) = repo(&root);
    let io = root.join("io");
    std::fs::create_dir_all(&io).unwrap();
    let rt = tokio::runtime::Runtime::new().unwrap();

    // No shared dependencies in this repository: the "not available" answer.
    let e = rt
        .block_on(gitsafe::baseline_check(&io, &dir, &base, "cat a.txt", 20))
        .unwrap_err();
    assert!(e.contains("not available in this repository"), "{e}");

    // A node_modules in the checkout is linked into the worktree.
    std::fs::create_dir_all(dir.join("node_modules/dep")).unwrap();
    std::fs::write(dir.join("node_modules/dep/index.js"), "dep\n").unwrap();
    let before = git(&dir, &["status", "--porcelain"]);
    let out = rt
        .block_on(gitsafe::baseline_check(&io, &dir, &base, "cat a.txt; cat node_modules/dep/index.js; pwd; exit 3", 20))
        .unwrap();
    assert!(out.starts_with("exit=3\n"), "{out}");
    assert!(out.contains("base a"), "runs on the base commit, not the agent's edit: {out}");
    assert!(out.contains("\ndep\n"), "node_modules is shared: {out}");
    assert!(out.contains("base-tree"), "runs in the worktree: {out}");
    assert_eq!(std::fs::read_to_string(dir.join("a.txt")).unwrap(), "agent a\n", "the checkout is untouched");
    assert!(dir.join("new.txt").exists());
    assert_eq!(git(&dir, &["status", "--porcelain"]), before);

    // A command written to the worktree stays there.
    rt.block_on(gitsafe::baseline_check(&io, &dir, &base, "echo scratch > scratch.txt", 20)).unwrap();
    assert!(!dir.join("scratch.txt").exists());

    // Only the last 200 lines come back.
    let out = rt
        .block_on(gitsafe::baseline_check(&io, &dir, &base, "seq 1 500", 20))
        .unwrap();
    assert!(out.contains("300 earlier lines cut") && out.ends_with("500"), "{out}");
    assert!(!out.contains("\n100\n"));

    // A timeout kills the command and its children.
    let started = std::time::Instant::now();
    let out = rt
        .block_on(gitsafe::baseline_check(&io, &dir, &base, "sh -c 'sleep 30' & sleep 30", 1))
        .unwrap();
    assert!(started.elapsed() < std::time::Duration::from_secs(10), "returned after the timeout");
    assert!(out.starts_with("exit=timed out after 1s"), "{out}");

    // The worktree is reused for the same base and rebuilt for another one.
    let wt = io.join("base-tree");
    assert!(wt.exists());
    let next = git(&dir, &["rev-parse", "HEAD"]);
    let out = rt
        .block_on(gitsafe::baseline_check(&io, &dir, &next, "cat a.txt", 20))
        .unwrap();
    assert!(out.contains("agent a"), "a new base rebuilds the worktree: {out}");
    let _ = std::fs::remove_dir_all(&root);
}
