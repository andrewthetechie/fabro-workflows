# fabro-ts:local — sandbox profile image for the `ts` environment
# (used by the backlog-womens-fantasy-sports automation).
#
# Bun is the runtime and package manager; node is provided by bun's
# node-fallback symlink in the base image.
#
# Reconstructed 2026-09-14 from `docker history --no-trunc fabro-ts:local`.
#
#   docker build -f Dockerfile.ts -t fabro-ts:local .

FROM oven/bun:1.4.2

RUN apt-get update && apt-get install -y --no-install-recommends git jq curl \
  && rm -rf /var/lib/apt/lists/* \
  && curl -fsSL -o /tmp/gh.tgz https://github.com/cli/cli/releases/download/v2.100.0/gh_2.100.0_linux_amd64.tar.gz \
  && tar -xzf /tmp/gh.tgz -C /usr/local --strip-components=1 && rm /tmp/gh.tgz \
  && gh --version
