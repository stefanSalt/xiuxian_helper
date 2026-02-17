from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class XinggongObservatoryStatus:
    total_disks: int | None
    idle_disks: list[int]
    abnormal_disks: list[int]
    collectable_disks: list[int]
    # Min time-to-finish (seconds) from any disk line that contains "(剩余: ...)".
    min_remaining_seconds: int | None = None


_TOTAL_RE = re.compile(r"引\s*\[?\s*星盘总数\s*[:：]\s*(\d+)\s*座")
_DISK_RE = re.compile(
    r"(\d+)\s*号\s*引\s*\[?\s*星盘\s*[:：]\s*(.+?)(?=(?:\d+\s*号\s*引\s*\[?\s*星盘\s*[:：])|$)",
    re.S,
)
_REMAINING_RE = re.compile(r"[（(]\s*剩余\s*[:：]\s*([^)）]+?)\s*[)）]")


def _parse_duration_seconds(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None

    def _pick(unit: str) -> int:
        match = re.search(rf"(\d+)\s*{unit}", raw)
        return int(match.group(1)) if match else 0

    days = _pick("天")
    hours = _pick("小时")
    minutes = _pick("分钟")
    seconds = _pick("秒")

    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def parse_xinggong_observatory(text: str) -> XinggongObservatoryStatus | None:
    """Parse '.观星台' response into coarse flags for automation.

    The game text may evolve; this parser is intentionally conservative and
    keyword-based to avoid mis-triggering.
    """

    if "观星台" not in text:
        return None

    total_disks: int | None = None
    total_match = _TOTAL_RE.search(text)
    if total_match:
        try:
            total_disks = int(total_match.group(1))
        except ValueError:
            total_disks = None

    idle: list[int] = []
    abnormal: list[int] = []
    collectable: list[int] = []
    min_remaining_seconds: int | None = None

    matched_disk = False
    for match in _DISK_RE.finditer(text):
        matched_disk = True
        try:
            idx = int(match.group(1))
        except ValueError:
            continue
        body = match.group(2)
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue

        if "空闲" in body:
            idle.append(idx)
            continue

        if any(k in body for k in ("元磁紊乱", "星光黯淡", "狂暴", "紊乱", "异常")):
            abnormal.append(idx)
            continue

        rem_match = _REMAINING_RE.search(body)
        if rem_match:
            rem_seconds = _parse_duration_seconds(rem_match.group(1))
            if rem_seconds is not None and (min_remaining_seconds is None or rem_seconds < min_remaining_seconds):
                min_remaining_seconds = rem_seconds
            continue

        # Heuristic: if it's neither idle/abnormal/collecting, it might be ready for collection.
        if any(k in body for k in ("已凝聚", "凝聚完成", "已成形", "可收集", "精华")):
            collectable.append(idx)

    # Fallback: if we can't match any disk lines, still return a minimal status so the caller can avoid actions.
    if not matched_disk and total_disks is None:
        return None

    return XinggongObservatoryStatus(
        total_disks=total_disks,
        idle_disks=sorted(set(idle)),
        abnormal_disks=sorted(set(abnormal)),
        collectable_disks=sorted(set(collectable)),
        min_remaining_seconds=min_remaining_seconds,
    )

