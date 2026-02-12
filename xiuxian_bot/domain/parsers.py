import re


def parse_biguan_cooldown_minutes(text: str) -> int | None:
    """Parse '打坐调息 N 分钟' -> N."""

    match = re.search(r"(\d+)\s*分钟", text)
    if not match:
        return None
    try:
        minutes = int(match.group(1))
    except ValueError:
        return None
    return minutes if minutes > 0 else None


def parse_lingqi_cooldown_seconds(text: str) -> int | None:
    """Parse '灵气尚未平复' cooldown like '10分钟8秒' or '18秒' -> total seconds."""

    match_min = re.search(r"(\d+)\s*分钟", text)
    minutes = int(match_min.group(1)) if match_min else 0

    match_sec = re.search(r"(\d+)\s*秒", text)
    seconds = int(match_sec.group(1)) if match_sec else 0

    total = minutes * 60 + seconds
    return total if total > 0 else None

