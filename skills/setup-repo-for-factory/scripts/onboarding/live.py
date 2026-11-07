"""Calls that leave the machine: GitHub through `gh`, the fabro API, the scheduler, and the host over ssh."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent.parent.parent / "data"
TEMPLATES = DATA.parent / "templates"
TRANSIENT = re.compile(r"dial tcp|can't assign requested address|timeout|TLS handshake|connection reset|EOF|HTTP 50[234]", re.I)
ABSENT = re.compile(r"HTTP 40[34]|Not Found")


def run(*args: str, cwd: Path | None = None, input_text: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, input=input_text, capture_output=True, text=True, timeout=timeout)


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    """`gh`, retried only on the failures that say nothing about the answer."""
    out = run("gh", *args)
    for delay in (3, 10):
        if out.returncode == 0 or not TRANSIENT.search(out.stderr):
            break
        time.sleep(delay)
        out = run("gh", *args)
    return out


def gh_json(*args: str) -> Any:
    out = _gh(*args)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def gh_probe(path: str) -> Any | None:
    """The API's answer, or None when GitHub says there is no such thing. Anything else raises:
    a failure to ask must never read as an answer."""
    out = _gh("api", path)
    if out.returncode == 0:
        return json.loads(out.stdout)
    if ABSENT.search(out.stderr):
        return None
    raise RuntimeError(f"gh api {path}: {out.stderr.strip()}")


def repo_settings(repo: str) -> dict[str, Any]:
    return gh_json("api", f"repos/{repo}")


def dependabot_alerts_readable(repo: str) -> bool:
    return gh_probe(f"repos/{repo}/dependabot/alerts?per_page=1") is not None


# --- labels ------------------------------------------------------------------------------------


def label_catalog() -> list[dict[str, str]]:
    with (DATA / "labels.toml").open("rb") as handle:
        return tomllib.load(handle)["label"]


def missing_labels(repo: str) -> list[dict[str, str]]:
    names = _gh("api", f"repos/{repo}/labels", "--paginate", "--jq", ".[].name")
    if names.returncode != 0:
        raise RuntimeError(f"cannot list labels of {repo}: {names.stderr.strip()}")
    # GitHub compares label names without regard to case.
    existing = {n.lower() for n in names.stdout.split("\n")}
    return [label for label in label_catalog() if label["name"].lower() not in existing]


def create_label(repo: str, label: dict[str, str]) -> None:
    out = _gh("label", "create", label["name"], "--repo", repo, "--color", label["color"],
              "--description", label.get("description", ""))
    if out.returncode != 0:
        raise RuntimeError(f"cannot create label {label['name']}: {out.stderr.strip()}")


# --- the fabro API -----------------------------------------------------------------------------


def token(host: str) -> str:
    """The server's own dev token, read the way `ops/deploy-host.sh` reads it. Never printed."""
    from_env = os.environ.get("FABRO_DEV_TOKEN") or os.environ.get("FABRO_API_TOKEN")
    if from_env:
        return from_env
    out = run("ssh", "-o", "ConnectTimeout=10", host, "docker exec fabro-fabro-1 cat /storage/server.dev-token")
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"no FABRO_DEV_TOKEN set and could not read it over ssh from {host}")
    return out.stdout.strip()


def http_json(url: str, *, auth: str = "", method: str = "GET", timeout: int = 15) -> Any:
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    request = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  operator-configured URL
        return json.loads(response.read() or b"null")


def api_list(api_url: str, auth: str, path: str) -> list[dict[str, Any]]:
    return http_json(f"{api_url}{path}", auth=auth).get("data", [])


def fire(api_url: str, auth: str, automation: str) -> str:
    response = http_json(f"{api_url}/automations/{automation}/runs", auth=auth, method="POST")
    run_id = response.get("id") or response.get("run_id") or (response.get("run") or {}).get("id")
    if not run_id:
        raise RuntimeError(f"firing {automation} returned no run id; the response has {sorted(response)}")
    return run_id


def run_state(api_url: str, auth: str, run_id: str) -> tuple[str, Any]:
    body = http_json(f"{api_url}/runs/{run_id}", auth=auth)
    return body["lifecycle"]["status"]["kind"], body["lifecycle"].get("error")


# --- the contract in its real image -----------------------------------------------------------


def contract_run(*, host: str, repo: str, ref: str, image: str, cpus: str, memory: str, log: Path, timeout: int) -> tuple[int, str]:
    """Run `setup.sh && ci.sh` from a fresh clone in the repository's own profile image, on the host.

    Returns the exit status and the merged output, which is also written to `log`.
    """
    script = (TEMPLATES / "contract-run.sh").read_text()
    out = subprocess.run(["ssh", host, "bash", "-s", "--", repo, ref, image, cpus, memory], input=script, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(out.stdout)
    return out.returncode, out.stdout


def describe_error(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code} from {error.url}: {error.read().decode(errors='replace')[:200]}"
    return str(error)
