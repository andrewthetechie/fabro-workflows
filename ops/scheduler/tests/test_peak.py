"""The peak-hours window (`peak.py`), its config key and its HTTP surface.

z.ai's window is the case that matters: Monday to Friday, 14:00-18:00 at UTC+8,
which is 06:00-10:00 UTC, with dispatch paused from 05:45 UTC. The edges are
asserted to the minute because "15 minutes before, resume right at the end" is
the operator's rule, not an approximation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fabro_scheduler.app import build_app
from fabro_scheduler.config import ConfigError, load_config
from fabro_scheduler.peak import PeakConfigError, parse_peak_hours
from fabro_scheduler.store import Store

TRACKED_REPOS = Path(__file__).resolve().parents[1] / "repos.toml"
ZAI = "mon-fri 14:00-18:00 +08:00"

# 2026-10-28 is a Wednesday; 2026-10-31 a Saturday.
WED = datetime(2026, 10, 28, tzinfo=UTC)


def at(day: datetime, hhmm: str) -> datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return day + timedelta(hours=hh, minutes=mm)


# --- the window --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hhmm", "in_block"),
    [
        ("05:44", False),
        ("05:45", True),  # 15 minutes before 06:00 UTC = 14:00 SGT
        ("06:00", True),
        ("09:59", True),
        ("10:00", False),  # resumes right at the end of the window
        ("22:00", False),
    ],
)
def test_zai_window_pauses_from_the_lead_and_resumes_at_the_close(hhmm, in_block):
    peak = parse_peak_hours(ZAI, 15)
    assert peak.status(at(WED, hhmm)).in_block is in_block


def test_the_weekend_is_never_peak():
    peak = parse_peak_hours(ZAI, 15)
    saturday = WED + timedelta(days=3)
    assert not peak.status(at(saturday, "07:00")).in_block
    # Monday's lead starts at 05:45 UTC Monday, not on Sunday.
    assert peak.status(at(saturday, "07:00")).next_block == at(
        saturday + timedelta(days=2), "05:45"
    )


def test_status_reports_until_and_next_block():
    peak = parse_peak_hours(ZAI, 15)
    inside = peak.status(at(WED, "07:00"))
    assert inside.until == at(WED, "10:00")
    after = peak.status(at(WED, "11:00"))
    assert after.next_block == at(WED + timedelta(days=1), "05:45")


def test_a_window_past_midnight_and_overlapping_windows_merge():
    peak = parse_peak_hours("mon-sun 22:00-02:00 +00:00; mon-sun 01:00-03:00 +00:00", 0)
    status = peak.status(at(WED, "23:30"))
    assert status.in_block
    assert status.until == at(WED + timedelta(days=1), "03:00")


def test_off_and_empty_disable_the_block():
    for spec in ("", "  ", "off", "OFF"):
        peak = parse_peak_hours(spec, 15)
        assert not peak.configured
        assert not peak.status(at(WED, "07:00")).in_block


@pytest.mark.parametrize(
    "spec",
    [
        "weekdays 14:00-18:00 +08:00",
        "mon-fri 14:00-18:00",
        "mon-fri 24:00-18:00 +08:00",
        "fri-mon 14:00-18:00 +08:00",
        "mon-fri 14:00-14:00 +08:00",
        "mon-fri 14:00-18:00 +15:00",
    ],
)
def test_a_bad_window_is_refused(spec):
    with pytest.raises(PeakConfigError):
        parse_peak_hours(spec, 15)


# --- the config keys ---------------------------------------------------------------


def test_config_is_off_unless_set():
    assert not load_config(TRACKED_REPOS, env={}).peak_hours.configured


def test_config_reads_the_window_and_lead():
    cfg = load_config(
        TRACKED_REPOS,
        env={"SCHEDULER_PEAK_HOURS": ZAI, "SCHEDULER_PEAK_LEAD_MINUTES": "30"},
    )
    assert cfg.peak_hours.lead == timedelta(minutes=30)
    assert cfg.peak_hours.status(at(WED, "05:31")).in_block


@pytest.mark.parametrize(
    ("env", "key"),
    [
        ({"SCHEDULER_PEAK_HOURS": "nonsense"}, "SCHEDULER_PEAK_HOURS"),
        ({"SCHEDULER_PEAK_HOURS": ZAI, "SCHEDULER_PEAK_LEAD_MINUTES": "-5"}, "SCHEDULER_PEAK_LEAD_MINUTES"),
        ({"SCHEDULER_PEAK_HOURS": ZAI, "SCHEDULER_PEAK_LEAD_MINUTES": "soon"}, "SCHEDULER_PEAK_LEAD_MINUTES"),
    ],
)
def test_a_bad_value_is_a_config_error_naming_the_key(env, key):
    with pytest.raises(ConfigError, match=key):
        load_config(TRACKED_REPOS, env=env)


# --- the override and the HTTP surface ---------------------------------------------


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store(tmp_path / "scheduler.db")
    yield opened
    opened.close()


def test_the_override_persists_across_a_reopen(tmp_path):
    first = Store(tmp_path / "scheduler.db")
    assert first.peak_override() is False
    first.set_peak_override(True)
    first.close()
    again = Store(tmp_path / "scheduler.db")
    assert again.peak_override() is True
    again.set_peak_override(False)
    assert again.peak_override() is False
    again.close()


def test_api_peak_and_the_two_buttons(store):
    every_minute = "mon-sun 00:00-23:59 +00:00"
    config = load_config(TRACKED_REPOS, env={"SCHEDULER_PEAK_HOURS": every_minute})
    client = TestClient(build_app(config, store, github_token="ghp_test"))

    body = client.get("/api/peak").json()
    assert body["configured"] is True
    assert body["blocking"] in (True, False)  # 23:59-00:00 UTC is the one open minute
    assert body["override"] is False

    on = client.post("/api/peak/override").json()
    assert on["override"] is True and on["blocking"] is False
    assert store.peak_override() is True
    assert client.get("/health").json()["peak_hours"]["override"] is True

    off = client.post("/api/peak/restore").json()
    assert off["override"] is False


def test_the_page_shows_the_block_and_the_button(store):
    config = load_config(TRACKED_REPOS, env={"SCHEDULER_PEAK_HOURS": ZAI})
    client = TestClient(build_app(config, store, github_token="ghp_test"))
    page = client.get("/").text
    assert "Peak hours" in page
    assert "/api/peak/override" in page
    store.set_peak_override(True)
    page = client.get("/").text
    assert "/api/peak/restore" in page
    assert "Restore peak-hours block" in page


def test_the_page_has_no_banner_when_off(store):
    client = TestClient(build_app(load_config(TRACKED_REPOS, env={}), store, github_token="ghp_test"))
    assert "/api/peak/override" not in client.get("/").text
