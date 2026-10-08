"""Tests for ops/check-agent-profiles.py (ADR 0017, issue #2).

    python3.11 -m unittest discover -s ops/tests

Every fixture is a small TOML with no real keys. The five cases the issue names are
`OpenaiChatPasses`, `AnthropicUnpinnedFails`, `ProviderPinPasses`, `ModelRowOverrideFails`
and `DisabledProviderIgnored`; the rest cover the resolution order, the bedrock adapter,
malformed metadata, and the rule that the output never carries a credential.
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cap", HERE.parent / "check-agent-profiles.py")
cap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cap)


def doc(providers_toml):
    """Wrap a `providers.*` fixture in the shape a real settings file has."""
    import tomllib
    return {"llm": {"providers": tomllib.loads(providers_toml)["providers"]}}


def check(providers_toml):
    return cap.check_document(doc(providers_toml))


def run_cli(path):
    """Run the CLI over a settings file, capturing both streams."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cap.main(["check-agent-profiles.py", str(path)])
    return rc, out.getvalue(), err.getvalue()


@contextmanager
def settings_file(text):
    path = Path(tempfile.mkdtemp()) / "settings.toml"
    path.write_text(text)
    try:
        yield path
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


# --- fixtures ----------------------------------------------------------------------------

OPENAI_CHAT = """
[providers.box-a]
display_name = "Local coder A"
base_url = "http://10.10.0.29:8000/v1"
auth = { type = "none" }

[providers.box-a.models."coders-a"]
display_name = "Local coders A"
api_model = "deepseek"
"""

ANTHROPIC_UNPINNED = """
[providers.kimi]
display_name = "Kimi (coding plan)"
base_url = "https://api.kimi.com/coding"
enabled = true
codecs = ["anthropic-messages"]
auth = { type = "bearer" }

[providers.kimi.models."kimi-k3"]
display_name = "Kimi K3"
api_model = "kimi-k3"
"""

ANTHROPIC_PINNED = """
[providers.kimi]
display_name = "Kimi (coding plan)"
base_url = "https://api.kimi.com/coding"
enabled = true
codecs = ["anthropic-messages"]
auth = { type = "bearer" }

# ADR 0017: one Agent profile for every session.
[providers.kimi.metadata.agent]
profile = "openai"

[providers.kimi.models."kimi-k3"]
display_name = "Kimi K3"
api_model = "kimi-k3"
"""

MODEL_ROW_OVERRIDE = """
[providers.kimi]
display_name = "Kimi (coding plan)"
codecs = ["anthropic-messages"]
enabled = true

[providers.kimi.metadata.agent]
profile = "openai"

[providers.kimi.models."kimi-k3"]
api_model = "kimi-k3"

[providers.kimi.models."kimi-k3".metadata.agent]
profile = "anthropic"
"""

DISABLED_ANTHROPIC = """
[providers.moonshot]
enabled = false
codecs = ["anthropic-messages"]

[providers.moonshot.models."kimi-k2.5"]
api_model = "kimi-k2.5"
"""

GEMINI_UNPINNED = """
[providers.gemini-host]
display_name = "Gemini host"
codecs = ["gemini-generate"]

[providers.gemini-host.models."gem-3"]
api_model = "gem-3"
"""

BEDROCK_ADAPTER = """
[providers.bedrock-ish]
display_name = "Bedrock-ish"
adapter = "bedrock"
codecs = ["openai-chat"]
"""

INLINE_PIN = """
[providers.kimi]
display_name = "Kimi (coding plan)"
codecs = ["anthropic-messages"]
metadata = { agent = { profile = "openai" } }

[providers.kimi.models."kimi-k3"]
api_model = "kimi-k3"
"""

BARE_BUILTIN_EXTENSION = """
[providers.zai.models."glm-5.3"]
display_name = "GLM 5.3 (z.ai coding plan)"
api_model = "glm-5.3"
"""

MALFORMED_AGENT_TABLE = """
[providers.kimi]
codecs = ["anthropic-messages"]

[providers.kimi.metadata]
agent = "openai"
"""

UNKNOWN_PROFILE = """
[providers.kimi]
codecs = ["anthropic-messages"]

[providers.kimi.metadata.agent]
profile = "claude"
"""


class OpenaiChatPasses(unittest.TestCase):
    """A box provider that names no codec at all defaults to openai-chat, so it passes."""

    def test_a_box_provider_with_no_pin_passes(self):
        offenders, notes, malformed = check(OPENAI_CHAT)
        self.assertEqual((offenders, malformed), ([], []))
        self.assertEqual(notes, [],
                         "a complete operator row has the catalog default codec, so it "
                         "is not an unverifiable built-in extension")


class AnthropicUnpinnedFails(unittest.TestCase):
    def test_the_codec_implied_anthropic_profile_is_an_offender(self):
        offenders, _, malformed = check(ANTHROPIC_UNPINNED)
        self.assertEqual(malformed, [])
        self.assertEqual(len(offenders), 1)
        self.assertIn("kimi (provider default)", offenders[0])
        self.assertIn("profile=anthropic", offenders[0])
        self.assertIn("anthropic-messages", offenders[0])

    def test_the_model_row_is_not_also_blamed_for_the_provider_default(self):
        """Every kimi-k3 session runs the provider's profile; naming the provider once
        is the finding. A per-row list of 49 stage sessions would be noise."""
        offenders, _, _ = check(ANTHROPIC_UNPINNED)
        self.assertEqual([line for line in offenders if "kimi-k3" in line], [])


class ProviderPinPasses(unittest.TestCase):
    def test_a_provider_pin_covers_every_model_under_it(self):
        offenders, notes, malformed = check(ANTHROPIC_PINNED)
        self.assertEqual((offenders, malformed), ([], []))
        self.assertEqual(notes, [], "a full provider row is not a bare built-in extension")

    def test_the_inline_table_spelling_is_accepted_too(self):
        offenders, _, malformed = check(INLINE_PIN)
        self.assertEqual((offenders, malformed), ([], []))

    def test_a_pin_covers_a_model_added_later(self):
        """The point of pinning at the provider (decision 2): a new row needs no pin."""
        text = ANTHROPIC_PINNED + '\n[providers.kimi.models."kimi-k4"]\napi_model = "kimi-k4"\n'
        offenders, _, malformed = check(text)
        self.assertEqual((offenders, malformed), ([], []))


class ModelRowOverrideFails(unittest.TestCase):
    def test_a_model_row_beats_the_provider_pin(self):
        """fabro reads the model row first (catalog.rs `model_agent_profile`), so a row
        that names anthropic runs anthropic whatever the provider says."""
        offenders, _, malformed = check(MODEL_ROW_OVERRIDE)
        self.assertEqual(malformed, [])
        self.assertEqual(len(offenders), 1)
        self.assertIn("kimi/kimi-k3", offenders[0])
        self.assertIn("source=model metadata.agent.profile", offenders[0])


class DisabledProviderIgnored(unittest.TestCase):
    def test_disabled_providers_are_not_offenders(self):
        offenders, notes, malformed = check(DISABLED_ANTHROPIC)
        self.assertEqual((offenders, notes, malformed), ([], [], []))

    def test_a_disabled_provider_needs_no_pin(self):
        """`[llm.providers.moonshot] enabled = false` is load-bearing in the live
        overlay (it keeps the built-in from winning `kimi-k3`), and moonshot's own
        profile is kimi. A checker that failed on it would be unfixable."""
        text = '[providers.moonshot]\nenabled = false\ncodecs = ["anthropic-messages"]\n'
        self.assertEqual(check(text)[0], [])


class OtherResolutionRules(unittest.TestCase):
    def test_gemini_generate_implies_gemini(self):
        offenders, _, _ = check(GEMINI_UNPINNED)
        self.assertEqual(len(offenders), 1)
        self.assertIn("profile=gemini", offenders[0])

    def test_the_bedrock_adapter_beats_its_codec_list(self):
        offenders, _, _ = check(BEDROCK_ADAPTER)
        self.assertEqual(len(offenders), 1)
        self.assertIn("profile=anthropic", offenders[0])

    def test_a_second_codec_does_not_change_the_answer(self):
        """The implied profile follows the FIRST codec, because that is the one a
        generation call goes out on."""
        text = ANTHROPIC_UNPINNED.replace('["anthropic-messages"]', '["openai-chat", "anthropic-messages"]')
        self.assertEqual(check(text)[0], [])

    def test_a_pin_on_a_model_alone_is_enough_for_that_model(self):
        text = ANTHROPIC_UNPINNED + '\n[providers.kimi.models."kimi-k3".metadata.agent]\nprofile = "openai"\n'
        offenders, _, malformed = check(text)
        self.assertEqual(malformed, [])
        self.assertEqual(len(offenders), 1, "the provider default still fails")
        self.assertIn("kimi (provider default)", offenders[0])

    def test_a_bare_built_in_extension_row_is_a_note_not_an_offender(self):
        offenders, notes, malformed = check(BARE_BUILTIN_EXTENSION)
        self.assertEqual((offenders, malformed), ([], []))
        self.assertEqual(len(notes), 1)
        self.assertIn("zai", notes[0])

    def test_an_empty_overlay_passes(self):
        self.assertEqual(cap.check_document({})[0], [])


class MalformedMetadata(unittest.TestCase):
    def test_a_metadata_namespace_that_is_not_a_table_fails(self):
        offenders, _, malformed = check(MALFORMED_AGENT_TABLE)
        self.assertEqual(offenders, [])
        self.assertEqual(len(malformed), 1)
        self.assertIn("kimi", malformed[0])

    def test_an_unknown_profile_name_fails(self):
        """`claude` is not an AgentProfileKind serde name; `claude-5` is. An unknown
        string is a build failure in pebble, not a fallback."""
        offenders, _, malformed = check(UNKNOWN_PROFILE)
        self.assertEqual(offenders, [])
        self.assertEqual(len(malformed), 1)
        self.assertIn("unknown agent profile", malformed[0])

    def test_malformed_metadata_exits_nonzero_from_the_cli(self):
        with settings_file("[llm.providers.kimi.metadata]\nagent = \"openai\"\n") as path:
            rc, out, err = run_cli(path)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out + err)


class OutputCarriesNoCredentials(unittest.TestCase):
    def test_auth_values_and_urls_never_appear_in_the_output(self):
        text = (ANTHROPIC_UNPINNED
                .replace('auth = { type = "bearer" }',
                         'auth = { type = "bearer", key = "SECRET-XYZ" }')
                .replace("https://api.kimi.com/coding", "https://user:pass@api.kimi.com/coding"))
        with settings_file("[llm]" + text.replace("\n[providers", "\n[llm.providers")) as path:
            rc, out, err = run_cli(path)
        self.assertEqual(rc, 1)
        combined = out + err
        for secret in ("SECRET-XYZ", "user:pass", "api.kimi.com", "base_url"):
            self.assertNotIn(secret, combined, combined)


class CommandLine(unittest.TestCase):
    def test_the_tracked_template_passes(self):
        example = HERE.parent / "settings.toml.example"
        rc, out, err = run_cli(example)
        self.assertEqual(rc, 0, out + err)

    def test_the_live_shape_of_the_template_is_flagged_when_the_pin_is_removed(self):
        example = HERE.parent / "settings.toml.example"
        text = example.read_text().replace('profile = "openai"', 'profile = "anthropic"')
        with settings_file(text) as path:
            rc, out, err = run_cli(path)
        self.assertEqual(rc, 1)
        self.assertIn("kimi", out)

    def test_stdin_is_accepted(self):
        body = ANTHROPIC_UNPINNED.replace("[providers.", "[llm.providers.")
        proc = subprocess.run(
            [sys.executable, str(HERE.parent / "check-agent-profiles.py"), "-"],
            input=body, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("kimi", proc.stdout)

    def test_a_missing_file_is_two_not_one(self):
        """A checker that cannot read its input must not look like a clean verdict."""
        rc, _, err = run_cli(Path("/nonexistent/settings.toml"))
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err)

    def test_usage_error_is_two(self):
        rc, _, err = run_cli_no_args()
        self.assertEqual(rc, 2)
        self.assertIn("usage", err)


def run_cli_no_args():
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cap.main(["check-agent-profiles.py"])
    return rc, out.getvalue(), err.getvalue()


if __name__ == "__main__":
    unittest.main()
