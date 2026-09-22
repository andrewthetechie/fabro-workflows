"""`load_config` is pure, so every case here writes a file to tmp_path.

The environment is always passed explicitly. That is not ceremony: `FABRO_API_URL`
and `SCHEDULER_PORT` are ordinary variables an operator may well have exported,
and a test that reads `os.environ` would pass or fail depending on whose shell
ran it.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from fabro_scheduler.config import (
    DEFAULT_CODER_POOLS,
    DEFAULT_DB_PATH,
    DEFAULT_FABRO_API_URL,
    DEFAULT_GITHUB_POLL_SECONDS,
    DEFAULT_PORT,
    DEFAULT_STARVATION_CEILING_SECONDS,
    ConfigError,
    load_config,
)

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "repos.toml"
    path.write_text(body)
    return path


def repo(
    name: str = "o/a",
    priority: str = "0",
    environment_id: str = "python",
    *,
    enabled: str | None = None,
) -> str:
    body = (
        f'[[repo]]\nname = "{name}"\npriority = {priority}\n'
        f'environment_id = "{environment_id}"\n'
    )
    if enabled is not None:
        body += f"enabled = {enabled}\n"
    return body


# --- the two cases the task names verbatim ---------------------------------------


def test_priority_accepts_negative_and_orders_smallest_first(tmp_path):
    p = tmp_path / "repos.toml"
    p.write_text(
        '[[repo]]\nname="o/late"\npriority=7\nenvironment_id="python"\n'
        '[[repo]]\nname="o/first"\npriority=-99\nenvironment_id="ts"\n'
    )
    cfg = load_config(p, env={})
    assert [r.name for r in sorted(cfg.repos, key=lambda r: (r.priority, r.name))] == [
        "o/first",
        "o/late",
    ]


def test_duplicate_repo_name_is_an_error(tmp_path):
    p = tmp_path / "repos.toml"
    p.write_text(
        '[[repo]]\nname="o/a"\npriority=0\nenvironment_id="python"\n'
        '[[repo]]\nname="o/a"\npriority=1\nenvironment_id="ts"\n'
    )
    with pytest.raises(ConfigError, match="o/a"):
        load_config(p, env={})


# --- priority --------------------------------------------------------------------


def test_missing_priority_is_an_error(tmp_path):
    p = write(tmp_path, '[[repo]]\nname = "o/a"\nenvironment_id = "python"\n')
    with pytest.raises(ConfigError, match=r"repo\[0\]\.priority"):
        load_config(p, env={})


def test_priority_rejects_a_non_integer(tmp_path):
    p = write(tmp_path, repo(priority='"high"'))
    with pytest.raises(ConfigError, match=r"repo\[0\]\.priority"):
        load_config(p, env={})


def test_priority_rejects_a_boolean(tmp_path):
    # `bool` is a subclass of `int`, so this would otherwise parse as 1 and sort
    # the repo ahead of every priority-2 repo.
    p = write(tmp_path, repo(priority="true"))
    with pytest.raises(ConfigError, match=r"repo\[0\]\.priority"):
        load_config(p, env={})


def test_zero_and_negative_priorities_are_kept_not_clamped(tmp_path):
    p = write(tmp_path, repo(name="o/zero", priority="0") + repo(name="o/neg", priority="-3"))
    cfg = load_config(p, env={})
    assert {(r.name, r.priority) for r in cfg.repos} == {
        ("o/zero", 0),
        ("o/neg", -3),
    }


# --- names ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["jelly-swipe", "owner/", "/repo", "owner/repo/extra", "", "own er/repo"],
)
def test_name_must_be_owner_slash_repo(tmp_path, name):
    p = write(tmp_path, repo(name=name))
    with pytest.raises(ConfigError, match=r"repo\[0\]\.name"):
        load_config(p, env={})


def test_duplicate_names_differing_only_by_case_are_an_error(tmp_path):
    # GitHub resolves both spellings to one repository, so these are not two repos
    # — they are one repo with two queue entries, and each would be allowed its own
    # in-flight run against the rule that there is at most one per repo.
    body = repo(name="o/a") + repo(name="O/A")
    with pytest.raises(ConfigError, match=r"repo\[1\]\.name"):
        load_config(write(tmp_path, body), env={})


def test_a_case_duplicate_names_both_spellings(tmp_path):
    # The operator has to find two lines that do not match on sight, so the message
    # carries the other row's spelling as well as its index.
    body = repo(name="o/a") + repo(name="O/A")
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, body), env={})
    assert "'O/A'" in str(caught.value)
    assert "'o/a'" in str(caught.value)
    assert "repo[0]" in str(caught.value)


def test_an_exact_duplicate_does_not_claim_a_different_spelling(tmp_path):
    body = repo(name="o/a") + repo(name="o/a")
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, body), env={})
    assert "spelled" not in str(caught.value)


def test_a_repo_name_is_stored_as_the_operator_wrote_it(tmp_path):
    # Case folding is for the duplicate check only: this name is what the GitHub
    # API gets called with.
    cfg = load_config(write(tmp_path, repo(name="AndrewTheTechie/Jelly-Swipe")), env={})
    assert cfg.repos[0].name == "AndrewTheTechie/Jelly-Swipe"


# --- enabled --------------------------------------------------------------------


def test_enabled_defaults_to_true(tmp_path):
    cfg = load_config(write(tmp_path, repo()), env={})
    assert cfg.repos[0].enabled is True


def test_disabled_row_is_kept_not_dropped(tmp_path):
    cfg = load_config(write(tmp_path, repo(enabled="false")), env={})
    assert len(cfg.repos) == 1
    assert cfg.repos[0].enabled is False


def test_enabled_rejects_a_non_boolean(tmp_path):
    p = write(tmp_path, repo(enabled='"no"'))
    with pytest.raises(ConfigError, match=r"repo\[0\]\.enabled"):
        load_config(p, env={})


# --- the file as a whole --------------------------------------------------------


def test_unknown_key_is_an_error(tmp_path):
    # A typo'd key would otherwise be ignored and the row would silently take the
    # default, which for `priority` does not exist and for `enabled` is true.
    p = write(tmp_path, repo() + "priorty = 3\n")
    with pytest.raises(ConfigError, match="priorty"):
        load_config(p, env={})


def test_no_repo_blocks_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="repo"):
        load_config(write(tmp_path, "# nothing here\n"), env={})


def test_invalid_toml_is_a_config_error_not_a_toml_error(tmp_path):
    p = write(tmp_path, "[[repo]]\nname = \n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(p, env={})


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="no such file"):
        load_config(tmp_path / "absent.toml", env={})


def test_environment_id_must_be_a_non_empty_string(tmp_path):
    p = write(tmp_path, '[[repo]]\nname = "o/a"\npriority = 0\nenvironment_id = "  "\n')
    with pytest.raises(ConfigError, match="environment_id"):
        load_config(p, env={})


# --- ordering -------------------------------------------------------------------


def test_ordered_repos_sorts_by_priority_then_name(tmp_path):
    body = (
        repo(name="o/zeta", priority="1")
        + repo(name="o/alpha", priority="1")
        + repo(name="o/last", priority=9)
        + repo(name="o/first", priority="-1")
    )
    cfg = load_config(write(tmp_path, body), env={})
    assert [r.name for r in cfg.ordered_repos()] == [
        "o/first",
        "o/alpha",
        "o/zeta",
        "o/last",
    ]


def test_ordered_repos_keeps_disabled_rows(tmp_path):
    body = repo(name="o/on", priority="0") + repo(name="o/off", priority="1", enabled="false")
    cfg = load_config(write(tmp_path, body), env={})
    assert [r.name for r in cfg.ordered_repos()] == ["o/on", "o/off"]


def test_schedulable_repos_drops_disabled_rows(tmp_path):
    # The one difference from ordered_repos(), and the reason both exist: whatever
    # picks the next run reads this, so `enabled = false` actually takes the repo
    # out of scheduling rather than only out of the report.
    body = repo(name="o/on", priority="0") + repo(name="o/off", priority="1", enabled="false")
    cfg = load_config(write(tmp_path, body), env={})
    assert [r.name for r in cfg.schedulable_repos()] == ["o/on"]


def test_schedulable_repos_keeps_the_scheduling_order(tmp_path):
    body = (
        repo(name="o/zeta", priority="1")
        + repo(name="o/nope", priority="-5", enabled="false")
        + repo(name="o/alpha", priority="1")
        + repo(name="o/first", priority="-1")
    )
    cfg = load_config(write(tmp_path, body), env={})
    assert [r.name for r in cfg.schedulable_repos()] == ["o/first", "o/alpha", "o/zeta"]


def test_schedulable_repos_can_be_empty_without_being_an_error(tmp_path):
    # Every repo disabled is a deliberate operator state — a paused factory — not a
    # broken file. It is an empty *work list* that load_config refuses, not this.
    body = repo(name="o/off", priority="0", enabled="false")
    cfg = load_config(write(tmp_path, body), env={})
    assert cfg.schedulable_repos() == []
    assert len(cfg.repos) == 1


def test_repos_keep_the_order_they_were_written_in(tmp_path):
    # ordered_repos() is the scheduling order; `repos` is what the operator wrote,
    # so reading the config back shows the file rather than a reshuffle of it.
    body = repo(name="o/zeta", priority="1") + repo(name="o/alpha", priority="0")
    cfg = load_config(write(tmp_path, body), env={})
    assert [r.name for r in cfg.repos] == ["o/zeta", "o/alpha"]


# --- the deployment-shaped half, from the environment ---------------------------


def test_defaults_when_the_environment_is_empty(tmp_path):
    cfg = load_config(write(tmp_path, repo()), env={})
    assert cfg.coder_pools == DEFAULT_CODER_POOLS
    assert cfg.coder_pools == ("coders-a", "coders-b")
    assert cfg.fabro_api_url == DEFAULT_FABRO_API_URL
    assert cfg.port == DEFAULT_PORT == 32280
    assert cfg.db_path == Path(DEFAULT_DB_PATH) == Path("/data/scheduler.db")
    assert cfg.starvation_ceiling == timedelta(hours=4)
    assert cfg.starvation_ceiling.total_seconds() == DEFAULT_STARVATION_CEILING_SECONDS
    assert cfg.github_poll_seconds == DEFAULT_GITHUB_POLL_SECONDS == 60


def test_the_queue_knobs_come_from_the_environment(tmp_path):
    cfg = load_config(
        write(tmp_path, repo()),
        env={
            "SCHEDULER_DB": "/tmp/elsewhere.db",
            "SCHEDULER_CEILING_SECONDS": "600",
            "SCHEDULER_GITHUB_POLL_SECONDS": "5",
        },
    )
    assert cfg.db_path == Path("/tmp/elsewhere.db")
    assert cfg.starvation_ceiling == timedelta(minutes=10)
    assert cfg.github_poll_seconds == 5


@pytest.mark.parametrize("key", ["SCHEDULER_CEILING_SECONDS", "SCHEDULER_GITHUB_POLL_SECONDS"])
@pytest.mark.parametrize("raw", ["not-a-number", "0", "-1", "60.5"])
def test_a_non_positive_or_unparsable_interval_names_the_key(tmp_path, key, raw):
    # Zero is refused rather than clamped: a zero-second poll would spend the rate
    # limit in minutes, and a zero-second ceiling would put every item in the
    # starved tier and make repo priority dead configuration.
    with pytest.raises(ConfigError, match=key):
        load_config(write(tmp_path, repo()), env={key: raw})


def test_a_blank_db_path_falls_back_to_the_default(tmp_path):
    cfg = load_config(write(tmp_path, repo()), env={"SCHEDULER_DB": "   "})
    assert cfg.db_path == Path(DEFAULT_DB_PATH)


def test_the_config_carries_no_credentials(tmp_path):
    # A deliberate design rule, not an accident: a config object that cannot hold
    # a secret is one fewer thing to audit for a leak into /health or a log line.
    cfg = load_config(
        write(tmp_path, repo()),
        env={"GITHUB_TOKEN": "ghp-do-not-echo", "FABRO_API_TOKEN": "tok-do-not-echo"},
    )
    assert "ghp-do-not-echo" not in repr(cfg)
    assert "tok-do-not-echo" not in repr(cfg)


def test_fabro_api_url_and_port_come_from_the_environment(tmp_path):
    cfg = load_config(
        write(tmp_path, repo()),
        env={"FABRO_API_URL": "http://fabro.example:1/api/v1", "SCHEDULER_PORT": "9000"},
    )
    assert cfg.fabro_api_url == "http://fabro.example:1/api/v1"
    assert cfg.port == 9000


@pytest.mark.parametrize("raw", ["not-a-port", "0", "-1", "65536", "80.5"])
def test_a_bad_port_names_the_key(tmp_path, raw):
    with pytest.raises(ConfigError, match="SCHEDULER_PORT"):
        load_config(write(tmp_path, repo()), env={"SCHEDULER_PORT": raw})


def test_a_blank_environment_value_falls_back_to_the_default(tmp_path):
    cfg = load_config(
        write(tmp_path, repo()), env={"FABRO_API_URL": "   ", "SCHEDULER_PORT": " "}
    )
    assert cfg.fabro_api_url == DEFAULT_FABRO_API_URL
    assert cfg.port == DEFAULT_PORT


# --- the tracked file -----------------------------------------------------------


def test_the_tracked_repos_toml_parses(tmp_path):
    # The file this repository actually ships. A typo in it is a deploy that
    # crash-loops, so it is asserted here rather than only on the host.
    cfg = load_config(TRACKED_REPOS, env={})
    assert [r.name for r in cfg.ordered_repos()] == [
        "andrewthetechie/womens-fantasy-sports",  # priority 10
        "andrewthetechie/writers-app",  # priority 20
        "andrewthetechie/lawncare-saas",  # priority 30
        "andrewthetechie/jelly-swipe",  # priority 99
    ]
    assert {(r.name, r.environment_id) for r in cfg.repos} == {
        ("andrewthetechie/jelly-swipe", "python"),
        ("andrewthetechie/lawncare-saas", "python-node"),
        ("andrewthetechie/womens-fantasy-sports", "ts"),
        ("andrewthetechie/writers-app", "rust-node"),
    }
    assert all(r.enabled for r in cfg.repos)


# --- repo lookup ----------------------------------------------------------------


def test_repo_named_folds_case_and_reports_a_disabled_row(tmp_path):
    # The lookup that `POST /api/dispatch-once` uses. It matches the way duplicates
    # are detected — case-insensitively — because GitHub resolves `o/Repo` and
    # `o/repo` to one repository, so asking for the other spelling means this row.
    cfg = load_config(
        write(
            tmp_path,
            '[[repo]]\nname = "o/Repo"\npriority = 0\nenvironment_id = "python"\n'
            '[[repo]]\nname = "o/off"\npriority = 1\nenvironment_id = "ts"\n'
            "enabled = false\n",
        ),
        env={},
    )

    assert cfg.repo_named("o/repo").name == "o/Repo"
    assert cfg.repo_named("  O/REPO  ").name == "o/Repo"
    # `enabled` is reported on the row, not filtered here, so the caller can tell
    # "not configured" from "configured but switched off".
    assert cfg.repo_named("o/off").enabled is False
    assert cfg.repo_named("o/nope") is None
