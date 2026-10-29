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


# --- aliases --------------------------------------------------------------------


def test_aliases_default_to_none(tmp_path):
    cfg = load_config(write(tmp_path, repo()), env={})
    assert cfg.repos[0].aliases == ()


def test_aliases_are_read(tmp_path):
    cfg = load_config(write(tmp_path, repo() + 'aliases = ["wfs", "w.f-s_2"]\n'), env={})
    assert cfg.repos[0].aliases == ("wfs", "w.f-s_2")


@pytest.mark.parametrize("value", ['"wfs"', '["w fs"]', '[""]', "[1]"])
def test_aliases_reject_anything_but_a_list_of_short_names(tmp_path, value):
    p = write(tmp_path, repo() + f"aliases = {value}\n")
    with pytest.raises(ConfigError, match=r"repo\[0\]\.aliases"):
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
    assert cfg.coder_pools == ("coders-a", "coders-b", "coders-c", "coders-d")
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
        "andrewthetechie/flakeradar",  # priority 40
        "andrewthetechie/jelly-swipe",  # priority 99
    ]
    assert {(r.name, r.environment_id) for r in cfg.repos} == {
        ("andrewthetechie/jelly-swipe", "python"),
        ("andrewthetechie/lawncare-saas", "python-node"),
        ("andrewthetechie/womens-fantasy-sports", "ts"),
        ("andrewthetechie/writers-app", "rust-node"),
        ("andrewthetechie/flakeradar", "python-node"),
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


# --- the ralph trial's share (ADR 0029 O21) ----------------------------------------


def test_the_ralph_share_defaults_to_zero_and_reads_fifty(tmp_path):
    """0 is the kill switch and the default: a host that has never heard of the
    trial behaves exactly as it did before it."""
    cfg = load_config(write(tmp_path, repo()), env={})
    assert cfg.ralph_share == 0
    assert load_config(
        write(tmp_path, repo()), env={"SCHEDULER_RALPH_SHARE": "50"}
    ).ralph_share == 50
    assert load_config(
        write(tmp_path, repo()), env={"SCHEDULER_RALPH_SHARE": " 0 "}
    ).ralph_share == 0


@pytest.mark.parametrize("raw", ["25", "yes", "-1", "100", "50.0"])
def test_a_ralph_share_the_trial_never_validated_names_the_key(tmp_path, raw):
    """Only 0 and 50 exist. A value in between would change what the two arms are
    compared on, and nothing in a run's history would say which share produced it.
    """
    with pytest.raises(ConfigError, match="SCHEDULER_RALPH_SHARE"):
        load_config(write(tmp_path, repo()), env={"SCHEDULER_RALPH_SHARE": raw})


# --- the background lane (more-local-inference C7, D12) -----------------------------


def test_the_background_lane_is_off_by_default_and_reads_on_and_off(tmp_path):
    """Off in code as well as in ops/docker-compose.yaml (D12): a host that has never
    heard of the lane must behave exactly as it did before it."""
    cfg = load_config(write(tmp_path, repo()), env={})
    assert cfg.background is False
    assert cfg.bg_gap_seconds == 30
    assert cfg.bg_slot_reserve == 3
    assert cfg.box_health == {}
    assert cfg.max_concurrent_runs == 8
    # Every kind the lane knows, in D10's value order (task 06 added `pr-doctor` at 1).
    assert cfg.bg_kinds == ("pr-doctor", "box-noop")
    # The doctor's daily review cap (operator decision 3): 6 per UTC day, on by default.
    assert cfg.pr_doctor_reviews_per_day == 6

    on = load_config(write(tmp_path, repo()), env={"SCHEDULER_BACKGROUND": " ON "})
    assert on.background is True
    off = load_config(write(tmp_path, repo()), env={"SCHEDULER_BACKGROUND": "off"})
    assert off.background is False


@pytest.mark.parametrize("raw", ["yes", "true", "1", "enabled"])
def test_an_unparsable_background_switch_names_the_key(tmp_path, raw):
    """A typo must not read as "the operator turned the lane off", which is the one
    answer they would believe."""
    with pytest.raises(ConfigError, match="SCHEDULER_BACKGROUND"):
        load_config(write(tmp_path, repo()), env={"SCHEDULER_BACKGROUND": raw})


def test_box_health_is_parsed_in_the_format_the_claim_hook_reads(tmp_path):
    cfg = load_config(
        write(tmp_path, repo()),
        env={
            "SCHEDULER_BOX_HEALTH": (
                " coders-a=http://10.10.0.29:5300/health , "
                "coders-b = http://10.10.0.56:5300/health , "
            )
        },
    )
    assert cfg.box_health == {
        "coders-a": "http://10.10.0.29:5300/health",
        "coders-b": "http://10.10.0.56:5300/health",
    }


def test_the_lane_knobs_read_and_reject_their_bounds(tmp_path):
    cfg = load_config(
        write(tmp_path, repo()),
        env={
            "SCHEDULER_BG_GAP_S": "0",
            "SCHEDULER_BG_SLOT_RESERVE": "0",
            "SCHEDULER_MAX_CONCURRENT_RUNS": "4",
            "SCHEDULER_BG_KINDS": "box-noop",
        },
    )
    assert (cfg.bg_gap_seconds, cfg.bg_slot_reserve, cfg.max_concurrent_runs) == (0, 0, 4)
    for key, value in (
        ("SCHEDULER_BG_GAP_S", "-1"),
        ("SCHEDULER_BG_SLOT_RESERVE", "-2"),
        ("SCHEDULER_MAX_CONCURRENT_RUNS", "0"),
        # A kind that matches no producer would silently turn nothing on, now that
        # `pr-doctor` is a real kind.
        ("SCHEDULER_BG_KINDS", "pr-docotr"),
        # The doctor's cap is `>= 0`: 0 is the reviews' kill switch, a negative is a typo.
        ("SCHEDULER_PR_DOCTOR_REVIEWS_PER_DAY", "-1"),
    ):
        with pytest.raises(ConfigError, match=key):
            load_config(write(tmp_path, repo()), env={key: value})


def test_the_gap_table_predicts_a_gap_and_zero_for_an_unknown_key(tmp_path):
    """C7 step 9: the table is measured (task 12 Phase 0 step 2), and an unknown key
    predicts 0, so no job is committed to a gap nobody has measured."""
    from fabro_scheduler.config import BACKGROUND_GAP_MINUTES, predicted_gap_minutes

    # Measured rows: the hosted review after a task leaves about nine minutes, and the
    # merge phase's reviewers about twenty, before the next box stage.
    assert predicted_gap_minutes("backlog", "review") == 9
    assert predicted_gap_minutes("backlog", "review_merge.standards") == 19
    assert predicted_gap_minutes("ralph", "refute") == 10
    # Measured from a visit's end, a CI watch is short (the run usually ends after it), so
    # it is not in the table and predicts 0, the safe direction.
    assert ("backlog", "review_merge.watch_checks") not in BACKGROUND_GAP_MINUTES
    assert predicted_gap_minutes("backlog", "review_merge.watch_checks") == 0
    # `pr-review` and any node the table has never seen predict 0.
    assert predicted_gap_minutes("pr-review", "review") == 0
    assert predicted_gap_minutes("backlog", "not-a-node") == 0
    # No row is a box stage (C1): a gap is what follows a hosted or command node.
    box = {"coder", "rework_t1", "resolve_merge", "extra_decompose", "plan",
           "review_merge.refute_fix", "review_merge.resolve_base",
           "iterate_t1", "learn", "resolve_base"}
    assert not [key for key in BACKGROUND_GAP_MINUTES if key[1] in box]
