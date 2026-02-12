from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GardenStatus:
    has_idle: bool
    has_growing: bool
    has_mature: bool
    has_insect: bool
    has_weed: bool
    has_drought: bool


_PLOT_LINE_RE = re.compile(r"^\s*(\d+)\s*号\s*灵田[:：]\s*(.+?)\s*$")


def parse_garden_status(text: str) -> GardenStatus | None:
    """Parse '.小药园' response into coarse flags.

    This is intentionally conservative and keyword-based: the game text format
    may evolve, but core keywords are stable enough to drive one-click actions.
    """

    # Fast reject to avoid mis-triggering on unrelated messages.
    if "小药园" not in text and "灵田总数" not in text:
        return None

    has_idle = False
    has_growing = False
    has_mature = False
    has_insect = False
    has_weed = False
    has_drought = False

    matched_plot_line = False
    for line in text.splitlines():
        match = _PLOT_LINE_RE.match(line)
        if not match:
            continue
        matched_plot_line = True
        body = match.group(2)

        if any(k in body for k in ("空闲", "未种植", "闲置", "空地")):
            has_idle = True
        if "生长中" in body:
            has_growing = True
        if "已成熟" in body:
            has_mature = True
        if "害虫侵扰" in body:
            has_insect = True
        if "杂草横生" in body:
            has_weed = True
        if "灵气干涸" in body:
            has_drought = True

    # Fallback: some versions may not label each plot with "X号灵田:" lines.
    if not matched_plot_line:
        has_idle = any(k in text for k in ("空闲", "未种植", "闲置", "空地"))
        has_growing = "生长中" in text
        has_mature = "已成熟" in text
        has_insect = "害虫侵扰" in text
        has_weed = "杂草横生" in text
        has_drought = "灵气干涸" in text

    return GardenStatus(
        has_idle=has_idle,
        has_growing=has_growing,
        has_mature=has_mature,
        has_insect=has_insect,
        has_weed=has_weed,
        has_drought=has_drought,
    )

