#!/usr/bin/env python3.11
"""Check that every enabled provider in a fabro settings overlay resolves to the
openai Agent profile (ADR 0017).

    ops/check-agent-profiles.py <settings.toml>        # a file (the tracked template
                                                       # or a copy of the live overlay)
    docker exec fabro-fabro-1 cat /storage/.home/settings.toml \
      | ops/check-agent-profiles.py -                  # or stdin

Pebble runs each agent session on an Agent profile: a prompt, a tool set and a list of
instruction files. fabro picks it per model -- the model row's `metadata.agent.profile`,
then the provider's, then the profile the provider's first codec implies -- and only the
openai profile loads `<checkout>/.codex/instructions.md`, the file that carries the Agent
guide (docs/coder-tweaks C1). A provider on `anthropic-messages` or `gemini-generate`
therefore runs every one of its stages without the guide and with a different tool
vocabulary, silently. That is why this is a checked rule (AGENTS.md, *Deployment
invariants*) and not a comment.

Exit 0 = every enabled provider and model row resolves to `openai`. Exit 1 = offenders,
named on stdout. Exit 2 = bad usage or an unparseable document.

Output carries provider ids, model ids, codecs and profiles only -- never `auth`, keys,
or URLs. The live overlay holds credentials; run this against it freely.

Known limit: this reads the overlay alone. A provider row that carries no `codecs`, no
`adapter` and no `display_name` cannot be an operator-defined provider (fabro rejects a
provider table without `display_name`), so it only extends a lithos BUILT-IN -- and a
built-in's codec and profile are not in this file. Those rows print a NOTE. fabro exposes
no per-model profile anywhere a checker can read -- not `fabro model list --json`, not
`fabro doctor`, not `GET /settings` (checked on 0.362.0-nightly.0, 2026-10-08) -- so the
built-in rows are verified at run time instead, from the `profile` in the
`agent.memory.loaded` event of `fabro events --json`.
"""
import sys
import tomllib

# The vocabulary of `metadata.agent.profile`: the AgentProfileKind serde names.
# lithos' own test (`every_builtin_row_resolves_a_known_agent_profile`) checks
# built-in rows against this same list.
AGENT_PROFILES = ("anthropic", "claude-5", "openai", "gemini", "kimi", "gpt56", "gpt6")

# The profile a provider's first codec implies (fabro-llm `adapter_agent_profile`).
# Anything not listed -- `openai-chat` above all -- implies openai.
CODEC_PROFILES = {"anthropic-messages": "anthropic", "gemini-generate": "gemini"}

# An adapter is not a codec: `bedrock` speaks Converse to Anthropic-shaped models,
# so its provider implies anthropic whatever its codecs say.
BEDROCK_ADAPTER = "bedrock"

REQUIRED_PROFILE = "openai"


class Malformed(Exception):
    """A metadata table this checker cannot read, so it cannot vouch for the profile."""


def provider_enabled(row):
    """Providers are enabled unless the row says otherwise."""
    return bool(row.get("enabled", True))


def declared_profile(row, what):
    """The profile a catalog row names under `metadata.agent`, or None."""
    metadata = row.get("metadata")
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise Malformed(f"{what}: `metadata` is not a table")
    agent = metadata.get("agent")
    if agent is None:
        return None
    if not isinstance(agent, dict):
        raise Malformed(f"{what}: `metadata.agent` is not a table")
    profile = agent.get("profile")
    if profile is None:
        return None
    if not isinstance(profile, str):
        raise Malformed(f"{what}: `metadata.agent.profile` is not a string")
    if profile not in AGENT_PROFILES:
        raise Malformed(f"{what}: unknown agent profile {profile!r}")
    return profile


def first_codec(row):
    """The codec fabro sends a generation call on. 0.362 reads `codecs = [...]`;
    `codec` (a bare string) was the pre-0.362 spelling, and a cold start refuses it
    today, but read it anyway so a stale row is still judged rather than assumed safe.
    """
    codecs = row.get("codecs")
    if isinstance(codecs, list) and codecs:
        return str(codecs[0])
    legacy = row.get("codec")
    if isinstance(legacy, str) and legacy:
        return legacy
    return "openai-chat"          # the catalog default when a row names none


def implied_profile(row):
    if str(row.get("adapter", "")) == BEDROCK_ADAPTER:
        return "anthropic"
    return CODEC_PROFILES.get(first_codec(row), "openai")


def check_document(document):
    """Return (offenders, notes, malformed) for a parsed settings document.

    Each offender is a printable line naming the provider, the model row where the
    profile comes from, the resolved profile, its codecs and where the value came from.
    """
    offenders, notes, malformed = [], [], []
    llm = document.get("llm") or {}
    providers = llm.get("providers") or {}
    for provider_id in sorted(providers):
        prow = providers[provider_id]
        if not isinstance(prow, dict):
            malformed.append(f"{provider_id}: provider row is not a table")
            continue
        if not provider_enabled(prow):
            continue
        codec = first_codec(prow)
        try:
            provider_profile = declared_profile(prow, f"{provider_id} (provider)")
        except Malformed as exc:
            malformed.append(str(exc))
            continue
        source = "provider metadata.agent.profile" if provider_profile else f"implied by codec {codec}"
        resolved = provider_profile or implied_profile(prow)
        # A row with no display_name cannot be an operator-defined provider (fabro
        # rejects a provider table that omits it), so it only extends a lithos
        # BUILT-IN -- and a built-in's real codec and profile are not in this file.
        # A complete row that names no codec genuinely has the catalog default,
        # openai-chat, and needs no note.
        if not ({"codecs", "codec", "adapter", "metadata"} & set(prow)) and "display_name" not in prow:
            notes.append(
                f"NOTE {provider_id}: row extends a built-in provider; its real codec "
                f"and profile are not in this file, so verify at run time (agent.memory.loaded)"
            )
        if resolved != REQUIRED_PROFILE:
            offenders.append(
                f"FAIL {provider_id} (provider default): profile={resolved} codecs={codec} "
                f"source={source}"
            )
        models = prow.get("models") or {}
        if not isinstance(models, dict):
            malformed.append(f"{provider_id}: `models` is not a table")
            continue
        for model_id in sorted(models):
            mrow = models[model_id]
            if not isinstance(mrow, dict):
                malformed.append(f"{provider_id}.{model_id}: model row is not a table")
                continue
            try:
                model_profile = declared_profile(mrow, f"{provider_id}.{model_id} (model)")
            except Malformed as exc:
                malformed.append(str(exc))
                continue
            if model_profile is None:
                continue          # inherits the provider, already judged above
            if model_profile != REQUIRED_PROFILE:
                offenders.append(
                    f"FAIL {provider_id}/{model_id}: profile={model_profile} codecs={codec} "
                    f"source=model metadata.agent.profile"
                )
    return offenders, notes, malformed


def main(argv):
    if len(argv) != 2:
        print("usage: check-agent-profiles.py <settings.toml | ->", file=sys.stderr)
        return 2
    where = argv[1]
    try:
        if where == "-":
            document = tomllib.load(sys.stdin.buffer)
        else:
            with open(where, "rb") as handle:
                document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR cannot read {where}: {exc}", file=sys.stderr)
        return 2
    try:
        offenders, notes, malformed = check_document(document)
    except Malformed as exc:                      # defensive: check_document catches these
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    for line in notes:
        print(line)
    for line in malformed:
        print(f"FAIL {line}")
    for line in offenders:
        print(line)
    if malformed:
        print(f"malformed metadata on {len(malformed)} row(s); nothing about them can be trusted",
              file=sys.stderr)
        return 1
    if offenders:
        print(f"{len(offenders)} row(s) resolve a profile other than '{REQUIRED_PROFILE}' "
              f"(ADR 0017: every agent session runs the openai profile)", file=sys.stderr)
        return 1
    print(f"ok: every enabled provider in {where} resolves the {REQUIRED_PROFILE} Agent profile"
          + ("" if not notes else " (built-in extension rows: see NOTEs above)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
