"""The provider's peak hours, during which the dispatch loop starts no new run.

z.ai bills the GLM Coding Plan in credits, and off-peak usage costs 50% of the
standard rate. Peak is Monday to Friday, 14:00-18:00 Singapore Standard Time
(UTC+8), which is 06:00-10:00 UTC; Singapore keeps no daylight saving, so the
window never moves against UTC (docs.z.ai/devpack/overview, read 2026-10-28). The
week to 2026-10-28 spent 24% of its credits in that window at twice the price.

So the scheduler stops *dispatching* `lead` minutes before a window opens and
resumes the moment it closes. A run already on a box is never touched: it keeps
running, and its hosted stages pay peak rates for as long as they last. The lead
is what keeps a run dispatched at 05:59 UTC from spending its first hosted stages
inside the window.

The window is configuration, not code, because the provider can change it:
`SCHEDULER_PEAK_HOURS` holds one or more windows separated by `;`, each

    <days> <HH:MM>-<HH:MM> <+HH:MM | -HH:MM>

where `<days>` is a comma list of `mon`..`sun` names or `a-b` ranges (`mon-fri`,
`sat,sun`, `mon,wed-fri`), the times are local to the offset, and the offset is a
fixed UTC offset. A fixed offset rather than a tz name, because the runtime image
carries no tz database and the provider publishes the window as `UTC+8`. An end
at or before the start runs past midnight into the next day. Unset, empty or `off`
disables the block.

Everything here is pure: `now` is always a parameter, so the tests and the page
can ask about any instant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_TIME = r"([01]\d|2[0-3]):([0-5]\d)"
_WINDOW = re.compile(
    rf"^(?P<days>[a-z,\-]+)\s+{_TIME}-{_TIME}\s+(?P<sign>[+-])(?P<oh>\d\d):(?P<om>\d\d)$"
)


class PeakConfigError(ValueError):
    """A `SCHEDULER_PEAK_HOURS` value that cannot be used."""


@dataclass(frozen=True)
class PeakWindow:
    """One recurring window: the weekdays it opens on, local start and end, offset."""

    days: frozenset[int]  # 0 = Monday, as `date.weekday()`
    start: time
    end: time
    offset: timezone

    def spans(self, around: datetime) -> list[tuple[datetime, datetime]]:
        """Every opening of this window that could touch the 9 days around `around`.

        Starts at `around`'s local date minus one, so a window that opened yesterday
        and runs past midnight is included.
        """
        local = around.astimezone(self.offset).date()
        out: list[tuple[datetime, datetime]] = []
        for delta in range(-1, 8):
            day = local + timedelta(days=delta)
            if day.weekday() not in self.days:
                continue
            opens = _at(day, self.start, self.offset)
            closes = _at(day, self.end, self.offset)
            if closes <= opens:
                closes += timedelta(days=1)
            out.append((opens, closes))
        return out


@dataclass(frozen=True)
class PeakHours:
    """The configured windows and the dispatch lead. Empty `windows` means off."""

    windows: tuple[PeakWindow, ...] = ()
    lead: timedelta = timedelta(minutes=15)
    spec: str = ""  # as configured, for the page and /health

    @property
    def configured(self) -> bool:
        return bool(self.windows)

    def blocked_spans(self, now: datetime) -> list[tuple[datetime, datetime]]:
        """The dispatch blocks near `now`, each `(window start - lead, window end)`, sorted."""
        spans = [
            (opens - self.lead, closes)
            for window in self.windows
            for opens, closes in window.spans(now)
        ]
        return sorted(spans)

    def status(self, now: datetime) -> PeakStatus:
        """Whether `now` is inside a block, and the next boundary either way.

        Overlapping or touching blocks are merged, so `until` is the moment dispatch
        can actually resume, not the end of the first of two adjacent windows.
        """
        if not self.windows:
            return PeakStatus(in_block=False, until=None, next_block=None)
        merged: list[tuple[datetime, datetime]] = []
        for start, end in self.blocked_spans(now):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        for start, end in merged:
            if start <= now < end:
                return PeakStatus(in_block=True, until=end, next_block=None)
            if start > now:
                return PeakStatus(in_block=False, until=None, next_block=start)
        return PeakStatus(in_block=False, until=None, next_block=None)


@dataclass(frozen=True)
class PeakStatus:
    """`in_block`: dispatch is paused now (before any override). `until`: when that
    block ends. `next_block`: when the next one begins, when not in one."""

    in_block: bool
    until: datetime | None
    next_block: datetime | None


def parse_peak_hours(spec: str, lead_minutes: int = 15) -> PeakHours:
    """Parse `SCHEDULER_PEAK_HOURS`. Raises `PeakConfigError` naming the bad part."""
    if lead_minutes < 0:
        raise PeakConfigError(f"lead {lead_minutes} is negative")
    text = spec.strip().lower()
    if not text or text == "off":
        return PeakHours(windows=(), lead=timedelta(minutes=lead_minutes), spec="off")
    windows = tuple(_parse_window(part.strip()) for part in text.split(";") if part.strip())
    if not windows:
        raise PeakConfigError(f"{spec!r} holds no window")
    return PeakHours(windows=windows, lead=timedelta(minutes=lead_minutes), spec=spec.strip())


def _parse_window(part: str) -> PeakWindow:
    match = _WINDOW.match(" ".join(part.split()))
    if match is None:
        raise PeakConfigError(
            f"{part!r} is not '<days> <HH:MM>-<HH:MM> <+HH:MM>', e.g. 'mon-fri 14:00-18:00 +08:00'"
        )
    hh1, mm1, hh2, mm2 = (int(g) for g in match.group(2, 3, 4, 5))
    oh, om = int(match["oh"]), int(match["om"])
    if oh > 14 or om > 59:
        raise PeakConfigError(f"{part!r}: offset is not a UTC offset")
    offset = timedelta(hours=oh, minutes=om)
    if match["sign"] == "-":
        offset = -offset
    if (hh1, mm1) == (hh2, mm2):
        raise PeakConfigError(f"{part!r}: start and end are the same time")
    return PeakWindow(
        days=_parse_days(match["days"], part),
        start=time(hh1, mm1),
        end=time(hh2, mm2),
        offset=timezone(offset),
    )


def _parse_days(text: str, part: str) -> frozenset[int]:
    days: set[int] = set()
    for item in text.split(","):
        bounds = item.split("-")
        if len(bounds) > 2 or not all(b in DAYS for b in bounds):
            raise PeakConfigError(f"{part!r}: {item!r} is not a day or a day range ({', '.join(DAYS)})")
        first, last = DAYS.index(bounds[0]), DAYS.index(bounds[-1])
        if last < first:
            raise PeakConfigError(f"{part!r}: {item!r} runs backwards")
        days.update(range(first, last + 1))
    return frozenset(days)


def _at(day: date, at: time, offset: timezone) -> datetime:
    return datetime.combine(day, at, tzinfo=offset)
