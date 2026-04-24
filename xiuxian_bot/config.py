from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from .domain.text_normalizer import normalize_match_text


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def _env(key: str) -> str | None:
    value = os.getenv(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _get_env_str(key: str, *, default: str | None = None) -> str:
    value = _env(key)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required env var: {key}")
        return default
    return value


def _get_env_int(key: str, *, default: int | None = None) -> int:
    value = _env(key)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required env var: {key}")
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid int env var {key}={value!r}") from exc


def _get_env_float(key: str, *, default: float | None = None) -> float:
    value = _env(key)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required env var: {key}")
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid float env var {key}={value!r}") from exc


def _get_env_bool(key: str, *, default: bool = False) -> bool:
    value = _env(key)
    if value is None:
        return default
    return _parse_bool(value, key)


def _parse_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise ValueError(f"Missing bool value for {label}")
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid bool value for {label}={value!r} (use 1/0)")


def _parse_int(value: Any, label: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid int value for {label}={value!r}") from exc


def _parse_float(value: Any, label: str) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float value for {label}={value!r}") from exc


def _parse_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


LEGACY_ACCOUNT_ENV_KEYS = (
    "TG_API_ID",
    "TG_API_HASH",
    "TG_SESSION_NAME",
    "GAME_CHAT_ID",
    "TOPIC_ID",
    "MY_NAME",
)

_MAIN_IDENTITY_KEY = "main"


def _parse_mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"Invalid mapping value for {label}={value!r}")
    return {str(key): val for key, val in value.items()}


def _parse_identity_key(value: Any, *, fallback: str) -> str:
    normalized = normalize_match_text(str(value or ""))
    return normalized or fallback


@dataclass(frozen=True)
class IdentityProfile:
    key: str
    kind: str
    my_name: str
    switch_target: str
    display_name: str = ""
    game_id: str = ""
    tg_username: str = ""
    config_overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.display_name.strip() or self.my_name.strip() or self.switch_target.strip() or self.key

    @property
    def is_main(self) -> bool:
        return self.kind == "main"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def normalized_tokens(self) -> tuple[str, ...]:
        tokens: list[str] = []
        for value in (
            self.key,
            self.my_name,
            self.switch_target,
            self.display_name,
            self.game_id,
            self.tg_username,
        ):
            normalized = normalize_match_text(value)
            if normalized and normalized not in tokens:
                tokens.append(normalized)
        return tuple(tokens)

    @staticmethod
    def from_mapping(data: Mapping[str, Any], *, fallback_key: str, kind: str = "avatar") -> "IdentityProfile":
        switch_target = str(data.get("switch_target", "")).strip()
        my_name = str(data.get("my_name", "")).strip()
        display_name = str(data.get("display_name", "")).strip()
        game_id = str(data.get("game_id", "")).strip()
        tg_username = str(data.get("tg_username", "")).strip().lstrip("@")
        key = _parse_identity_key(
            data.get("key"),
            fallback=_parse_identity_key(switch_target or my_name or game_id or display_name, fallback=fallback_key),
        )
        return IdentityProfile(
            key=key,
            kind=str(data.get("kind", kind)).strip() or kind,
            my_name=my_name,
            switch_target=switch_target,
            display_name=display_name,
            game_id=game_id,
            tg_username=tg_username,
            config_overrides=_parse_mapping(data.get("config_overrides"), "config_overrides"),
        )


@dataclass(frozen=True)
class SystemConfig:
    log_level: str = "INFO"
    app_db_path: str = "xiuxian_app.sqlite3"
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    web_admin_username: str = "admin"
    web_admin_password: str = "changeme"
    web_secret_key: str = "changeme-secret"
    log_dir: str = "logs"
    session_root_dir: str = ""
    default_account_name: str = "default"
    message_archive_cleanup_enabled: bool = True
    message_archive_retention_days: int = 30
    message_archive_vacuum_enabled: bool = True

    @staticmethod
    def load() -> "SystemConfig":
        _load_dotenv(Path(".env"))
        app_db_path = _env("APP_DB_PATH") or _env("STATE_DB_PATH") or "xiuxian_app.sqlite3"
        admin_password = _get_env_str("WEB_ADMIN_PASSWORD", default="changeme")
        secret_key = _get_env_str(
            "WEB_SECRET_KEY",
            default=f"xiuxian-helper::{admin_password}::{app_db_path}",
        )
        return SystemConfig(
            log_level=_get_env_str("LOG_LEVEL", default="INFO").upper(),
            app_db_path=app_db_path,
            web_host=_get_env_str("WEB_HOST", default="127.0.0.1"),
            web_port=_get_env_int("WEB_PORT", default=8000),
            web_admin_username=_get_env_str("WEB_ADMIN_USERNAME", default="admin"),
            web_admin_password=admin_password,
            web_secret_key=secret_key,
            log_dir=_get_env_str("LOG_DIR", default="logs"),
            session_root_dir=_get_env_str("SESSION_ROOT_DIR", default=""),
            default_account_name=_get_env_str("DEFAULT_ACCOUNT_NAME", default="default"),
            message_archive_cleanup_enabled=_get_env_bool(
                "MESSAGE_ARCHIVE_CLEANUP_ENABLED",
                default=True,
            ),
            message_archive_retention_days=_get_env_int(
                "MESSAGE_ARCHIVE_RETENTION_DAYS",
                default=30,
            ),
            message_archive_vacuum_enabled=_get_env_bool(
                "MESSAGE_ARCHIVE_VACUUM_ENABLED",
                default=True,
            ),
        )


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

    # Plugin toggles
    enable_biguan: bool
    enable_daily: bool
    enable_garden: bool
    enable_xinggong: bool
    enable_yuanying: bool
    enable_zongmen: bool

    # Biguan timings
    biguan_extra_buffer_seconds: int
    biguan_cooldown_jitter_min_seconds: int
    biguan_cooldown_jitter_max_seconds: int
    biguan_retry_jitter_min_seconds: int
    biguan_retry_jitter_max_seconds: int

    # Garden
    garden_seed_name: str
    garden_poll_interval_seconds: int
    garden_action_spacing_seconds: int

    # Xinggong
    xinggong_star_name: str
    xinggong_poll_interval_seconds: int
    xinggong_action_spacing_seconds: int
    xinggong_qizhen_start_time: str
    xinggong_qizhen_retry_interval_seconds: int
    xinggong_qizhen_second_offset_seconds: int
    xinggong_wenan_interval_seconds: int

    # Yuanying
    yuanying_liefeng_interval_seconds: int
    yuanying_chuqiao_interval_seconds: int

    # Zongmen
    zongmen_cmd_dianmao: str
    zongmen_cmd_chuangong: str
    zongmen_dianmao_time: str | None
    zongmen_chuangong_times: str | None
    zongmen_chuangong_xinde_text: str
    zongmen_catch_up: bool
    zongmen_action_spacing_seconds: int

    # Xinggong sub-features
    enable_message_archive: bool = True
    enable_xinggong_wenan: bool = True
    enable_xinggong_deep_biguan: bool = False
    enable_xinggong_guanxing: bool = False
    enable_yuanying_liefeng: bool = True
    xinggong_guanxing_target_username: str = "salt9527"
    xinggong_guanxing_preview_advance_seconds: int = 180
    xinggong_guanxing_shift_advance_seconds: float = 1.0
    xinggong_guanxing_watch_events: str = "星辰异象,地磁暴动"

    # Send spacing
    global_send_min_interval_seconds: int = 10

    # Persistence path (kept for backward compatibility, runtime uses SystemConfig.app_db_path)
    state_db_path: str = "xiuxian_app.sqlite3"

    # Chuangta
    enable_chuangta: bool = False
    chuangta_time: str = "14:15"

    # Lingxiaogong
    enable_lingxiaogong: bool = False
    enable_lingxiaogong_wenxintai: bool = True
    enable_lingxiaogong_jiutian: bool = True
    enable_lingxiaogong_dengtianjie: bool = True
    lingxiaogong_poll_interval_seconds: int = 300
    lingxiaogong_wenxintai_after_climb_count: int = 4
    enable_random_event_nanlonghou: bool = True
    random_event_nanlonghou_action: str = ".交换 功法"
    enable_random_event_jiyin: bool = True
    random_event_jiyin_action: str = ".献上魂魄"
    system_reply_source_usernames: str = "hantianzunhl"

    # Identity / multi-account metadata
    account_id: str = "default"
    account_name: str = "default"
    identity_profiles: tuple[IdentityProfile, ...] = field(default_factory=tuple)
    active_identity_key: str = _MAIN_IDENTITY_KEY
    switch_command_template: str = ".切换 {target}"
    switch_list_command: str = ".切换"
    switch_back_target: str = "主魂"
    switch_success_keywords: str = "切换成功,神念已附着"
    switch_back_success_keywords: str = "神念重归主魂肉身"
    switch_failure_keywords: str = "未找到道号或ID"
    auto_return_main_after_avatar_action: bool = True
    auto_return_main_delay_seconds: int = 120
    status_command: str = ".状态"
    status_identity_header_keyword: str = "修士状态"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["identity_profiles"] = [profile.to_dict() for profile in self.identity_profiles]
        return payload

    def _resolve_identity_profiles(self) -> tuple[IdentityProfile, ...]:
        if self.identity_profiles:
            return self.identity_profiles
        default_main = IdentityProfile(
            key=_MAIN_IDENTITY_KEY,
            kind="main",
            my_name=self.my_name,
            switch_target=self.switch_back_target or "主魂",
            display_name=self.my_name,
        )
        return (default_main,)

    @property
    def identities(self) -> tuple[IdentityProfile, ...]:
        return self._resolve_identity_profiles()

    @property
    def active_identity(self) -> IdentityProfile:
        for identity in self.identities:
            if identity.key == self.active_identity_key:
                return identity
        return self.identities[0]

    @property
    def all_identity_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for identity in self.identities:
            candidate = identity.my_name.strip()
            if candidate and candidate not in names:
                names.append(candidate)
        return tuple(names)

    @property
    def all_identity_mentions(self) -> tuple[str, ...]:
        mentions: list[str] = list(self.all_identity_names)
        for identity in self.identities:
            username = identity.tg_username.strip().lstrip("@")
            if not username:
                continue
            for candidate in (username, f"@{username}"):
                if candidate not in mentions:
                    mentions.append(candidate)
        return tuple(mentions)

    def identity_by_key(self, key: str) -> IdentityProfile | None:
        normalized_key = _parse_identity_key(key, fallback="")
        for identity in self.identities:
            if identity.key == normalized_key:
                return identity
        return None

    def apply_identity(self, identity_key: str) -> "Config":
        identity = self.identity_by_key(identity_key)
        if identity is None:
            raise ValueError(f"Unknown identity key: {identity_key}")
        overrides = dict(identity.config_overrides)
        overrides["my_name"] = identity.my_name or self.my_name
        overrides["active_identity_key"] = identity.key
        return replace(self, **overrides)

    def with_identity(self, *, account_id: str, account_name: str, state_db_path: str | None = None) -> "Config":
        data = {
            "account_id": str(account_id),
            "account_name": account_name.strip() or str(account_id),
        }
        if state_db_path is not None:
            data["state_db_path"] = state_db_path
        updated = replace(self, **data)
        if not updated.identity_profiles:
            updated = replace(
                updated,
                identity_profiles=updated._resolve_identity_profiles(),
            )
        if updated.identity_by_key(updated.active_identity_key) is None:
            updated = replace(updated, active_identity_key=updated.identities[0].key)
        return updated

    def with_session_name(self, tg_session_name: str) -> "Config":
        return replace(self, tg_session_name=tg_session_name)

    @staticmethod
    def from_mapping(data: Mapping[str, Any]) -> "Config":
        enable_zongmen = _parse_bool(data.get("enable_zongmen", False), "enable_zongmen")
        zongmen_dianmao_time = _parse_optional_str(data.get("zongmen_dianmao_time"))
        zongmen_chuangong_times = _parse_optional_str(data.get("zongmen_chuangong_times"))
        if enable_zongmen and (zongmen_dianmao_time is None or zongmen_chuangong_times is None):
            raise ValueError("启用宗门功能时必须填写点卯时间和传功时间")
        identities_raw = data.get("identity_profiles")
        identity_profiles: tuple[IdentityProfile, ...]
        if isinstance(identities_raw, list):
            parsed_profiles: list[IdentityProfile] = []
            for index, raw_profile in enumerate(identities_raw):
                if not isinstance(raw_profile, Mapping):
                    raise ValueError(f"Invalid identity profile at index {index}")
                parsed_profiles.append(
                    IdentityProfile.from_mapping(
                        raw_profile,
                        fallback_key=f"avatar_{index + 1}",
                        kind="main" if index == 0 else "avatar",
                    )
                )
            identity_profiles = tuple(parsed_profiles)
        else:
            identity_profiles = ()

        config = Config(
            tg_api_id=_parse_int(data.get("tg_api_id"), "tg_api_id"),
            tg_api_hash=str(data.get("tg_api_hash", "")).strip(),
            tg_session_name=str(data.get("tg_session_name", "")).strip(),
            game_chat_id=_parse_int(data.get("game_chat_id"), "game_chat_id"),
            topic_id=_parse_int(data.get("topic_id"), "topic_id"),
            my_name=str(data.get("my_name", "")).strip(),
            send_to_topic=_parse_bool(data.get("send_to_topic", False), "send_to_topic"),
            action_cmd_biguan=str(data.get("action_cmd_biguan", ".闭关修炼")).strip() or ".闭关修炼",
            dry_run=_parse_bool(data.get("dry_run", False), "dry_run"),
            enable_message_archive=_parse_bool(
                data.get("enable_message_archive", True),
                "enable_message_archive",
            ),
            log_level=str(data.get("log_level", "INFO")).strip().upper() or "INFO",
            global_sends_per_minute=_parse_int(
                data.get("global_sends_per_minute", 6), "global_sends_per_minute"
            ),
            plugin_sends_per_minute=_parse_int(
                data.get("plugin_sends_per_minute", 3), "plugin_sends_per_minute"
            ),
            enable_biguan=_parse_bool(data.get("enable_biguan", True), "enable_biguan"),
            enable_daily=_parse_bool(data.get("enable_daily", False), "enable_daily"),
            enable_garden=_parse_bool(data.get("enable_garden", False), "enable_garden"),
            enable_xinggong=_parse_bool(data.get("enable_xinggong", False), "enable_xinggong"),
            enable_yuanying=_parse_bool(data.get("enable_yuanying", False), "enable_yuanying"),
            enable_zongmen=enable_zongmen,
            biguan_extra_buffer_seconds=_parse_int(
                data.get("biguan_extra_buffer_seconds", 60), "biguan_extra_buffer_seconds"
            ),
            biguan_cooldown_jitter_min_seconds=_parse_int(
                data.get("biguan_cooldown_jitter_min_seconds", 5),
                "biguan_cooldown_jitter_min_seconds",
            ),
            biguan_cooldown_jitter_max_seconds=_parse_int(
                data.get("biguan_cooldown_jitter_max_seconds", 15),
                "biguan_cooldown_jitter_max_seconds",
            ),
            biguan_retry_jitter_min_seconds=_parse_int(
                data.get("biguan_retry_jitter_min_seconds", 3),
                "biguan_retry_jitter_min_seconds",
            ),
            biguan_retry_jitter_max_seconds=_parse_int(
                data.get("biguan_retry_jitter_max_seconds", 8),
                "biguan_retry_jitter_max_seconds",
            ),
            garden_seed_name=str(data.get("garden_seed_name", "清灵草种子")).strip() or "清灵草种子",
            garden_poll_interval_seconds=_parse_int(
                data.get("garden_poll_interval_seconds", 3600),
                "garden_poll_interval_seconds",
            ),
            garden_action_spacing_seconds=_parse_int(
                data.get("garden_action_spacing_seconds", 25),
                "garden_action_spacing_seconds",
            ),
            xinggong_star_name=str(data.get("xinggong_star_name", "庚金星")).strip() or "庚金星",
            xinggong_poll_interval_seconds=_parse_int(
                data.get("xinggong_poll_interval_seconds", 3600),
                "xinggong_poll_interval_seconds",
            ),
            xinggong_action_spacing_seconds=_parse_int(
                data.get("xinggong_action_spacing_seconds", 25),
                "xinggong_action_spacing_seconds",
            ),
            xinggong_qizhen_start_time=str(
                data.get("xinggong_qizhen_start_time", "07:00")
            ).strip()
            or "07:00",
            xinggong_qizhen_retry_interval_seconds=_parse_int(
                data.get("xinggong_qizhen_retry_interval_seconds", 120),
                "xinggong_qizhen_retry_interval_seconds",
            ),
            xinggong_qizhen_second_offset_seconds=_parse_int(
                data.get("xinggong_qizhen_second_offset_seconds", 43500),
                "xinggong_qizhen_second_offset_seconds",
            ),
            xinggong_wenan_interval_seconds=_parse_int(
                data.get("xinggong_wenan_interval_seconds", 43200),
                "xinggong_wenan_interval_seconds",
            ),
            yuanying_liefeng_interval_seconds=_parse_int(
                data.get("yuanying_liefeng_interval_seconds", 43200),
                "yuanying_liefeng_interval_seconds",
            ),
            yuanying_chuqiao_interval_seconds=_parse_int(
                data.get("yuanying_chuqiao_interval_seconds", 28800),
                "yuanying_chuqiao_interval_seconds",
            ),
            zongmen_cmd_dianmao=str(data.get("zongmen_cmd_dianmao", ".宗门点卯")).strip()
            or ".宗门点卯",
            zongmen_cmd_chuangong=str(data.get("zongmen_cmd_chuangong", ".宗门传功")).strip()
            or ".宗门传功",
            zongmen_dianmao_time=zongmen_dianmao_time,
            zongmen_chuangong_times=zongmen_chuangong_times,
            zongmen_chuangong_xinde_text=str(
                data.get("zongmen_chuangong_xinde_text", "今日修行心得：稳中求进。")
            ).strip()
            or "今日修行心得：稳中求进。",
            zongmen_catch_up=_parse_bool(data.get("zongmen_catch_up", True), "zongmen_catch_up"),
            zongmen_action_spacing_seconds=_parse_int(
                data.get("zongmen_action_spacing_seconds", 20),
                "zongmen_action_spacing_seconds",
            ),
            enable_xinggong_wenan=_parse_bool(
                data.get("enable_xinggong_wenan", True),
                "enable_xinggong_wenan",
            ),
            enable_xinggong_deep_biguan=_parse_bool(
                data.get("enable_xinggong_deep_biguan", False),
                "enable_xinggong_deep_biguan",
            ),
            enable_xinggong_guanxing=_parse_bool(
                data.get("enable_xinggong_guanxing", False),
                "enable_xinggong_guanxing",
            ),
            enable_yuanying_liefeng=_parse_bool(
                data.get("enable_yuanying_liefeng", True),
                "enable_yuanying_liefeng",
            ),
            xinggong_guanxing_target_username=str(
                data.get("xinggong_guanxing_target_username", "salt9527")
            ).strip()
            or "salt9527",
            xinggong_guanxing_preview_advance_seconds=_parse_int(
                data.get("xinggong_guanxing_preview_advance_seconds", 180),
                "xinggong_guanxing_preview_advance_seconds",
            ),
            xinggong_guanxing_shift_advance_seconds=_parse_float(
                data.get("xinggong_guanxing_shift_advance_seconds", 1),
                "xinggong_guanxing_shift_advance_seconds",
            ),
            xinggong_guanxing_watch_events=str(
                data.get("xinggong_guanxing_watch_events", "星辰异象,地磁暴动")
            ).strip()
            or "星辰异象,地磁暴动",
            global_send_min_interval_seconds=_parse_int(
                data.get("global_send_min_interval_seconds", 10),
                "global_send_min_interval_seconds",
            ),
            state_db_path=str(data.get("state_db_path", "xiuxian_app.sqlite3")).strip()
            or "xiuxian_app.sqlite3",
            enable_chuangta=_parse_bool(data.get("enable_chuangta", False), "enable_chuangta"),
            chuangta_time=str(data.get("chuangta_time", "14:15")).strip() or "14:15",
            enable_lingxiaogong=_parse_bool(
                data.get("enable_lingxiaogong", False),
                "enable_lingxiaogong",
            ),
            enable_lingxiaogong_wenxintai=_parse_bool(
                data.get("enable_lingxiaogong_wenxintai", True),
                "enable_lingxiaogong_wenxintai",
            ),
            enable_lingxiaogong_jiutian=_parse_bool(
                data.get("enable_lingxiaogong_jiutian", True),
                "enable_lingxiaogong_jiutian",
            ),
            enable_lingxiaogong_dengtianjie=_parse_bool(
                data.get("enable_lingxiaogong_dengtianjie", True),
                "enable_lingxiaogong_dengtianjie",
            ),
            lingxiaogong_poll_interval_seconds=_parse_int(
                data.get("lingxiaogong_poll_interval_seconds", 300),
                "lingxiaogong_poll_interval_seconds",
            ),
            lingxiaogong_wenxintai_after_climb_count=_parse_int(
                data.get("lingxiaogong_wenxintai_after_climb_count", 4),
                "lingxiaogong_wenxintai_after_climb_count",
            ),
            enable_random_event_nanlonghou=_parse_bool(
                data.get("enable_random_event_nanlonghou", True),
                "enable_random_event_nanlonghou",
            ),
            random_event_nanlonghou_action=str(
                data.get("random_event_nanlonghou_action", ".交换 功法")
            ).strip()
            or ".交换 功法",
            enable_random_event_jiyin=_parse_bool(
                data.get("enable_random_event_jiyin", True),
                "enable_random_event_jiyin",
            ),
            random_event_jiyin_action=str(
                data.get("random_event_jiyin_action", ".献上魂魄")
            ).strip()
            or ".献上魂魄",
            system_reply_source_usernames=str(
                data.get("system_reply_source_usernames", "hantianzunhl")
            ).strip()
            or "hantianzunhl",
            account_id=str(data.get("account_id", "default")).strip() or "default",
            account_name=str(data.get("account_name", "default")).strip() or "default",
            identity_profiles=identity_profiles,
            active_identity_key=_parse_identity_key(
                data.get("active_identity_key"),
                fallback=_MAIN_IDENTITY_KEY,
            ),
            switch_command_template=str(data.get("switch_command_template", ".切换 {target}")).strip()
            or ".切换 {target}",
            switch_list_command=str(data.get("switch_list_command", ".切换")).strip() or ".切换",
            switch_back_target=str(data.get("switch_back_target", "主魂")).strip() or "主魂",
            switch_success_keywords=str(data.get("switch_success_keywords", "切换成功,神念已附着")).strip()
            or "切换成功,神念已附着",
            switch_back_success_keywords=str(
                data.get("switch_back_success_keywords", "神念重归主魂肉身")
            ).strip()
            or "神念重归主魂肉身",
            switch_failure_keywords=str(data.get("switch_failure_keywords", "未找到道号或ID")).strip()
            or "未找到道号或ID",
            auto_return_main_after_avatar_action=_parse_bool(
                data.get("auto_return_main_after_avatar_action", True),
                "auto_return_main_after_avatar_action",
            ),
            auto_return_main_delay_seconds=max(
                0,
                _parse_int(
                    data.get("auto_return_main_delay_seconds", 120),
                    "auto_return_main_delay_seconds",
                ),
            ),
            status_command=str(data.get("status_command", ".状态")).strip() or ".状态",
            status_identity_header_keyword=str(data.get("status_identity_header_keyword", "修士状态")).strip()
            or "修士状态",
        )
        configured = config.with_identity(
            account_id=str(data.get("account_id", config.account_id)).strip() or config.account_id,
            account_name=str(data.get("account_name", config.account_name)).strip() or config.account_name,
            state_db_path=str(data.get("state_db_path", config.state_db_path)).strip() or config.state_db_path,
        )
        if configured.identity_profiles:
            return configured
        return replace(
            configured,
            identity_profiles=configured._resolve_identity_profiles(),
            active_identity_key=_MAIN_IDENTITY_KEY,
        )

    @staticmethod
    def load() -> "Config":
        config = Config.load_legacy_env()
        if config is None:
            raise ValueError("No legacy single-account env configuration found")
        return config

    @staticmethod
    def load_legacy_env() -> "Config" | None:
        _load_dotenv(Path(".env"))
        if any(_env(key) is None for key in LEGACY_ACCOUNT_ENV_KEYS):
            return None

        mapping = {
            "tg_api_id": _get_env_int("TG_API_ID"),
            "tg_api_hash": _get_env_str("TG_API_HASH"),
            "tg_session_name": _get_env_str("TG_SESSION_NAME", default="xiuxian_private_session"),
            "game_chat_id": _get_env_int("GAME_CHAT_ID"),
            "topic_id": _get_env_int("TOPIC_ID"),
            "my_name": _get_env_str("MY_NAME"),
            "send_to_topic": _get_env_bool("SEND_TO_TOPIC", default=False),
            "action_cmd_biguan": _get_env_str("ACTION_CMD_BIGUAN", default=".闭关修炼"),
            "dry_run": _get_env_bool("DRY_RUN", default=False),
            "enable_message_archive": _get_env_bool("ENABLE_MESSAGE_ARCHIVE", default=True),
            "log_level": _get_env_str("LOG_LEVEL", default="INFO").upper(),
            "global_sends_per_minute": _get_env_int("GLOBAL_SENDS_PER_MINUTE", default=6),
            "plugin_sends_per_minute": _get_env_int("PLUGIN_SENDS_PER_MINUTE", default=3),
            "enable_biguan": _get_env_bool("ENABLE_BIGUAN", default=True),
            "enable_daily": _get_env_bool("ENABLE_DAILY", default=False),
            "enable_garden": _get_env_bool("ENABLE_GARDEN", default=False),
            "enable_xinggong": _get_env_bool("ENABLE_XINGGONG", default=False),
            "enable_yuanying": _get_env_bool("ENABLE_YUANYING", default=False),
            "enable_zongmen": _get_env_bool("ENABLE_ZONGMEN", default=False),
            "biguan_extra_buffer_seconds": _get_env_int(
                "BIGUAN_EXTRA_BUFFER_SECONDS", default=60
            ),
            "biguan_cooldown_jitter_min_seconds": _get_env_int(
                "BIGUAN_COOLDOWN_JITTER_MIN_SECONDS", default=5
            ),
            "biguan_cooldown_jitter_max_seconds": _get_env_int(
                "BIGUAN_COOLDOWN_JITTER_MAX_SECONDS", default=15
            ),
            "biguan_retry_jitter_min_seconds": _get_env_int(
                "BIGUAN_RETRY_JITTER_MIN_SECONDS", default=3
            ),
            "biguan_retry_jitter_max_seconds": _get_env_int(
                "BIGUAN_RETRY_JITTER_MAX_SECONDS", default=8
            ),
            "garden_seed_name": _get_env_str("GARDEN_SEED_NAME", default="清灵草种子"),
            "garden_poll_interval_seconds": _get_env_int(
                "GARDEN_POLL_INTERVAL_SECONDS", default=3600
            ),
            "garden_action_spacing_seconds": _get_env_int(
                "GARDEN_ACTION_SPACING_SECONDS", default=25
            ),
            "xinggong_star_name": _get_env_str("XINGGONG_STAR_NAME", default="庚金星"),
            "xinggong_poll_interval_seconds": _get_env_int(
                "XINGGONG_POLL_INTERVAL_SECONDS", default=3600
            ),
            "xinggong_action_spacing_seconds": _get_env_int(
                "XINGGONG_ACTION_SPACING_SECONDS", default=25
            ),
            "xinggong_qizhen_start_time": _get_env_str(
                "XINGGONG_QIZHEN_START_TIME", default="07:00"
            ),
            "xinggong_qizhen_retry_interval_seconds": _get_env_int(
                "XINGGONG_QIZHEN_RETRY_INTERVAL_SECONDS", default=120
            ),
            "xinggong_qizhen_second_offset_seconds": _get_env_int(
                "XINGGONG_QIZHEN_SECOND_OFFSET_SECONDS", default=43500
            ),
            "xinggong_wenan_interval_seconds": _get_env_int(
                "XINGGONG_WENAN_INTERVAL_SECONDS", default=43200
            ),
            "yuanying_liefeng_interval_seconds": _get_env_int(
                "YUANYING_LIEFENG_INTERVAL_SECONDS", default=43200
            ),
            "yuanying_chuqiao_interval_seconds": _get_env_int(
                "YUANYING_CHUQIAO_INTERVAL_SECONDS", default=28800
            ),
            "zongmen_cmd_dianmao": _get_env_str("ZONGMEN_CMD_DIANMAO", default=".宗门点卯"),
            "zongmen_cmd_chuangong": _get_env_str("ZONGMEN_CMD_CHUANGONG", default=".宗门传功"),
            "zongmen_dianmao_time": _env("ZONGMEN_DIANMAO_TIME"),
            "zongmen_chuangong_times": _env("ZONGMEN_CHUANGONG_TIMES"),
            "zongmen_chuangong_xinde_text": _get_env_str(
                "ZONGMEN_CHUANGONG_XINDE_TEXT",
                default="今日修行心得：稳中求进。",
            ),
            "zongmen_catch_up": _get_env_bool("ZONGMEN_CATCH_UP", default=True),
            "zongmen_action_spacing_seconds": _get_env_int(
                "ZONGMEN_ACTION_SPACING_SECONDS", default=20
            ),
            "enable_xinggong_wenan": _get_env_bool("ENABLE_XINGGONG_WENAN", default=True),
            "enable_xinggong_deep_biguan": _get_env_bool(
                "ENABLE_XINGGONG_DEEP_BIGUAN", default=False
            ),
            "enable_xinggong_guanxing": _get_env_bool(
                "ENABLE_XINGGONG_GUANXING", default=False
            ),
            "enable_yuanying_liefeng": _get_env_bool(
                "ENABLE_YUANYING_LIEFENG", default=True
            ),
            "xinggong_guanxing_target_username": _get_env_str(
                "XINGGONG_GUANXING_TARGET_USERNAME",
                default="salt9527",
            ),
            "xinggong_guanxing_preview_advance_seconds": _get_env_int(
                "XINGGONG_GUANXING_PREVIEW_ADVANCE_SECONDS",
                default=180,
            ),
            "xinggong_guanxing_shift_advance_seconds": _get_env_float(
                "XINGGONG_GUANXING_SHIFT_ADVANCE_SECONDS",
                default=1.0,
            ),
            "xinggong_guanxing_watch_events": _get_env_str(
                "XINGGONG_GUANXING_WATCH_EVENTS",
                default="星辰异象,地磁暴动",
            ),
            "global_send_min_interval_seconds": _get_env_int(
                "GLOBAL_SEND_MIN_INTERVAL_SECONDS", default=10
            ),
            "state_db_path": _env("APP_DB_PATH") or _env("STATE_DB_PATH") or "xiuxian_app.sqlite3",
            "enable_chuangta": _get_env_bool("ENABLE_CHUANGTA", default=False),
            "chuangta_time": _get_env_str("CHUANGTA_TIME", default="14:15"),
            "enable_lingxiaogong": _get_env_bool("ENABLE_LINGXIAOGONG", default=False),
            "enable_lingxiaogong_wenxintai": _get_env_bool(
                "ENABLE_LINGXIAOGONG_WENXINTAI",
                default=True,
            ),
            "enable_lingxiaogong_jiutian": _get_env_bool(
                "ENABLE_LINGXIAOGONG_JIUTIAN",
                default=True,
            ),
            "enable_lingxiaogong_dengtianjie": _get_env_bool(
                "ENABLE_LINGXIAOGONG_DENGTIANJIE",
                default=True,
            ),
            "lingxiaogong_poll_interval_seconds": _get_env_int(
                "LINGXIAOGONG_POLL_INTERVAL_SECONDS",
                default=300,
            ),
            "lingxiaogong_wenxintai_after_climb_count": _get_env_int(
                "LINGXIAOGONG_WENXINTAI_AFTER_CLIMB_COUNT",
                default=4,
            ),
            "enable_random_event_nanlonghou": _get_env_bool(
                "ENABLE_RANDOM_EVENT_NANLONGHOU",
                default=True,
            ),
            "random_event_nanlonghou_action": _get_env_str(
                "RANDOM_EVENT_NANLONGHOU_ACTION",
                default=".交换 功法",
            ),
            "enable_random_event_jiyin": _get_env_bool(
                "ENABLE_RANDOM_EVENT_JIYIN",
                default=True,
            ),
            "random_event_jiyin_action": _get_env_str(
                "RANDOM_EVENT_JIYIN_ACTION",
                default=".献上魂魄",
            ),
            "system_reply_source_usernames": _get_env_str(
                "SYSTEM_REPLY_SOURCE_USERNAMES",
                default="hantianzunhl",
            ),
            "account_id": "legacy-default",
            "account_name": _get_env_str("DEFAULT_ACCOUNT_NAME", default="default"),
        }
        return Config.from_mapping(mapping)
