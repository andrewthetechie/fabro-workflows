"""The Docker Engine HTTP API, reached over the mounted unix socket.

The scheduler container is given the host's `/var/run/docker.sock` (read-only) so
it can run exactly one read command inside a fabro sandbox: counting the
decomposed tasks, which live only on the sandbox filesystem
(`/tmp/fabro/tasks.json`) and are not exposed by the fabro API. This is the same
datum `ops/fabro-run-status.sh` reads by `docker exec` from the Mac; here it is
reached over the socket instead.

**No `docker` CLI is installed** — the Dockerfile deliberately does not add one.
The Engine API over the socket needs only `httpx`, which is already a dependency,
and a `httpx.HTTPTransport(uds=...)`. The exec flow is three calls:

1. `POST /containers/{id}/exec`  → an exec instance id.
2. `POST /exec/{id}/start`       → the process output as a multiplexed stream;
   with `Detach: false` this blocks until the command finishes, so `resp.content`
   is the complete output.
3. `GET /exec/{id}/json`         → the exit code (the probe only needs stdout).

The socket is mounted read-only, so a bug here can read a counter from a sandbox
but cannot mutate the daemon (no container/image/network mutation is reachable
over a `ro` socket).
"""

from __future__ import annotations

import struct

import httpx

API_VERSION = "v1.41"
DOCKER_SOCKET = "/var/run/docker.sock"
DEFAULT_TIMEOUT_SECONDS = 10.0


def decode_docker_stream(data: bytes) -> str:
    """Turn a non-TTY Docker exec multiplexed stream into plain text.

    Each frame is an 8-byte header — stream type byte, three reserved bytes, then
    a big-endian uint32 payload length — followed by that many payload bytes.
    stdout (1) and stderr (2) payloads are kept, stdin (0) is dropped, and the
    headers are discarded. The result is stripped of surrounding whitespace.
    """
    out = bytearray()
    off = 0
    n = len(data)
    while off + 8 <= n:
        stream_type = data[off]
        size = struct.unpack(">I", data[off + 4 : off + 8])[0]
        off += 8
        payload = data[off : off + size]
        off += size
        if stream_type in (1, 2):  # stdout, stderr
            out += payload
    return out.decode("utf-8", "replace").strip()


class DockerClient:
    """Minimal Engine API client over the unix socket.

    Only the pieces the probe needs: run a short command inside one container and
    return its combined standard output. Error behaviour is deliberate: a missing
    container (sandbox gone) fails the `/exec` create with 404 and raises, which
    the probe records as "no task count right now" rather than crashing its beat.
    """

    def __init__(self, socket_path: str = DOCKER_SOCKET) -> None:
        self._socket_path = socket_path
        # Note: no shared transport. httpx `Client` takes ownership of a passed
        # `transport` and closes it on exit, so reusing one across the `with`
        # blocks below would close it after the first call. Create it per call.
        self._base = f"http://docker/{API_VERSION}"

    def exec(
        self,
        container_id: str,
        command: list[str],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        """Run `command` inside `container_id`, returning combined stdout+stderr."""
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=self._socket_path),
            timeout=timeout,
            base_url=self._base,
        ) as client:
            created = client.post(
                f"/containers/{container_id}/exec",
                json={"AttachStdout": True, "AttachStderr": True, "Cmd": command},
            )
            created.raise_for_status()
            exec_id = created.json()["Id"]

            started = client.post(
                f"/exec/{exec_id}/start",
                json={"Detach": False, "Tty": False},
            )
            started.raise_for_status()
            raw = started.content

            inspected = client.get(f"/exec/{exec_id}/json")
            inspected.raise_for_status()
            # The probe ignores the exit code: the read command is written to be
            # tolerant already, and a failed container is surfaced as a missing
            # value by the caller regardless.
        return decode_docker_stream(raw)
