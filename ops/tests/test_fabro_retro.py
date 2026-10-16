"""Tests for ops/fabro-retro.sh (issue #33, ADR 0026 C8).

    python3.11 -m unittest discover -s ops/tests

Every test puts stub `docker` and `curl` scripts first on PATH. The docker stub records
each invocation's arguments and answers by its first two arguments: a run in the
scheduler container prints `$STUB_OUT` and exits `$STUB_RC`, and the webhook read prints a
fake URL (or nothing when `$STUB_NO_HOOK` is set). The curl stub saves the request body,
which is the `{"content": ...}` JSON the wrapper built, to `$CAP/curl-body.json`.
"""

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RETRO = HERE.parent / "fabro-retro.sh"

DOCKER_STUB = """#!/bin/sh
# One invocation per line, so the test can assert exactly what the subcommand got.
printf '%s\\n' "$*" >> "$CAP/docker.argv"
sched="${SCHEDULER_CONTAINER:-fabro-scheduler}"
hook="${CONTAINER:-fabro-fabro-1}"
if [ "$1" = "exec" ] && [ "$2" = "$sched" ]; then
  printf '%s' "$STUB_OUT"
  exit "${STUB_RC:-0}"
fi
if [ "$1" = "exec" ] && [ "$2" = "$hook" ]; then
  [ -n "$STUB_NO_HOOK" ] && exit 0
  printf 'https://discord.invalid/api/webhooks/1/not-a-real-token'
  exit 0
fi
exit 0
"""

CURL_STUB = """#!/bin/sh
# `-d @-` means curl reads the body from stdin; save exactly what it would send.
cat > "$CAP/curl-body.json"
exit "${CURL_RC:-0}"
"""


class Retro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stub = self.dir / "bin"
        self.stub.mkdir()
        for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB)):
            path = self.stub / name
            path.write_text(body)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.tmp.cleanup()

    def run_retro(self, **env_over):
        env = dict(os.environ)
        env.update({"PATH": f"{self.stub}:{env['PATH']}", "CAP": str(self.dir)})
        env.update(env_over)
        return subprocess.run(["sh", str(RETRO)], env=env, capture_output=True, text=True)

    def docker_argv(self):
        return (self.dir / "docker.argv").read_text().splitlines()

    def posted(self):
        return json.loads((self.dir / "curl-body.json").read_text())

    def test_default_env_runs_a_dry_run_and_posts_the_output(self):
        r = self.run_retro(STUB_OUT="hello from the retro")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.docker_argv()[0],
            "exec fabro-scheduler python -m fabro_scheduler retro --last-week --dry-run",
        )
        self.assertEqual(self.posted()["content"], "hello from the retro")
        self.assertIn("hello from the retro", r.stdout)

    def test_dry_run_zero_drops_the_flag(self):
        r = self.run_retro(DRY_RUN="0", STUB_OUT="live")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.docker_argv()[0],
            "exec fabro-scheduler python -m fabro_scheduler retro --last-week",
        )

    def test_retro_args_are_word_split(self):
        r = self.run_retro(RETRO_ARGS="--cap 1 --min-runs 2", STUB_OUT="x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.docker_argv()[0],
            "exec fabro-scheduler python -m fabro_scheduler retro --last-week --dry-run"
            " --cap 1 --min-runs 2",
        )

    def test_week_of_replaces_last_week(self):
        # The subcommand refuses both together, so WEEK_OF swaps one for the other.
        r = self.run_retro(WEEK_OF="2026-10-08", STUB_OUT="x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            self.docker_argv()[0],
            "exec fabro-scheduler python -m fabro_scheduler retro --week-of 2026-10-08"
            " --dry-run",
        )

    def test_a_failing_subcommand_posts_the_failure_line_and_its_output(self):
        r = self.run_retro(STUB_RC="3", STUB_OUT="retro failed: cannot list retro issues")
        self.assertEqual(r.returncode, 3, r.stderr)
        content = self.posted()["content"]
        self.assertTrue(content.startswith("retro failed: exit 3; see"), content)
        self.assertIn("retro failed: cannot list retro issues", content)

    def test_a_failing_subcommand_with_no_output_posts_the_line_alone(self):
        r = self.run_retro(STUB_RC="1")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(
            self.posted()["content"],
            "retro failed: exit 1; see ~/.local/state/fabro-retro.log",
        )

    def test_a_long_message_is_truncated_to_2000_characters(self):
        r = self.run_retro(STUB_OUT="x" * 3000)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.posted()["content"]), 2000)

    def test_no_post_prints_and_never_calls_curl(self):
        r = self.run_retro(NO_POST="1", STUB_OUT="hello")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hello", r.stdout)
        self.assertFalse((self.dir / "curl-body.json").exists())
        # The webhook is not even read.
        self.assertEqual(len(self.docker_argv()), 1)

    def test_an_unreadable_webhook_warns_and_exits_one(self):
        r = self.run_retro(STUB_NO_HOOK="1", STUB_OUT="hello")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read the Discord webhook", r.stderr)
        self.assertFalse((self.dir / "curl-body.json").exists())


if __name__ == "__main__":
    unittest.main()
