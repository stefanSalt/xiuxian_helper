from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Config, SystemConfig
from .core.account_repository import AccountRepository
from .core.message_archive_repository import MessageArchiveRepository
from .core.state_store import SQLiteStateStore
from .core.system_settings_repository import (
    SHARED_SYSTEM_SETTING_KEYS,
    SystemSettingsRepository,
)
from .runtime import RunnerManager, _resolve_session_name, setup_root_logger
from .tg_adapter import list_send_as_options
from .tg_login import TGLoginService


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
_MESSAGE_ARCHIVE_MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass
class _TGLoginSession:
    flow_id: str
    account_name: str
    tg_session_name: str
    resolved_session_name: str
    phone: str
    phone_code_hash: str
    step: str = "code"

CHECKBOX_FIELDS = {
    "dry_run",
    "enable_message_archive",
    "enable_biguan",
    "enable_daily",
    "enable_wild_explore",
    "enable_random_text",
    "enable_shiqie",
    "enable_luoyunzong",
    "enable_garden",
    "enable_xinggong",
    "enable_yuanying",
    "enable_zongmen",
    "enable_zongmen_chuangong",
    "zongmen_catch_up",
    "enable_xinggong_wenan",
    "enable_xinggong_deep_biguan",
    "enable_xinggong_guanxing",
    "enable_yuanying_liefeng",
    "enable_chuangta",
    "enable_lingxiaogong",
    "enable_lingxiaogong_wenxintai",
    "enable_lingxiaogong_jiutian",
    "enable_lingxiaogong_dengtianjie",
    "enable_random_event_nanlonghou",
    "enable_random_event_jiyin",
    "auto_return_main_after_avatar_action",
}

IDENTITY_OVERRIDE_FIELDS: tuple[dict[str, str], ...] = (
    {"name": "enable_biguan", "label": "自动闭关"},
    {"name": "enable_garden", "label": "小药园"},
    {"name": "enable_xinggong", "label": "星宫"},
    {"name": "enable_xinggong_guanxing", "label": "观星劫持"},
    {"name": "enable_yuanying", "label": "元婴"},
    {"name": "enable_wild_explore", "label": "野外历练"},
    {"name": "enable_random_text", "label": "随机文本"},
    {"name": "enable_shiqie", "label": "侍妾"},
    {"name": "enable_luoyunzong", "label": "落云宗"},
    {"name": "enable_chuangta", "label": "闯塔"},
    {"name": "enable_lingxiaogong", "label": "凌霄宫"},
    {"name": "enable_random_event_nanlonghou", "label": "随机事件·南陇侯"},
    {"name": "enable_random_event_jiyin", "label": "随机事件·极阴祖师"},
    {"name": "enable_zongmen", "label": "宗门"},
)

FORM_SECTIONS: list[tuple[str, list[dict[str, str]]]] = [
    (
        "账号基础",
        [
            {"name": "name", "label": "账号名称", "type": "text"},
            {"name": "tg_session_name", "label": "Session 名称", "type": "text"},
            {"name": "my_name", "label": "游戏名 / @名", "type": "text"},
            {"name": "active_identity_key", "label": "当前激活身份 key", "type": "text"},
            {"name": "enabled", "label": "保存后自动启用", "type": "checkbox"},
            {"name": "dry_run", "label": "Dry Run", "type": "checkbox"},
            {"name": "enable_message_archive", "label": "消息归档", "type": "checkbox"},
        ],
    ),
    (
        "主魂 / 化身",
        [
            {"name": "switch_command_template", "label": "切换命令模板", "type": "text"},
            {"name": "switch_back_target", "label": "主魂切回目标", "type": "text"},
            {"name": "switch_success_keywords", "label": "切换成功关键词", "type": "text"},
            {"name": "switch_back_success_keywords", "label": "切回主魂成功关键词", "type": "text"},
            {"name": "switch_failure_keywords", "label": "切换失败关键词", "type": "text"},
            {"name": "auto_return_main_after_avatar_action", "label": "分身动作后自动切回主魂", "type": "checkbox"},
            {"name": "auto_return_main_delay_seconds", "label": "自动切回主魂延迟(秒)", "type": "number"},
            {"name": "status_command", "label": "状态命令", "type": "text"},
            {"name": "status_identity_header_keyword", "label": "状态头关键词", "type": "text"},
        ],
    ),
    (
        "全局发送",
        [
            {"name": "log_level", "label": "日志级别", "type": "text"},
            {"name": "global_sends_per_minute", "label": "全局每分钟发送数", "type": "number"},
            {"name": "plugin_sends_per_minute", "label": "单插件每分钟发送数", "type": "number"},
            {"name": "global_send_min_interval_seconds", "label": "全局最小发送间隔(秒)", "type": "number"},
        ],
    ),
    (
        "插件开关",
        [
            {"name": "enable_biguan", "label": "自动闭关", "type": "checkbox"},
            {"name": "enable_daily", "label": "日常·卜筮问天", "type": "checkbox"},
            {"name": "enable_wild_explore", "label": "野外历练", "type": "checkbox"},
            {"name": "enable_random_text", "label": "随机文本", "type": "checkbox"},
            {"name": "enable_shiqie", "label": "侍妾", "type": "checkbox"},
            {"name": "enable_luoyunzong", "label": "落云宗", "type": "checkbox"},
            {"name": "enable_garden", "label": "自动种植", "type": "checkbox"},
            {"name": "enable_xinggong", "label": "星宫", "type": "checkbox"},
            {"name": "enable_yuanying", "label": "元婴", "type": "checkbox"},
            {"name": "enable_chuangta", "label": "闯塔", "type": "checkbox"},
            {"name": "enable_lingxiaogong", "label": "凌霄宫", "type": "checkbox"},
            {"name": "enable_random_event_nanlonghou", "label": "随机事件·南陇侯", "type": "checkbox"},
            {"name": "enable_random_event_jiyin", "label": "随机事件·极阴祖师", "type": "checkbox"},
            {"name": "enable_zongmen", "label": "宗门", "type": "checkbox"},
        ],
    ),
    (
        "闭关",
        [
            {"name": "action_cmd_biguan", "label": "闭关指令", "type": "text"},
            {"name": "biguan_mode", "label": "闭关模式(normal/deep)", "type": "text"},
            {"name": "biguan_deep_settle_command", "label": "深度闭关结算触发指令", "type": "text"},
            {"name": "biguan_deep_duration_seconds", "label": "深度闭关周期(秒)", "type": "number"},
            {"name": "biguan_extra_buffer_seconds", "label": "闭关额外缓冲(秒)", "type": "number"},
            {"name": "biguan_cooldown_jitter_min_seconds", "label": "闭关随机最小(秒)", "type": "number"},
            {"name": "biguan_cooldown_jitter_max_seconds", "label": "闭关随机最大(秒)", "type": "number"},
            {"name": "biguan_retry_jitter_min_seconds", "label": "闭关重试最小(秒)", "type": "number"},
            {"name": "biguan_retry_jitter_max_seconds", "label": "闭关重试最大(秒)", "type": "number"},
        ],
    ),
    (
        "日常",
        [
            {"name": "daily_bushi_start_time", "label": "卜筮问天起始时间(HH:MM)", "type": "text"},
            {"name": "daily_bushi_times_per_day", "label": "卜筮问天每日次数(0-10)", "type": "number"},
            {"name": "daily_bushi_interval_seconds", "label": "卜筮问天间隔(秒，至少30)", "type": "number"},
            {"name": "daily_bushi_exchange_action", "label": "神物现世换取指令", "type": "text"},
        ],
    ),
    (
        "野外历练",
        [
            {"name": "wild_explore_strategy", "label": "历练策略(谨慎/均衡/深入)", "type": "text"},
            {"name": "wild_explore_interval_seconds", "label": "历练间隔(秒)", "type": "number"},
        ],
    ),
    (
        "随机文本",
        [
            {"name": "random_text_messages", "label": "文本列表(一行一条)", "type": "textarea"},
            {"name": "random_text_min_interval_seconds", "label": "最小间隔(秒)", "type": "number"},
            {"name": "random_text_max_interval_seconds", "label": "最大间隔(秒)", "type": "number"},
            {"name": "random_text_daily_limit", "label": "每日上限", "type": "number"},
        ],
    ),
    (
        "侍妾",
        [
            {"name": "shiqie_tianji_interval_seconds", "label": "天机代卜间隔(秒)", "type": "number"},
            {"name": "shiqie_rumeng_interval_seconds", "label": "入梦寻图间隔(秒)", "type": "number"},
        ],
    ),
    (
        "落云宗",
        [
            {"name": "luoyunzong_status_interval_seconds", "label": "灵树状态检查间隔(秒)", "type": "number"},
            {"name": "luoyunzong_watering_cooldown_seconds", "label": "灵树灌溉冷却(秒)", "type": "number"},
            {"name": "luoyunzong_watering_strategy", "label": "灌溉策略(match_linggen/always/match_need)", "type": "text"},
            {"name": "luoyunzong_watering_required_needs", "label": "指定需求列表", "type": "text"},
            {"name": "luoyunzong_linggen_refresh_seconds", "label": "灵根刷新间隔(秒)", "type": "number"},
            {"name": "luoyunzong_harvest_suppress_seconds", "label": "采摘后抑制(秒)", "type": "number"},
        ],
    ),
    (
        "小药园",
        [
            {"name": "garden_seed_name", "label": "种子名", "type": "text"},
            {"name": "garden_poll_interval_seconds", "label": "轮询间隔(秒)", "type": "number"},
            {"name": "garden_action_spacing_seconds", "label": "动作间隔(秒)", "type": "number"},
        ],
    ),
    (
        "星宫",
        [
            {"name": "xinggong_star_name", "label": "牵引星辰名", "type": "text"},
            {"name": "xinggong_poll_interval_seconds", "label": "观星台轮询(秒)", "type": "number"},
            {"name": "xinggong_action_spacing_seconds", "label": "星宫动作间隔(秒)", "type": "number"},
            {"name": "xinggong_qizhen_start_time", "label": "启阵开始时间", "type": "text"},
            {"name": "xinggong_qizhen_retry_interval_seconds", "label": "启阵重试间隔(秒)", "type": "number"},
            {"name": "xinggong_qizhen_second_offset_seconds", "label": "第二次启阵偏移(秒)", "type": "number"},
            {"name": "enable_xinggong_wenan", "label": "每日问安", "type": "checkbox"},
            {"name": "xinggong_wenan_interval_seconds", "label": "问安间隔(秒)", "type": "number"},
            {"name": "enable_xinggong_deep_biguan", "label": "启阵联动深度闭关", "type": "checkbox"},
            {"name": "enable_xinggong_guanxing", "label": "观星劫持", "type": "checkbox"},
            {"name": "xinggong_guanxing_target_username", "label": "改换星移目标", "type": "text"},
            {"name": "xinggong_guanxing_preview_advance_seconds", "label": "观星提前量(秒)", "type": "number"},
            {
                "name": "xinggong_guanxing_shift_advance_seconds",
                "label": "改换偏移(秒；正数提前，负数延后)",
                "type": "number",
                "step": "any",
            },
            {"name": "xinggong_guanxing_watch_events", "label": "监听事件", "type": "text"},
        ],
    ),
    (
        "凌霄宫",
        [
            {"name": "enable_lingxiaogong_wenxintai", "label": "自动问心台", "type": "checkbox"},
            {"name": "enable_lingxiaogong_jiutian", "label": "自动引九天罡风", "type": "checkbox"},
            {"name": "enable_lingxiaogong_dengtianjie", "label": "自动登天阶", "type": "checkbox"},
            {"name": "lingxiaogong_poll_interval_seconds", "label": "状态轮询间隔(秒)", "type": "number"},
            {"name": "lingxiaogong_wenxintai_after_climb_count", "label": "第几次登天阶后问心", "type": "number"},
        ],
    ),
    (
        "随机事件",
        [
            {"name": "random_event_nanlonghou_action", "label": "南陇侯响应指令", "type": "text"},
            {"name": "random_event_jiyin_action", "label": "极阴祖师响应指令", "type": "text"},
        ],
    ),
    (
        "元婴 / 闯塔 / 宗门",
        [
            {"name": "enable_yuanying_liefeng", "label": "自动探寻裂缝", "type": "checkbox"},
            {"name": "yuanying_liefeng_interval_seconds", "label": "探寻裂缝间隔(秒)", "type": "number"},
            {"name": "yuanying_chuqiao_interval_seconds", "label": "元婴出窍间隔(秒)", "type": "number"},
            {"name": "chuangta_time", "label": "闯塔时间", "type": "text"},
            {"name": "zongmen_cmd_dianmao", "label": "点卯指令", "type": "text"},
            {"name": "zongmen_dianmao_time", "label": "点卯时间", "type": "text"},
            {"name": "enable_zongmen_chuangong", "label": "自动传功", "type": "checkbox"},
            {"name": "zongmen_cmd_chuangong", "label": "传功指令", "type": "text"},
            {"name": "zongmen_chuangong_times", "label": "传功时间列表", "type": "text"},
            {"name": "zongmen_chuangong_xinde_text", "label": "传功心得文本", "type": "text"},
            {"name": "zongmen_catch_up", "label": "启动补做", "type": "checkbox"},
            {"name": "zongmen_action_spacing_seconds", "label": "宗门动作间隔(秒)", "type": "number"},
        ],
    ),
]


IDENTITY_ACCOUNT_SECTION_TITLES = {"账号基础", "主魂 / 化身", "全局发送"}


def _build_identity_plugin_sections() -> list[tuple[str, list[dict[str, str]]]]:
    sections: list[tuple[str, list[dict[str, str]]]] = []
    seen_fields: set[str] = set()
    for section_title, fields in FORM_SECTIONS:
        if section_title in IDENTITY_ACCOUNT_SECTION_TITLES:
            continue
        section_fields: list[dict[str, str]] = []
        for field in fields:
            name = field["name"]
            if name in seen_fields:
                continue
            seen_fields.add(name)
            section_fields.append(field)
        if section_fields:
            sections.append((section_title, section_fields))
    return sections


IDENTITY_PLUGIN_SECTIONS = _build_identity_plugin_sections()
IDENTITY_PLUGIN_FIELDS = tuple(
    field for _, fields in IDENTITY_PLUGIN_SECTIONS for field in fields
)


def _template_values_for_new(system_config: SystemConfig) -> dict[str, Any]:
    return {
        "name": "",
        "tg_api_id": system_config.tg_api_id,
        "tg_api_hash": system_config.tg_api_hash,
        "tg_session_name": "",
        "game_chat_id": system_config.game_chat_id,
        "topic_id": system_config.topic_id,
        "my_name": "",
        "system_reply_source_usernames": system_config.system_reply_source_usernames,
        "active_identity_key": "main",
        "send_to_topic": system_config.send_to_topic,
        "enabled": True,
        "dry_run": False,
        "enable_message_archive": True,
        "switch_command_template": ".切换 {target}",
        "switch_back_target": "主魂",
        "switch_success_keywords": "切换成功,神念已附着",
        "switch_back_success_keywords": "神念重归主魂肉身",
        "switch_failure_keywords": "未找到道号或ID",
        "auto_return_main_after_avatar_action": True,
        "auto_return_main_delay_seconds": 120,
        "status_command": ".状态",
        "status_identity_header_keyword": "修士状态",
        "identity_profiles": [
            {
                "key": "main",
                "kind": "main",
                "my_name": "",
                "switch_target": "主魂",
                "display_name": "主魂",
                "game_id": "",
                "tg_username": "",
                "config_overrides": {},
            }
        ],
        "log_level": system_config.log_level,
        "global_sends_per_minute": 6,
        "plugin_sends_per_minute": 3,
        "enable_biguan": True,
        "enable_daily": False,
        "enable_wild_explore": False,
        "enable_random_text": False,
        "enable_shiqie": False,
        "enable_luoyunzong": False,
        "enable_garden": False,
        "enable_xinggong": False,
        "enable_yuanying": False,
        "enable_zongmen": False,
        "enable_zongmen_chuangong": False,
        "action_cmd_biguan": ".闭关修炼",
        "biguan_mode": "normal",
        "biguan_deep_settle_command": ".状态",
        "biguan_deep_duration_seconds": 8 * 3600 + 180,
        "biguan_extra_buffer_seconds": 60,
        "biguan_cooldown_jitter_min_seconds": 5,
        "biguan_cooldown_jitter_max_seconds": 15,
        "biguan_retry_jitter_min_seconds": 3,
        "biguan_retry_jitter_max_seconds": 8,
        "daily_bushi_start_time": "08:00",
        "daily_bushi_times_per_day": 5,
        "daily_bushi_interval_seconds": 120,
        "daily_bushi_exchange_action": ".换取",
        "wild_explore_strategy": "深入",
        "wild_explore_interval_seconds": 7200,
        "random_text_messages": "",
        "random_text_min_interval_seconds": 1800,
        "random_text_max_interval_seconds": 7200,
        "random_text_daily_limit": 6,
        "shiqie_tianji_interval_seconds": 12 * 3600,
        "shiqie_rumeng_interval_seconds": 8 * 3600,
        "luoyunzong_status_interval_seconds": 1800,
        "luoyunzong_watering_cooldown_seconds": 7200,
        "luoyunzong_watering_strategy": "match_linggen",
        "luoyunzong_watering_required_needs": "",
        "luoyunzong_linggen_refresh_seconds": 86400,
        "luoyunzong_harvest_suppress_seconds": 86400,
        "garden_seed_name": "清灵草种子",
        "garden_poll_interval_seconds": 3600,
        "garden_action_spacing_seconds": 25,
        "xinggong_star_name": "庚金星",
        "xinggong_poll_interval_seconds": 3600,
        "xinggong_action_spacing_seconds": 25,
        "xinggong_qizhen_start_time": "07:00",
        "xinggong_qizhen_retry_interval_seconds": 120,
        "xinggong_qizhen_second_offset_seconds": 43500,
        "enable_xinggong_wenan": True,
        "xinggong_wenan_interval_seconds": 43200,
        "enable_xinggong_deep_biguan": False,
        "enable_xinggong_guanxing": False,
        "enable_yuanying_liefeng": True,
        "xinggong_guanxing_target_username": "salt9527",
        "xinggong_guanxing_preview_advance_seconds": 180,
        "xinggong_guanxing_shift_advance_seconds": 1.0,
        "xinggong_guanxing_watch_events": "星辰异象,地磁暴动",
        "yuanying_liefeng_interval_seconds": 43200,
        "yuanying_chuqiao_interval_seconds": 28800,
        "enable_chuangta": False,
        "chuangta_time": "14:15",
        "enable_lingxiaogong": False,
        "enable_lingxiaogong_wenxintai": True,
        "enable_lingxiaogong_jiutian": True,
        "enable_lingxiaogong_dengtianjie": True,
        "lingxiaogong_poll_interval_seconds": 300,
        "lingxiaogong_wenxintai_after_climb_count": 4,
        "enable_random_event_nanlonghou": True,
        "random_event_nanlonghou_action": ".交换 功法",
        "enable_random_event_jiyin": True,
        "random_event_jiyin_action": ".献上魂魄",
        "zongmen_cmd_dianmao": ".宗门点卯",
        "zongmen_cmd_chuangong": ".宗门传功",
        "zongmen_dianmao_time": "",
        "zongmen_chuangong_times": "",
        "zongmen_chuangong_xinde_text": "今日修行心得：稳中求进。",
        "zongmen_catch_up": True,
        "zongmen_action_spacing_seconds": 20,
    }


def _template_values_for_account(record) -> dict[str, Any]:
    values = record.config.to_dict()
    values["name"] = record.name
    values["enabled"] = record.enabled
    values["identity_profiles"] = [profile.to_dict() for profile in record.config.identities]
    for key, value in list(values.items()):
        if value is None:
            values[key] = ""
    return values


def _form_list(form, name: str) -> list[str]:
    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        return [str(value or "").strip() for value in getlist(name)]
    value = form.get(name)
    if isinstance(value, list):
        return [str(item or "").strip() for item in value]
    return [str(value or "").strip()] if value is not None else []


def _list_get(values: list[str], index: int, default: str = "") -> str:
    if index >= len(values):
        return default
    return values[index].strip()


def _parse_identity_override_value(field: dict[str, str], raw_value: str) -> Any | None:
    value = str(raw_value or "").strip()
    if field["type"] == "checkbox":
        if value == "on":
            return True
        if value == "off":
            return False
        return None
    if not value:
        return None
    if field["type"] == "number":
        try:
            if field.get("step") == "any":
                return float(value)
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field['label']} 必须是数字") from exc
    return value


def _parse_identity_profiles_from_form(form) -> list[dict[str, Any]]:
    keys = _form_list(form, "identity_key")
    if not keys:
        raw_identity_profiles = (form.get("identity_profiles_json") or "").strip()
        identity_profiles = json.loads(raw_identity_profiles) if raw_identity_profiles else []
        if identity_profiles and not isinstance(identity_profiles, list):
            raise ValueError("身份组 JSON 必须是数组")
        return identity_profiles

    kinds = _form_list(form, "identity_kind")
    my_names = _form_list(form, "identity_my_name")
    switch_targets = _form_list(form, "identity_switch_target")
    display_names = _form_list(form, "identity_display_name")
    game_ids = _form_list(form, "identity_game_id")
    tg_usernames = _form_list(form, "identity_tg_username")
    send_as_values = _form_list(form, "identity_send_as")
    override_values = {
        field["name"]: _form_list(form, f"identity_override_{field['name']}")
        for field in IDENTITY_PLUGIN_FIELDS
    }

    profiles: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, raw_key in enumerate(keys):
        kind = _list_get(kinds, index, "avatar") or "avatar"
        if index == 0:
            kind = "main"
        key = raw_key or ("main" if index == 0 else f"avatar_{index}")
        my_name = _list_get(my_names, index)
        switch_target = _list_get(switch_targets, index) or ("主魂" if kind == "main" else my_name)
        display_name = _list_get(display_names, index) or ("主魂" if kind == "main" else my_name)
        game_id = _list_get(game_ids, index)
        tg_username = _list_get(tg_usernames, index).lstrip("@")
        send_as = _list_get(send_as_values, index)
        if not any((key, my_name, switch_target, display_name, game_id, tg_username, send_as)):
            continue
        if kind == "main":
            key = "main"
            send_as = ""
        if kind == "channel" and not send_as:
            raise ValueError(f"频道身份 {key} 必须填写发送频道")
        if key in seen_keys:
            raise ValueError(f"身份 key 重复: {key}")
        seen_keys.add(key)

        config_overrides: dict[str, Any] = {}
        for field in IDENTITY_PLUGIN_FIELDS:
            value = _list_get(override_values[field["name"]], index)
            parsed_override = _parse_identity_override_value(field, value)
            if parsed_override is not None:
                config_overrides[field["name"]] = parsed_override

        profiles.append(
            {
                "key": key,
                "kind": kind,
                "my_name": my_name,
                "switch_target": switch_target,
                "display_name": display_name,
                "game_id": game_id,
                "tg_username": tg_username,
                "send_as": send_as,
                "config_overrides": config_overrides,
            }
        )

    if not profiles:
        raise ValueError("至少需要保留一个主魂身份")
    if profiles[0]["kind"] != "main" or profiles[0]["key"] != "main":
        raise ValueError("第一行身份必须是主魂")
    return profiles


def _template_values_from_form(form) -> dict[str, Any]:
    values = dict(form)
    for key in CHECKBOX_FIELDS | {"enabled"}:
        values[key] = key in form
    try:
        values["identity_profiles"] = _parse_identity_profiles_from_form(form)
    except Exception:
        values["identity_profiles"] = []
    return values


def _inject_shared_config(raw: dict[str, Any], system_config: SystemConfig) -> None:
    raw["tg_api_id"] = system_config.tg_api_id
    raw["tg_api_hash"] = system_config.tg_api_hash
    raw["game_chat_id"] = system_config.game_chat_id
    raw["topic_id"] = system_config.topic_id
    raw["send_to_topic"] = system_config.send_to_topic
    raw["system_reply_source_usernames"] = system_config.system_reply_source_usernames


def _shared_system_settings_values(system_config: SystemConfig) -> dict[str, Any]:
    return {key: getattr(system_config, key) for key in SHARED_SYSTEM_SETTING_KEYS}


def _parse_settings_int(
    value: Any,
    label: str,
    *,
    allow_zero: bool = False,
    positive: bool = False,
) -> int:
    raw = str(value or "").strip()
    if not raw and allow_zero:
        return 0
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是整数") from exc
    if parsed == 0 and allow_zero:
        return parsed
    if parsed == 0:
        raise ValueError(f"{label} 不能为 0")
    if positive and parsed < 0:
        raise ValueError(f"{label} 必须大于 0")
    return parsed


def _system_settings_values_from_form(form) -> dict[str, Any]:
    send_to_topic = "send_to_topic" in form
    values = {
        "tg_api_id": _parse_settings_int(form.get("tg_api_id"), "TG API ID", positive=True),
        "tg_api_hash": str(form.get("tg_api_hash") or "").strip(),
        "game_chat_id": _parse_settings_int(form.get("game_chat_id"), "游戏群 Chat ID"),
        "topic_id": _parse_settings_int(
            form.get("topic_id"),
            "话题 TOPIC_ID",
            allow_zero=not send_to_topic,
            positive=True,
        ),
        "send_to_topic": send_to_topic,
        "system_reply_source_usernames": str(
            form.get("system_reply_source_usernames") or ""
        ).strip(),
    }
    if not values["tg_api_hash"]:
        raise ValueError("TG API HASH 不能为空")
    return values


def _sync_shared_system_settings_to_accounts(
    repository: AccountRepository,
    system_config: SystemConfig,
) -> list[int]:
    shared = _shared_system_settings_values(system_config)
    updated_ids: list[int] = []
    for record in repository.list_accounts():
        changed = any(getattr(record.config, key) != value for key, value in shared.items())
        if not changed:
            continue
        updated_config = replace(record.config, **shared)
        repository.update_account(record.id, record.name, updated_config, enabled=record.enabled)
        updated_ids.append(record.id)
    return updated_ids


def _build_config_from_form(form, system_config: SystemConfig) -> tuple[str, bool, Config]:
    raw: dict[str, Any] = {}
    for section in FORM_SECTIONS:
        for field in section[1]:
            name = field["name"]
            if field["type"] == "checkbox":
                raw[name] = name in form
            else:
                raw[name] = (form.get(name) or "").strip()
    name = (form.get("name") or "").strip()
    enabled = "enabled" in form
    identity_profiles = _parse_identity_profiles_from_form(form)
    raw["state_db_path"] = system_config.app_db_path
    raw["account_name"] = name or system_config.default_account_name
    raw["account_id"] = ""
    raw["identity_profiles"] = identity_profiles
    _inject_shared_config(raw, system_config)
    config = Config.from_mapping(raw)
    return name, enabled, config


def _identity_label_map(records, db_path: str, logger: logging.Logger | None) -> dict[int, str]:
    labels: dict[int, str] = {}
    for record in records:
        state_store = SQLiteStateStore(
            db_path,
            logger,
            account_id=f"{record.id}::__identity__",
        )
        try:
            active_key = str(
                state_store.load_state("__identity_runtime__").get("active_identity_key", "")
            ).strip()
        finally:
            state_store.close()
        identity = record.config.identity_by_key(active_key) or record.config.active_identity
        labels[record.id] = identity.label
    return labels


def _drop_state_keys(state_store: SQLiteStateStore, plugin: str, keys: set[str]) -> bool:
    state = state_store.load_state(plugin)
    if not state:
        return False
    changed = False
    for key in keys:
        if key in state:
            state.pop(key, None)
            changed = True
    if changed:
        state_store.save_state(plugin, state)
    return changed


def _iter_runtime_state_stores(
    *,
    db_path: str,
    account_id: int,
    config: Config,
    logger: logging.Logger | None,
) -> list[SQLiteStateStore]:
    stores = [
        SQLiteStateStore(db_path, logger, account_id=str(account_id)),
    ]
    for identity in config.identities:
        stores.append(
            SQLiteStateStore(
                db_path,
                logger,
                account_id=f"{account_id}:{identity.key}",
            )
        )
    return stores


def _reconcile_runtime_state_for_config_change(
    *,
    db_path: str,
    account_id: int,
    previous_config: Config,
    current_config: Config,
    logger: logging.Logger | None,
) -> None:
    state_stores = _iter_runtime_state_stores(
        db_path=db_path,
        account_id=account_id,
        config=current_config,
        logger=logger,
    )
    try:
        for state_store in state_stores:
            if (
                previous_config.garden_poll_interval_seconds != current_config.garden_poll_interval_seconds
                or previous_config.garden_action_spacing_seconds != current_config.garden_action_spacing_seconds
            ):
                _drop_state_keys(state_store, "garden", {"next_poll_at"})

            if (
                previous_config.xinggong_poll_interval_seconds != current_config.xinggong_poll_interval_seconds
                or previous_config.xinggong_action_spacing_seconds != current_config.xinggong_action_spacing_seconds
            ):
                _drop_state_keys(state_store, "xinggong", {"next_poll_at"})

            if (
                previous_config.enable_xinggong_wenan != current_config.enable_xinggong_wenan
                or previous_config.xinggong_wenan_interval_seconds
                != current_config.xinggong_wenan_interval_seconds
            ):
                _drop_state_keys(state_store, "xinggong", {"wenan_next_at"})

            if (
                previous_config.yuanying_liefeng_interval_seconds
                != current_config.yuanying_liefeng_interval_seconds
                or previous_config.enable_yuanying_liefeng != current_config.enable_yuanying_liefeng
            ):
                state = state_store.load_state("yuanying")
                if state:
                    source = state.get("liefeng_block_source")
                    escape_pause_active = bool(state.get("escape_pause_active", False))
                    if not escape_pause_active and source not in {"cooldown", "weakness"}:
                        _drop_state_keys(
                            state_store,
                            "yuanying",
                            {"liefeng_blocked_until", "liefeng_block_source"},
                        )

            if (
                previous_config.enable_lingxiaogong != current_config.enable_lingxiaogong
                or previous_config.enable_lingxiaogong_wenxintai
                != current_config.enable_lingxiaogong_wenxintai
                or previous_config.enable_lingxiaogong_jiutian
                != current_config.enable_lingxiaogong_jiutian
                or previous_config.enable_lingxiaogong_dengtianjie
                != current_config.enable_lingxiaogong_dengtianjie
                or previous_config.lingxiaogong_poll_interval_seconds
                != current_config.lingxiaogong_poll_interval_seconds
                or previous_config.lingxiaogong_wenxintai_after_climb_count
                != current_config.lingxiaogong_wenxintai_after_climb_count
            ):
                _drop_state_keys(
                    state_store,
                    "lingxiaogong",
                    {"next_status_at", "next_climb_at", "next_jiutian_at"},
                )
    finally:
        for state_store in state_stores:
            state_store.close()


def _auth_token(system_config: SystemConfig) -> str:
    return hmac.new(
        system_config.web_secret_key.encode("utf-8"),
        b"xiuxian-helper-admin",
        sha256,
    ).hexdigest()


def _is_authenticated(request: Request, system_config: SystemConfig) -> bool:
    cookie = request.cookies.get("xiuxian_admin")
    if not cookie:
        return False
    return hmac.compare_digest(cookie, _auth_token(system_config))


def _redirect_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _read_log_tail(path: Path, max_lines: int = 200) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def _parse_int_query(value: str | None, *, minimum: int | None = None) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    if minimum is not None and parsed < minimum:
        return None
    return parsed


def _build_page_url(request: Request, page: int) -> str:
    params = dict(request.query_params)
    params["page"] = str(page)
    encoded = urlencode(params)
    return f"{request.url.path}?{encoded}" if encoded else request.url.path


def _sqlite_storage_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def _format_bytes(size: int) -> str:
    value = float(max(size, 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_message_archive_cleanup(
    repository: MessageArchiveRepository,
    system_config: SystemConfig,
    logger: logging.Logger,
    *,
    now: datetime | None = None,
):
    if not system_config.message_archive_cleanup_enabled:
        return None
    result = repository.cleanup_old_messages(
        retention_days=system_config.message_archive_retention_days,
        now=now or _utc_now(),
        vacuum=system_config.message_archive_vacuum_enabled,
    )
    if result.deleted_count > 0 or result.vacuum_attempted:
        logger.info(
            "message_archive_cleanup before=%s deleted=%s after=%s vacuum_attempted=%s vacuum_succeeded=%s retention_days=%s",
            result.before_count,
            result.deleted_count,
            result.after_count,
            result.vacuum_attempted,
            result.vacuum_succeeded,
            system_config.message_archive_retention_days,
        )
    return result


async def _message_archive_maintenance_loop(
    repository: MessageArchiveRepository,
    system_config: SystemConfig,
    logger: logging.Logger,
) -> None:
    while True:
        await asyncio.sleep(_MESSAGE_ARCHIVE_MAINTENANCE_INTERVAL_SECONDS)
        _run_message_archive_cleanup(repository, system_config, logger)


def create_app() -> FastAPI:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        base_system_config = SystemConfig.load()
        system_settings_repository = SystemSettingsRepository(base_system_config.app_db_path)
        system_config = system_settings_repository.apply_to_config(base_system_config)
        logger = setup_root_logger(system_config)
        repository = AccountRepository(system_config.app_db_path, logger)
        message_archive_repository = MessageArchiveRepository(system_config.app_db_path, logger)
        cleanup_task: asyncio.Task[None] | None = None
        migrated = repository.ensure_legacy_account(system_config)
        if migrated is not None:
            logger.warning("legacy_account_migrated account=%s id=%s", migrated.name, migrated.id)
        _run_message_archive_cleanup(
            message_archive_repository,
            system_config,
            logger,
            now=_utc_now(),
        )
        manager = RunnerManager(repository, system_config)
        await manager.start_enabled_accounts()
        app.state.system_config = system_config
        app.state.logger = logger
        app.state.repository = repository
        app.state.system_settings_repository = system_settings_repository
        app.state.message_archive_repository = message_archive_repository
        app.state.runner_manager = manager
        app.state.tg_login_service = TGLoginService()
        app.state.tg_login_sessions = {}
        if system_config.message_archive_cleanup_enabled:
            cleanup_task = asyncio.create_task(
                _message_archive_maintenance_loop(
                    message_archive_repository,
                    system_config,
                    logger,
                ),
                name="message-archive-maintenance",
            )
        if system_config.web_admin_password == "changeme":
            logger.warning("web_admin_password_is_default please_change_it")
        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                await asyncio.gather(cleanup_task, return_exceptions=True)
            await manager.shutdown()
            message_archive_repository.close()
            system_settings_repository.close()
            repository.close()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _ctx(request: Request, **extra: Any) -> dict[str, Any]:
        _ = request
        return {
            "system_config": app.state.system_config,
            "identity_override_fields": IDENTITY_OVERRIDE_FIELDS,
            "identity_plugin_sections": IDENTITY_PLUGIN_SECTIONS,
            **extra,
        }

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if _is_authenticated(request, system_config):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", _ctx(request, error=""))

    @app.post("/login")
    async def login_submit(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = (form.get("password") or "").strip()
        if (
            username != system_config.web_admin_username
            or not hmac.compare_digest(password, system_config.web_admin_password)
        ):
            return templates.TemplateResponse(
                request,
                "login.html",
                _ctx(request, error="用户名或密码错误"),
                status_code=400,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            "xiuxian_admin",
            _auth_token(system_config),
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request):
        _ = request
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("xiuxian_admin")
        return response

    @app.get("/healthz")
    async def healthz(request: Request):
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        return {
            "status": "ok",
            "accounts": repository.count_accounts(),
            "running_accounts": len(manager.snapshots()),
        }

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        return templates.TemplateResponse(
            request,
            "settings.html",
            _ctx(
                request,
                values=_shared_system_settings_values(system_config),
                error="",
                message="",
            ),
        )

    @app.post("/settings", response_class=HTMLResponse)
    async def settings_update(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        settings_repository: SystemSettingsRepository = request.app.state.system_settings_repository
        form = await request.form()
        values = dict(form)
        values["send_to_topic"] = "send_to_topic" in form
        try:
            parsed_values = _system_settings_values_from_form(form)
            updated_config = replace(system_config, **parsed_values)
            settings_repository.save_shared_settings(parsed_values)
            request.app.state.system_config = updated_config
            update_manager_config = getattr(manager, "update_system_config", None)
            if callable(update_manager_config):
                update_manager_config(updated_config)
            updated_account_ids = _sync_shared_system_settings_to_accounts(
                repository,
                updated_config,
            )
            for account_id in updated_account_ids:
                await manager.sync_account(account_id)
            return templates.TemplateResponse(
                request,
                "settings.html",
                _ctx(
                    request,
                    values=_shared_system_settings_values(updated_config),
                    error="",
                    message=f"已保存应用设置，已同步 {len(updated_account_ids)} 个账号。",
                ),
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "settings.html",
                _ctx(
                    request,
                    values=values,
                    error=str(exc),
                    message="",
                ),
                status_code=400,
            )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        logger: logging.Logger = request.app.state.logger
        accounts = repository.list_accounts()
        snapshots = manager.snapshots()
        active_identity_labels = _identity_label_map(accounts, system_config.app_db_path, logger)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            _ctx(
                request,
                accounts=accounts,
                snapshots=snapshots,
                active_identity_labels=active_identity_labels,
            ),
        )

    def _message_filters_from_request(request: Request, *, fixed_account_id: int | None = None) -> dict[str, Any]:
        query = (request.query_params.get("q") or "").strip()
        raw_event_type = (request.query_params.get("event_type") or "").strip().lower()
        event_type = raw_event_type if raw_event_type in {"new", "edit"} else None
        page = _parse_int_query(request.query_params.get("page"), minimum=1) or 1
        raw_account_id = str(fixed_account_id) if fixed_account_id is not None else (request.query_params.get("account_id") or "").strip()
        return {
            "q": query,
            "account_id": raw_account_id,
            "topic_id": (request.query_params.get("topic_id") or "").strip(),
            "sender_id": (request.query_params.get("sender_id") or "").strip(),
            "event_type": raw_event_type,
            "page": page,
            "parsed_account_id": fixed_account_id if fixed_account_id is not None else _parse_int_query(raw_account_id, minimum=1),
            "parsed_topic_id": _parse_int_query(request.query_params.get("topic_id"), minimum=1),
            "parsed_sender_id": _parse_int_query(request.query_params.get("sender_id"), minimum=1),
            "parsed_event_type": event_type,
        }

    def _render_message_archive_page(
        request: Request,
        *,
        title: str,
        account=None,
    ) -> HTMLResponse:
        repository: AccountRepository = request.app.state.repository
        archive_repository: MessageArchiveRepository = request.app.state.message_archive_repository
        page_size = 50
        fixed_account_id = account.id if account is not None else None
        filters = _message_filters_from_request(request, fixed_account_id=fixed_account_id)
        stats = archive_repository.get_stats(
            account_id=fixed_account_id,
            now=_utc_now(),
        )
        total = archive_repository.count_messages(
            query=filters["q"] or None,
            account_id=filters["parsed_account_id"],
            topic_id=filters["parsed_topic_id"],
            sender_id=filters["parsed_sender_id"],
            event_type=filters["parsed_event_type"],
        )
        offset = (filters["page"] - 1) * page_size
        records = archive_repository.search_messages(
            query=filters["q"] or None,
            account_id=filters["parsed_account_id"],
            topic_id=filters["parsed_topic_id"],
            sender_id=filters["parsed_sender_id"],
            event_type=filters["parsed_event_type"],
            limit=page_size,
            offset=offset,
        )
        accounts = repository.list_accounts()
        account_lookup = {item.id: item for item in accounts}
        identity_lookup = {
            f"{item.id}:{identity.key}": identity.label
            for item in accounts
            for identity in item.config.identities
        }
        prev_url = _build_page_url(request, filters["page"] - 1) if filters["page"] > 1 else None
        next_url = _build_page_url(request, filters["page"] + 1) if offset + len(records) < total else None
        storage_size_label = _format_bytes(_sqlite_storage_bytes(archive_repository.path))
        return templates.TemplateResponse(
            request,
            "messages.html",
            _ctx(
                request,
                title=title,
                account=account,
                accounts=accounts,
                account_lookup=account_lookup,
                identity_lookup=identity_lookup,
                records=records,
                total=total,
                page=filters["page"],
                page_size=page_size,
                stats=stats,
                storage_size_label=storage_size_label,
                filters=filters,
                prev_url=prev_url,
                next_url=next_url,
            ),
        )

    def _render_tg_login_page(
        request: Request,
        *,
        step: str = "phone",
        flow_id: str = "",
        values: dict[str, Any] | None = None,
        error: str = "",
        message: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        login_state = request.app.state.tg_login_sessions.get(flow_id) if flow_id else None
        if values is None:
            values = {
                "account_name": login_state.account_name if login_state else "",
                "tg_session_name": login_state.tg_session_name if login_state else "",
                "phone": login_state.phone if login_state else "",
            }
        return templates.TemplateResponse(
            request,
            "tg_login.html",
            _ctx(
                request,
                step=step,
                flow_id=flow_id,
                values=values,
                login_state=login_state,
                error=error,
                message=message,
            ),
            status_code=status_code,
        )

    def _resolve_tg_login_session_name(system_config: SystemConfig, session_name: str) -> str:
        session_name = session_name.strip()
        if not session_name:
            raise ValueError("Session 名称不能为空")
        return _resolve_session_name(system_config, session_name)

    def _validate_tg_login_shared_config(system_config: SystemConfig) -> None:
        missing: list[str] = []
        if system_config.tg_api_id <= 0:
            missing.append("TG_API_ID")
        if not system_config.tg_api_hash.strip():
            missing.append("TG_API_HASH")
        if system_config.game_chat_id == 0:
            missing.append("GAME_CHAT_ID")
        if system_config.send_to_topic and system_config.topic_id == 0:
            missing.append("TOPIC_ID")
        if missing:
            raise ValueError("请先配置应用级共享参数: " + ", ".join(missing))

    def _build_tg_login_account_config(
        system_config: SystemConfig,
        login_state: _TGLoginSession,
    ) -> Config:
        raw = _template_values_for_new(system_config)
        raw["name"] = login_state.account_name
        raw["tg_session_name"] = login_state.tg_session_name
        raw["account_name"] = login_state.account_name
        raw["account_id"] = ""
        raw["state_db_path"] = system_config.app_db_path
        raw["enabled"] = False
        _inject_shared_config(raw, system_config)
        return Config.from_mapping(raw)

    @app.get("/messages", response_class=HTMLResponse)
    async def message_archive(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        return _render_message_archive_page(
            request,
            title="消息归档",
        )

    @app.get("/accounts/new", response_class=HTMLResponse)
    async def account_new(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        return templates.TemplateResponse(
            request,
            "account_form.html",
            _ctx(
                request,
                title="新增账号",
                action="/accounts/new",
                values=_template_values_for_new(system_config),
                form_sections=FORM_SECTIONS,
                tg_login_entry_url="/accounts/new/tg-login",
                error="",
            ),
        )

    @app.post("/accounts/new")
    async def account_create(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        form = await request.form()
        values = _template_values_from_form(form)
        try:
            name, enabled, config = _build_config_from_form(form, system_config)
            record = repository.create_account(name, config, enabled=enabled)
            return RedirectResponse("/", status_code=303)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "account_form.html",
                _ctx(
                    request,
                    title="新增账号",
                    action="/accounts/new",
                    values=values,
                    form_sections=FORM_SECTIONS,
                    tg_login_entry_url="/accounts/new/tg-login",
                    error=str(exc),
                ),
                status_code=400,
            )

    @app.get("/accounts/new/tg-login", response_class=HTMLResponse)
    async def account_new_tg_login(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        flow_id = (request.query_params.get("flow_id") or "").strip()
        requested_step = (request.query_params.get("step") or "").strip()
        login_state = request.app.state.tg_login_sessions.get(flow_id) if flow_id else None
        step = requested_step or (login_state.step if login_state is not None else "phone")
        if step not in {"phone", "code", "password"}:
            step = "phone"
        if step in {"code", "password"} and login_state is None:
            step = "phone"
            flow_id = ""
        return _render_tg_login_page(request, step=step, flow_id=flow_id)

    @app.post("/accounts/new/tg-login/send-code")
    async def account_new_tg_login_send_code(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        form = await request.form()
        account_name = (form.get("account_name") or "").strip()
        tg_session_name = (form.get("tg_session_name") or "").strip()
        phone = (form.get("phone") or "").strip()
        values = {
            "account_name": account_name,
            "tg_session_name": tg_session_name,
            "phone": phone,
        }
        try:
            if not account_name:
                raise ValueError("账号名称不能为空")
            if any(record.name == account_name for record in repository.list_accounts()):
                raise ValueError(f"账号名称已存在: {account_name}")
            if not phone:
                raise ValueError("手机号不能为空")
            _validate_tg_login_shared_config(system_config)
            resolved_session_name = _resolve_tg_login_session_name(system_config, tg_session_name)
            service: TGLoginService = request.app.state.tg_login_service
            phone_code_hash = await service.send_code(
                api_id=system_config.tg_api_id,
                api_hash=system_config.tg_api_hash,
                session_name=resolved_session_name,
                phone=phone,
            )
            flow_id = secrets.token_urlsafe(16)
            request.app.state.tg_login_sessions[flow_id] = _TGLoginSession(
                flow_id=flow_id,
                account_name=account_name,
                tg_session_name=tg_session_name,
                resolved_session_name=resolved_session_name,
                phone=phone,
                phone_code_hash=phone_code_hash,
            )
            return RedirectResponse(
                f"/accounts/new/tg-login?flow_id={flow_id}&step=code",
                status_code=303,
            )
        except Exception as exc:
            return _render_tg_login_page(
                request,
                step="phone",
                values=values,
                error=str(exc),
                status_code=400,
            )

    @app.post("/accounts/new/tg-login/verify-code")
    async def account_new_tg_login_verify_code(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        form = await request.form()
        flow_id = (form.get("flow_id") or "").strip()
        login_state = request.app.state.tg_login_sessions.get(flow_id) if flow_id else None
        if login_state is None:
            return _render_tg_login_page(
                request,
                step="phone",
                error="请先发送验证码",
                status_code=400,
            )
        code = (form.get("code") or "").strip()
        if not code:
            return _render_tg_login_page(
                request,
                step="code",
                flow_id=flow_id,
                error="验证码不能为空",
                status_code=400,
            )
        try:
            service: TGLoginService = request.app.state.tg_login_service
            result = await service.sign_in_code(
                api_id=system_config.tg_api_id,
                api_hash=system_config.tg_api_hash,
                session_name=login_state.resolved_session_name,
                phone=login_state.phone,
                code=code,
                phone_code_hash=login_state.phone_code_hash,
            )
            if result.password_required:
                login_state.step = "password"
                return RedirectResponse(
                    f"/accounts/new/tg-login?flow_id={flow_id}&step=password",
                    status_code=303,
                )
            if not result.authorized:
                raise ValueError("TG 登录未完成")
            config = _build_tg_login_account_config(system_config, login_state)
            record = repository.create_account(login_state.account_name, config, enabled=False)
            request.app.state.tg_login_sessions.pop(flow_id, None)
            return RedirectResponse(f"/accounts/{record.id}/edit", status_code=303)
        except Exception as exc:
            return _render_tg_login_page(
                request,
                step="code",
                flow_id=flow_id,
                error=str(exc),
                status_code=400,
            )

    @app.post("/accounts/new/tg-login/verify-password")
    async def account_new_tg_login_verify_password(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        form = await request.form()
        flow_id = (form.get("flow_id") or "").strip()
        login_state = request.app.state.tg_login_sessions.get(flow_id) if flow_id else None
        if login_state is None:
            return _render_tg_login_page(
                request,
                step="phone",
                error="请先发送验证码",
                status_code=400,
            )
        password = (form.get("password") or "").strip()
        if not password:
            return _render_tg_login_page(
                request,
                step="password",
                flow_id=flow_id,
                error="两步验证密码不能为空",
                status_code=400,
            )
        try:
            service: TGLoginService = request.app.state.tg_login_service
            result = await service.sign_in_password(
                api_id=system_config.tg_api_id,
                api_hash=system_config.tg_api_hash,
                session_name=login_state.resolved_session_name,
                password=password,
            )
            if not result.authorized:
                raise ValueError("TG 登录未完成")
            config = _build_tg_login_account_config(system_config, login_state)
            record = repository.create_account(login_state.account_name, config, enabled=False)
            request.app.state.tg_login_sessions.pop(flow_id, None)
            return RedirectResponse(f"/accounts/{record.id}/edit", status_code=303)
        except Exception as exc:
            return _render_tg_login_page(
                request,
                step="password",
                flow_id=flow_id,
                error=str(exc),
                status_code=400,
            )

    @app.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
    async def account_edit(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        record = repository.get_account(account_id)
        if record is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request,
            "account_form.html",
            _ctx(
                request,
                title=f"编辑账号 #{account_id}",
                action=f"/accounts/{account_id}/edit",
                values=_template_values_for_account(record),
                form_sections=FORM_SECTIONS,
                send_as_options_url=f"/accounts/{account_id}/send-as-options",
                error="",
            ),
        )

    @app.get("/accounts/{account_id}/send-as-options")
    async def account_send_as_options(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        repository: AccountRepository = request.app.state.repository
        logger: logging.Logger = request.app.state.logger
        record = repository.get_account(account_id)
        if record is None:
            return JSONResponse({"error": "account not found"}, status_code=404)
        config = record.config.with_identity(
            account_id=str(record.id),
            account_name=record.name,
            state_db_path=system_config.app_db_path,
        )
        config = config.with_session_name(
            _resolve_session_name(system_config, config.tg_session_name)
        )
        try:
            options = await list_send_as_options(config, logger)
        except Exception as exc:
            logger.warning("send_as_options_failed account_id=%s error=%s", account_id, exc)
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"options": [option.to_dict() for option in options]}

    @app.post("/accounts/{account_id}/edit")
    async def account_update(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        logger: logging.Logger = request.app.state.logger
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        form = await request.form()
        values = _template_values_from_form(form)
        try:
            previous_record = repository.get_account(account_id)
            if previous_record is None:
                return RedirectResponse("/", status_code=303)
            name, enabled, config = _build_config_from_form(form, system_config)
            repository.update_account(account_id, name, config, enabled=enabled)
            _reconcile_runtime_state_for_config_change(
                db_path=system_config.app_db_path,
                account_id=account_id,
                previous_config=previous_record.config,
                current_config=config,
                logger=logger,
            )
            await manager.sync_account(account_id)
            return RedirectResponse("/", status_code=303)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "account_form.html",
                _ctx(
                    request,
                    title=f"编辑账号 #{account_id}",
                    action=f"/accounts/{account_id}/edit",
                    values=values,
                    form_sections=FORM_SECTIONS,
                    error=str(exc),
                ),
                status_code=400,
            )

    @app.post("/accounts/{account_id}/toggle")
    async def account_toggle(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        record = repository.get_account(account_id)
        if record is not None:
            repository.update_account(
                account_id,
                record.name,
                record.config,
                enabled=not record.enabled,
            )
            await manager.sync_account(account_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/start")
    async def account_start(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        manager: RunnerManager = request.app.state.runner_manager
        await manager.start_account(
            account_id,
            respect_enabled=False,
            clear_runtime_pause=True,
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/stop")
    async def account_stop(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        manager: RunnerManager = request.app.state.runner_manager
        await manager.stop_account(account_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/accounts/{account_id}/delete")
    async def account_delete(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        await manager.stop_account(account_id)
        repository.delete_account(account_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/accounts/{account_id}/logs", response_class=HTMLResponse)
    async def account_logs(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        record = repository.get_account(account_id)
        if record is None:
            return RedirectResponse("/", status_code=303)
        snapshot = manager.snapshot_for(account_id)
        log_path = Path(snapshot.log_path) if snapshot is not None else Path(system_config.log_dir) / f"account_{account_id}.log"
        content = _read_log_tail(log_path)
        return templates.TemplateResponse(
            request,
            "logs.html",
            _ctx(
                request,
                account=record,
                snapshot=snapshot,
                content=content,
            ),
        )

    @app.get("/accounts/{account_id}/messages", response_class=HTMLResponse)
    async def account_messages(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        record = repository.get_account(account_id)
        if record is None:
            return RedirectResponse("/", status_code=303)
        return _render_message_archive_page(
            request,
            title=f"账号消息 #{account_id}",
            account=record,
        )

    return app


def main() -> None:
    system_config = SystemConfig.load()
    uvicorn.run(
        create_app(),
        host=system_config.web_host,
        port=system_config.web_port,
    )
