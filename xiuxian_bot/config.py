import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Very small .env loader (KEY=VALUE), no external dependency.

    - Ignores empty lines and lines starting with '#'
    - Does not override existing environment variables
    """

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")  # allow quoted values
        if not key:
            continue
        os.environ.setdefault(key, value)


def _get_env_str(key: str, *, default: str | None = None) -> str:
    value = os.getenv(key)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required env var: {key}")
        return default
    value = value.strip()
    if not value:
        raise ValueError(f"Empty required env var: {key}")
    return value


def _get_env_int(key: str, *, default: int | None = None) -> int:
    value = os.getenv(key)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required env var: {key}")
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid int env var {key}={value!r}") from exc


def _get_env_bool(key: str, *, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid bool env var {key}={value!r} (use 1/0)")


@dataclass(frozen=True)
class Config:
    # Telegram / Telethon
    tg_api_id: int
    tg_api_hash: str
    tg_session_name: str

    # Game scope
    game_chat_id: int
    topic_id: int
    my_name: str

    # Where to send commands
    send_to_topic: bool

    # Commands
    action_cmd_biguan: str

    # Safety / ops
    dry_run: bool
    log_level: str
    global_sends_per_minute: int
    plugin_sends_per_minute: int

    # Plugin toggles (low-risk default)
    enable_biguan: bool
    enable_daily: bool
    enable_garden: bool
    enable_zongmen: bool

    # Biguan timings
    biguan_extra_buffer_seconds: int
    biguan_cooldown_jitter_min_seconds: int
    biguan_cooldown_jitter_max_seconds: int
    biguan_retry_jitter_min_seconds: int
    biguan_retry_jitter_max_seconds: int

    # Garden (小药园)
    garden_seed_name: str
    garden_poll_interval_seconds: int
    garden_action_spacing_seconds: int

    # 宗门（日常）
    zongmen_cmd_dianmao: str
    zongmen_cmd_chuangong: str
    zongmen_dianmao_time: str | None
    zongmen_chuangong_times: str | None
    zongmen_chuangong_xinde_text: str
    zongmen_catch_up: bool
    zongmen_action_spacing_seconds: int

    @staticmethod
    def load() -> "Config":
        _load_dotenv(Path(".env"))

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"

        enable_zongmen = _get_env_bool("ENABLE_ZONGMEN", default=False)
        zongmen_dianmao_time = os.getenv("ZONGMEN_DIANMAO_TIME", "").strip() or None
        zongmen_chuangong_times = os.getenv("ZONGMEN_CHUANGONG_TIMES", "").strip() or None
        if enable_zongmen and (zongmen_dianmao_time is None or zongmen_chuangong_times is None):
            raise ValueError(
                "ENABLE_ZONGMEN=1 requires ZONGMEN_DIANMAO_TIME and ZONGMEN_CHUANGONG_TIMES (e.g. 09:37 and 09:38,09:40,09:43)"
            )

        return Config(
            tg_api_id=_get_env_int("TG_API_ID"),
            tg_api_hash=_get_env_str("TG_API_HASH"),
            tg_session_name=_get_env_str("TG_SESSION_NAME", default="xiuxian_private_session"),
            game_chat_id=_get_env_int("GAME_CHAT_ID"),
            topic_id=_get_env_int("TOPIC_ID"),
            my_name=_get_env_str("MY_NAME"),
            send_to_topic=_get_env_bool("SEND_TO_TOPIC", default=False),
            action_cmd_biguan=_get_env_str("ACTION_CMD_BIGUAN", default=".闭关修炼"),
            dry_run=_get_env_bool("DRY_RUN", default=False),
            log_level=log_level,
            global_sends_per_minute=_get_env_int("GLOBAL_SENDS_PER_MINUTE", default=6),
            plugin_sends_per_minute=_get_env_int("PLUGIN_SENDS_PER_MINUTE", default=3),
            enable_biguan=_get_env_bool("ENABLE_BIGUAN", default=True),
            enable_daily=_get_env_bool("ENABLE_DAILY", default=False),
            enable_garden=_get_env_bool("ENABLE_GARDEN", default=False),
            enable_zongmen=enable_zongmen,
            biguan_extra_buffer_seconds=_get_env_int("BIGUAN_EXTRA_BUFFER_SECONDS", default=60),
            biguan_cooldown_jitter_min_seconds=_get_env_int(
                "BIGUAN_COOLDOWN_JITTER_MIN_SECONDS", default=5
            ),
            biguan_cooldown_jitter_max_seconds=_get_env_int(
                "BIGUAN_COOLDOWN_JITTER_MAX_SECONDS", default=15
            ),
            biguan_retry_jitter_min_seconds=_get_env_int(
                "BIGUAN_RETRY_JITTER_MIN_SECONDS", default=3
            ),
            biguan_retry_jitter_max_seconds=_get_env_int(
                "BIGUAN_RETRY_JITTER_MAX_SECONDS", default=8
            ),
            garden_seed_name=_get_env_str("GARDEN_SEED_NAME", default="清灵草种子"),
            garden_poll_interval_seconds=_get_env_int("GARDEN_POLL_INTERVAL_SECONDS", default=3600),
            garden_action_spacing_seconds=_get_env_int("GARDEN_ACTION_SPACING_SECONDS", default=25),
            zongmen_cmd_dianmao=_get_env_str("ZONGMEN_CMD_DIANMAO", default=".宗门点卯"),
            zongmen_cmd_chuangong=_get_env_str("ZONGMEN_CMD_CHUANGONG", default=".宗门传功"),
            zongmen_dianmao_time=zongmen_dianmao_time,
            zongmen_chuangong_times=zongmen_chuangong_times,
            zongmen_chuangong_xinde_text=_get_env_str(
                "ZONGMEN_CHUANGONG_XINDE_TEXT", default="今日修行心得：稳中求进。"
            ),
            zongmen_catch_up=_get_env_bool("ZONGMEN_CATCH_UP", default=True),
            zongmen_action_spacing_seconds=_get_env_int("ZONGMEN_ACTION_SPACING_SECONDS", default=20),
        )
