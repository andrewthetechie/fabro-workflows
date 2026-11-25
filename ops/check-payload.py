#!/usr/bin/env python3.11
"""check-payload.py — every workflow's dispatch payload fits the server's version limit.

Usage:
  check-payload.py [--root DIR]     check every package under DIR/.fabro/workflows/

A dispatch uploads one package plus `workflows/_shared/` (ralph-v2 01):
`build_version_payload` in the scheduler's `workflow_version.py` builds that payload, and
the server refuses one over 2 MiB of compact canonical JSON. The refusal is not scoped to
the package that grew — it stops **every** workflow's dispatch — so the size has to be
checked before a push that grows any package, not only the one being changed.

The budget here is 1,500,000 bytes a package, the headroom task 01 sets against the
2,097,152-byte cap, and the server's 512 KiB per-file limit. One line per package with its
byte count; exit 1 when a package is over either.

Standard library only, and it reads the tree itself rather than importing the scheduler:
this is an offline gate in `make check`, where the scheduler's dependencies are not
installed and its package is not on the path. `tests/test_check_payload.py` holds the two
together.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The server's `WorkflowVersion` schema, and the per-file half of it. `MAX_FILE_BYTES` is
# also the scheduler's.
SERVER_MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024

# The headroom a package must keep under that cap. Over it, the package can no longer be
# uploaded and no workflow can be dispatched (ralph-v2 01, A1), so the gate fails far
# earlier than the server would.
PAYLOAD_BUDGET_BYTES = 1_500_000

# The shared phase graphs and their prompts travel with every package that imports them.
SHARED_PREFIX = "workflows/_shared/"


def package_files(fabro: Path, package: str) -> dict[str, str]:
    """The files one dispatch of `package` uploads: the package's and `workflows/_shared/`'s.

    Keyed by the POSIX path relative to `fabro`, sorted, exactly as the scheduler keys
    them. Everything else under `.fabro/` — another package's files, `_io/` — is dropped:
    no graph imports another package's files and `_io/` is embedded in each
    `workflow.toml`.
    """
    prefix = f"workflows/{package}/"
    files: dict[str, str] = {}
    for path in sorted(fabro.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(fabro).as_posix()
        if relative.startswith(prefix) or relative.startswith(SHARED_PREFIX):
            files[relative] = path.read_text()
    return files


def payload_bytes(package: str, files: dict[str, str]) -> int:
    """The server's measure: compact canonical JSON, `ensure_ascii` on (the larger form)."""
    payload = {
        "entrypoint": f"workflows/{package}/workflow.fabro",
        "files": files,
        "workflow_dependencies": {},
    }
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def check(root: Path, budget: int = PAYLOAD_BUDGET_BYTES) -> tuple[list[str], list[str]]:
    """`(lines, failures)` for every package under `root/.fabro/workflows/`."""
    fabro = root / ".fabro"
    packages = sorted(
        path.parent.name for path in (fabro / "workflows").glob("*/workflow.fabro")
    )
    if not packages:
        return [], [f"{fabro}: no workflows/*/workflow.fabro to check"]

    lines: list[str] = []
    failures: list[str] = []
    for package in packages:
        files = package_files(fabro, package)
        size = payload_bytes(package, files)
        lines.append(f"  {package:<20} {size:>9,} bytes  ({len(files)} file(s))")
        if size > budget:
            failures.append(
                f"{package}: {size} bytes, over the {budget:,}-byte payload budget"
                f" (the server's cap is {SERVER_MAX_PAYLOAD_BYTES:,})"
            )
        for relative, text in files.items():
            size = len(text.encode("utf-8"))
            if size > MAX_FILE_BYTES:
                failures.append(
                    f"{relative}: {size} bytes, over the {MAX_FILE_BYTES:,}-byte per-file limit"
                )
    return lines, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check every workflow's dispatch payload.")
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO,
        help="the checkout to read (default: the one this script is in)",
    )
    args = parser.parse_args(argv)

    lines, failures = check(args.root)
    for line in lines:
        print(line)
    for failure in failures:
        print(f"  FAIL {failure}")
    if failures:
        return 1
    print(f"  ok: {len(lines)} packages under the {PAYLOAD_BUDGET_BYTES:,}-byte payload budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
