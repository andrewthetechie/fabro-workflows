"""Inline SVG charts for the `/metrics` page.

Server-rendered, like everything else the scheduler serves: no chart library, no
CDN, no client build. Each chart is one `<svg>` with a `viewBox`, so it scales to
its card. Hover is each mark's `<title>`, which every browser shows as a tooltip,
over a hit area wider than the mark.

Colour is by role, not by hex: marks use `var(--s1)`..`var(--s8)`, the
categorical slots `metrics.html` defines for the page's dark surface (validated
with the dataviz palette checker against `#0f172a`: every adjacent pair clears the
CVD and normal-vision floors). Slots are assigned per entity in fixed order and
never cycled. Text never wears a series colour.

Mark specs: columns and bars at most 24px thick with a 4px rounded data end and a
square baseline, 2px lines, 8px markers with a 2px surface ring, a 2px surface gap
between stacked segments, hairline solid gridlines.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from markupsafe import Markup, escape

WIDTH = 640
SURFACE = "#0f172a"  # slate-900, the card surface
GRID = "#1e293b"  # slate-800
AXIS_TEXT = "#94a3b8"  # slate-400
VALUE_TEXT = "#cbd5e1"  # slate-300
FONT = 'font-family="ui-sans-serif, system-ui, sans-serif"'


@dataclass(frozen=True)
class Series:
    name: str
    values: Sequence[float | None]
    slot: int  # 1..8, fixed per entity


def legend(series: Sequence[Series]) -> Markup:
    """An HTML legend: a swatch beside plain text, one item per series."""
    items = "".join(
        f'<span class="inline-flex items-center gap-1.5">'
        f'<span class="inline-block h-2.5 w-2.5 rounded-sm" style="background:var(--s{s.slot})"></span>'
        f"{escape(s.name)}</span>"
        for s in series
    )
    return Markup(f'<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">{items}</div>')


def line_chart(
    labels: Sequence[str],
    series: Sequence[Series],
    *,
    y_max: float | None = None,
    fmt: Callable[[float], str] = lambda v: f"{v:.0f}",
    height: int = 220,
) -> Markup:
    """One y-axis line chart over categorical x positions (weeks)."""
    left, right, top, bottom = 44, 16, 12, 28
    plot_w, plot_h = WIDTH - left - right, height - top - bottom
    peak = max([v for s in series for v in s.values if v is not None] or [0])
    top_value = y_max if y_max is not None else _nice_ceiling(peak)
    n = len(labels)

    def x(i: int) -> float:
        return left + (plot_w * (i + 0.5) / n if n else 0)

    def y(v: float) -> float:
        return top + plot_h - (plot_h * v / top_value if top_value else 0)

    parts = [_svg_open(height, "line chart")]
    parts.append(_y_grid(top_value, left, plot_w, y, fmt))
    parts.append(_x_labels(labels, x, height - 8))
    for s in series:
        points = [(x(i), y(v)) for i, v in enumerate(s.values) if v is not None]
        if len(points) > 1:
            d = " ".join(f"{'M' if j == 0 else 'L'}{px:.1f},{py:.1f}" for j, (px, py) in enumerate(points))
            parts.append(
                f'<path d="{d}" fill="none" stroke="var(--s{s.slot})" stroke-width="2"'
                ' stroke-linejoin="round" stroke-linecap="round"/>'
            )
        for i, v in enumerate(s.values):
            if v is None:
                continue
            parts.append(
                f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="var(--s{s.slot})"'
                f' stroke="{SURFACE}" stroke-width="2"/>'
            )
    # One hit column per x position, carrying every series' value in its tooltip.
    for i, label in enumerate(labels):
        tip = "\n".join(
            f"{s.name}: {fmt(s.values[i]) if s.values[i] is not None else 'n/a'}" for s in series
        )
        parts.append(
            f'<rect x="{x(i) - plot_w / n / 2:.1f}" y="{top}" width="{plot_w / n:.1f}"'
            f' height="{plot_h}" fill="transparent"><title>{escape(label)}\n{escape(tip)}</title></rect>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def stacked_columns(
    labels: Sequence[str],
    series: Sequence[Series],
    *,
    normalize: bool = False,
    fmt: Callable[[float], str] = lambda v: f"{v:.0f}",
    height: int = 220,
) -> Markup:
    """Stacked columns, one per x position. `normalize` stacks shares to 100%."""
    left, right, top, bottom = 44, 16, 12, 28
    plot_w, plot_h = WIDTH - left - right, height - top - bottom
    n = len(labels)
    totals = [sum((s.values[i] or 0) for s in series) for i in range(n)]
    top_value = 1.0 if normalize else _nice_ceiling(max(totals or [0]))
    band = plot_w / n if n else plot_w
    bar = min(24.0, band * 0.6)

    def y(v: float) -> float:
        return top + plot_h - (plot_h * v / top_value if top_value else 0)

    def x(i: int) -> float:
        return left + band * (i + 0.5)

    axis_fmt = (lambda v: f"{v * 100:.0f}%") if normalize else fmt
    parts = [_svg_open(height, "stacked column chart")]
    parts.append(_y_grid(top_value, left, plot_w, y, axis_fmt))
    parts.append(_x_labels(labels, x, height - 8))
    for i in range(n):
        total = totals[i]
        if not total:
            continue
        base = 0.0
        drawn = [(s, s.values[i] or 0) for s in series if (s.values[i] or 0) > 0]
        for j, (s, raw) in enumerate(drawn):
            value = raw / total if normalize else raw
            y0, y1 = y(base), y(base + value)
            is_top = j == len(drawn) - 1
            # 2px surface gap between segments: shave 1px off each shared edge.
            seg_top = y1 + (0 if is_top else 1)
            seg_bottom = y0 - (0 if j == 0 else 1)
            h = max(0.0, seg_bottom - seg_top)
            share = f" ({raw / total * 100:.0f}%)" if total else ""
            tip = f"{labels[i]}\n{s.name}: {fmt(raw)}{share}"
            parts.append(
                _column(x(i) - bar / 2, seg_top, bar, h, f"var(--s{s.slot})", rounded=is_top, tip=tip)
            )
            base += value
    parts.append("</svg>")
    return Markup("".join(parts))


def bars(
    items: Sequence[tuple[str, float, str]],
    *,
    slot: int = 1,
    row: int = 26,
) -> Markup:
    """Horizontal bars, one series, value at the tip. `items` are (label, value, value text)."""
    if not items:
        return Markup('<p class="text-sm text-slate-500">Nothing in this window.</p>')
    label_w, right, top = 210, 64, 4
    plot_w = WIDTH - label_w - right
    height = top * 2 + row * len(items)
    peak = max(value for _, value, _ in items) or 1
    thick = min(16, row - 8)
    parts = [_svg_open(height, "bar chart")]
    for i, (label, value, text) in enumerate(items):
        cy = top + row * i + row / 2
        w = plot_w * value / peak
        parts.append(
            f'<text x="{label_w - 8}" y="{cy + 4:.1f}" text-anchor="end" font-size="12"'
            f' fill="{AXIS_TEXT}" {FONT}>{escape(_clip(label, 30))}</text>'
        )
        parts.append(
            f'<g><title>{escape(label)}: {escape(text)}</title>'
            f'<rect x="{label_w}" y="{cy - row / 2:.1f}" width="{plot_w + right}" height="{row}" fill="transparent"/>'
            + _bar_path(label_w, cy - thick / 2, w, thick, f"var(--s{slot})")
            + "</g>"
        )
        parts.append(
            f'<text x="{label_w + w + 6:.1f}" y="{cy + 4:.1f}" font-size="12"'
            f' fill="{VALUE_TEXT}" {FONT}>{escape(text)}</text>'
        )
    parts.append("</svg>")
    return Markup("".join(parts))


def _svg_open(height: int, label: str) -> str:
    return (
        f'<svg viewBox="0 0 {WIDTH} {height}" width="100%" role="img" aria-label="{escape(label)}"'
        ' preserveAspectRatio="xMidYMid meet" class="block">'
    )


def _y_grid(top_value: float, left: int, plot_w: float, y: Callable[[float], float], fmt) -> str:
    out = []
    for k in range(5):
        v = top_value * k / 4
        yy = y(v)
        out.append(
            f'<line x1="{left}" x2="{left + plot_w}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{left - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="11"'
            f' fill="{AXIS_TEXT}" {FONT} style="font-variant-numeric: tabular-nums">{escape(fmt(v))}</text>'
        )
    return "".join(out)


def _x_labels(labels: Sequence[str], x: Callable[[int], float], baseline: float) -> str:
    # Thin the labels when there are many weeks, so they never collide.
    step = max(1, -(-len(labels) // 8))
    return "".join(
        f'<text x="{x(i):.1f}" y="{baseline}" text-anchor="middle" font-size="11"'
        f' fill="{AXIS_TEXT}" {FONT}>{escape(label)}</text>'
        for i, label in enumerate(labels)
        if i % step == 0 or i == len(labels) - 1
    )


def _column(x0: float, y0: float, w: float, h: float, fill: str, *, rounded: bool, tip: str) -> str:
    """A column segment; the top segment gets the 4px rounded data end."""
    r = min(4.0, h / 2, w / 2) if rounded else 0.0
    if r <= 0:
        shape = f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"/>'
    else:
        x1, y1 = x0 + w, y0 + h
        shape = (
            f'<path d="M{x0:.1f},{y1:.1f} L{x0:.1f},{y0 + r:.1f} Q{x0:.1f},{y0:.1f} {x0 + r:.1f},{y0:.1f}'
            f" L{x1 - r:.1f},{y0:.1f} Q{x1:.1f},{y0:.1f} {x1:.1f},{y0 + r:.1f} L{x1:.1f},{y1:.1f} Z\""
            f' fill="{fill}"/>'
        )
    return f"<g><title>{escape(tip)}</title>{shape}</g>"


def _bar_path(x0: float, y0: float, w: float, h: float, fill: str) -> str:
    """A horizontal bar, square at the baseline, 4px rounded at the data end."""
    if w <= 0:
        return ""
    r = min(4.0, h / 2, w)
    x1, y1 = x0 + w, y0 + h
    return (
        f'<path d="M{x0:.1f},{y0:.1f} L{x1 - r:.1f},{y0:.1f} Q{x1:.1f},{y0:.1f} {x1:.1f},{y0 + r:.1f}'
        f" L{x1:.1f},{y1 - r:.1f} Q{x1:.1f},{y1:.1f} {x1 - r:.1f},{y1:.1f} L{x0:.1f},{y1:.1f} Z\""
        f' fill="{fill}"/>'
    )


def _nice_ceiling(value: float) -> float:
    """The next clean axis maximum at or above `value` (1, 2, 2.5, 5 x 10^k)."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** len(str(int(value))) / 10 if value >= 1 else 1.0
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
