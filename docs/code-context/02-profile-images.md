# 02 · codegraph, sqlite3 and fabro-code in every profile image

Read `00-overview-and-contracts.md` first. The contract for this task is C5.

## Change

In each of `ops/profile-images/Dockerfile.{python,python-node,ts,rust-node}`:

1. Add `sqlite3` to the existing `apt-get install` line.
2. Add a pinned install. The digest is the one that the v1.6.0 release publishes for this
   asset:

   ```dockerfile
   ARG CODEGRAPH_VERSION=1.6.0
   ARG CODEGRAPH_SHA256=de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0
   ENV CODEGRAPH_TELEMETRY=0 DO_NOT_TRACK=1 CODEGRAPH_NO_UPDATE_CHECK=1
   RUN curl -fsSL -o /tmp/cg.tgz "https://github.com/colbymchenry/codegraph/releases/download/v${CODEGRAPH_VERSION}/codegraph-linux-x64.tar.gz" \
    && echo "${CODEGRAPH_SHA256}  /tmp/cg.tgz" | sha256sum -c - \
    && mkdir -p /opt/codegraph && tar -xzf /tmp/cg.tgz -C /opt/codegraph --strip-components=1 \
    && rm /tmp/cg.tgz && ln -s /opt/codegraph/bin/codegraph /usr/local/bin/codegraph \
    && codegraph --version
   ```

   Check the path of the binary inside the tarball before you write the symlink:
   `tar -tzf cg.tgz | head`. `install.sh` strips one top-level directory, and the bench
   found the binary on `PATH` after that step.
3. `COPY fabro-code /usr/local/bin/fabro-code` (task 03 writes the file). This task can land
   first with a stub that prints `no code index; use grep` and exits 2.

Do **not** use `install.sh`. It resolves "latest" at build time, so the pin would mean
nothing.

## Gate in `build-images.sh`

After the existing contract check (`.fabro/setup.sh`), add an **index check** for each
image. Run it offline, in a throwaway copy of the warmed clone, with `--cpus=2 -m 4g`:

```sh
codegraph init --yes </dev/null && codegraph status | grep -q 'up to date' \
  && [ "$(git status --porcelain | grep -c codegraph)" = 1 ]
```

The last test proves that the only new path is `.codegraph/`. Nothing may write into
`AGENTS.md`. Log the wall time. If it fails, keep the previous image, as the other checks do.

## Acceptance

- All four images build, and `codegraph --version` prints `1.6.0` in each.
- The index check passes in each. The logged wall times are within 2× of the bench:
  jelly-swipe 1.2 s, writers-app 3.0 s, womens-fantasy-sports 4.3 s, lawncare-saas 4.2 s.
- `docker run --rm --network none <image> codegraph init --yes` in a copy of the clone
  succeeds, so the index needs no network.
- The image size grows by about 65 MB or less, compared with `docker images` before the change.
