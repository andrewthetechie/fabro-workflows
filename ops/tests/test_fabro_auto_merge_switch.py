"""Tests for ops/fabro-auto-merge-switch.sh and its dependabot rows (ADR 0022 D17, task 09).

    python3.11 -m unittest discover -s ops/tests

The script talks to the fabro API through curl. Each test puts a stub curl first on PATH: it
serves a fixture automations list for the GET, and it captures the body of a PUT, then echoes that
body back as the response so the script's read-back check passes.
"""
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SWITCH = HERE.parent / "fabro-auto-merge-switch.sh"

CURL_STUB = """#!/bin/sh
out=""; method=GET; data=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -X) method="$2"; shift ;;
    --data-binary) data="$2"; shift ;;
    -w) shift ;;
  esac
  shift
done
if [ "$method" = PUT ]; then
  cp "${data#@}" "$CAP/put.json"
  cp "${data#@}" "$out"
  printf 200
  exit 0
fi
cp "$CAP/list.json" "$out"
printf 200
"""


def automation(id_, workflow, repo, description, revision="7"):
    return {
        "id": id_,
        "revision": revision,
        "workflow": workflow,
        "target": {"repo": repo, "kind": "git", "branch": "main"},
        "environment_id": "python",
        "description": description,
        "workflow_source": {"repo": "andrewthetechie/fabro-workflows", "branch": "main"},
        "triggers": [{"type": "api", "id": "manual", "enabled": True}],
    }


class Switch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.stub = self.dir / "bin"
        self.stub.mkdir()
        curl = self.stub / "curl"
        curl.write_text(CURL_STUB)
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.tmp.cleanup()

    def run_switch(self, rows, args, dry_run="1"):
        (self.dir / "list.json").write_text(json.dumps({"data": rows}))
        env = dict(os.environ)
        env.update({
            "PATH": f"{self.stub}:{env['PATH']}",
            "CAP": str(self.dir),
            "FABRO_API_URL": "http://fake.test/api/v1",
            "FABRO_DEV_TOKEN": "test-token",
            "DRY_RUN": dry_run,
        })
        return subprocess.run(["sh", str(SWITCH), *args], env=env, capture_output=True, text=True)

    def put_body(self):
        return json.loads((self.dir / "put.json").read_text())

    def test_dependabot_row_by_repo_flips_the_token_and_keeps_the_text(self):
        rows = [automation("dependabot-jelly-swipe", "dependabot", "andrewthetechie/jelly-swipe",
                           "dependabot daily run for andrewthetechie/jelly-swipe. auto_merge=true")]
        r = self.run_switch(rows, ["--workflow", "dependabot", "andrewthetechie/jelly-swipe", "off"], dry_run="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        desc = self.put_body()["description"]
        self.assertEqual(desc, "dependabot daily run for andrewthetechie/jelly-swipe. auto_merge=false")

    def test_dependabot_id_is_accepted(self):
        rows = [automation("dependabot-writers-app", "dependabot", "andrewthetechie/writers-app",
                           "dependabot daily run for andrewthetechie/writers-app. auto_merge=false")]
        r = self.run_switch(rows, ["dependabot-writers-app", "on"], dry_run="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto_merge=true", self.put_body()["description"])

    def test_empty_description_on_a_dependabot_row_names_the_workflow(self):
        rows = [automation("dependabot-writers-app", "dependabot", "andrewthetechie/writers-app", "")]
        r = self.run_switch(rows, ["dependabot-writers-app", "on"], dry_run="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.put_body()["description"],
                         "dependabot daily run (switch only). auto_merge=true")

    def test_dry_run_prints_the_change_and_sends_nothing(self):
        rows = [automation("dependabot-jelly-swipe", "dependabot", "andrewthetechie/jelly-swipe",
                           "dependabot daily run for andrewthetechie/jelly-swipe. auto_merge=true")]
        r = self.run_switch(rows, ["--workflow", "dependabot", "andrewthetechie/jelly-swipe", "off"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto_merge   true -> false", r.stdout)
        self.assertFalse((self.dir / "put.json").exists())

    def test_owner_repo_without_the_flag_still_selects_the_pr_review_row(self):
        rows = [automation("dependabot-jelly-swipe", "dependabot", "andrewthetechie/jelly-swipe",
                           "dependabot daily run for andrewthetechie/jelly-swipe. auto_merge=true")]
        r = self.run_switch(rows, ["andrewthetechie/jelly-swipe", "off"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no automation matches", r.stderr)

    def test_a_backlog_id_is_still_refused(self):
        rows = [automation("backlog-jelly-swipe", "backlog", "andrewthetechie/jelly-swipe", "")]
        r = self.run_switch(rows, ["backlog-jelly-swipe", "off"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a pr-review or dependabot automation", r.stderr)


if __name__ == "__main__":
    unittest.main()
