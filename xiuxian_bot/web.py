from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Config, SystemConfig
from .core.account_repository import AccountRepository
from .runtime import RunnerManager, setup_root_logger


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

CHECKBOX_FIELDS = {
    "send_to_topic",
    "dry_run",
    "enable_biguan",
    "enable_daily",
    "enable_garden",
    "enable_xinggong",
    "enable_yuanying",
    "enable_zongmen",
    "zongmen_catch_up",
    "enable_xinggong_wenan",
    "enable_xinggong_deep_biguan",
    "enable_xinggong_guanxing",
    "enable_chuangta",
}

FORM_SECTIONS: list[tuple[str, list[dict[str, str]]]] = [
    (
        "账号基础",
        [
            {"name": "name", "label": "账号名称", "type": "text"},
            {"name": "tg_api_id", "label": "TG API ID", "type": "number"},
            {"name": "tg_api_hash", "label": "TG API HASH", "type": "text"},
            {"name": "tg_session_name", "label": "Session 名称", "type": "text"},
            {"name": "game_chat_id", "label": "群组 Chat ID", "type": "number"},
            {"name": "topic_id", "label": "话题 TOPIC_ID", "type": "number"},
            {"name": "my_name", "label": "游戏名 / @名", "type": "text"},
            {"name": "send_to_topic", "label": "发送到指定话题", "type": "checkbox"},
            {"name": "enabled", "label": "保存后自动启用", "type": "checkbox"},
            {"name": "dry_run", "label": "Dry Run", "type": "checkbox"},
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
            {"name": "enable_daily", "label": "Daily 占位插件", "type": "checkbox"},
            {"name": "enable_garden", "label": "自动种植", "type": "checkbox"},
            {"name": "enable_xinggong", "label": "星宫", "type": "checkbox"},
            {"name": "enable_yuanying", "label": "元婴", "type": "checkbox"},
            {"name": "enable_chuangta", "label": "闯塔", "type": "checkbox"},
            {"name": "enable_zongmen", "label": "宗门", "type": "checkbox"},
        ],
    ),
    (
        "闭关",
        [
            {"name": "action_cmd_biguan", "label": "闭关指令", "type": "text"},
            {"name": "biguan_extra_buffer_seconds", "label": "闭关额外缓冲(秒)", "type": "number"},
            {"name": "biguan_cooldown_jitter_min_seconds", "label": "闭关随机最小(秒)", "type": "number"},
            {"name": "biguan_cooldown_jitter_max_seconds", "label": "闭关随机最大(秒)", "type": "number"},
            {"name": "biguan_retry_jitter_min_seconds", "label": "闭关重试最小(秒)", "type": "number"},
            {"name": "biguan_retry_jitter_max_seconds", "label": "闭关重试最大(秒)", "type": "number"},
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
            {"name": "xinggong_guanxing_shift_advance_seconds", "label": "改换提前量(秒)", "type": "number"},
            {"name": "xinggong_guanxing_watch_events", "label": "监听事件", "type": "text"},
        ],
    ),
    (
        "元婴 / 闯塔 / 宗门",
        [
            {"name": "yuanying_liefeng_interval_seconds", "label": "探寻裂缝间隔(秒)", "type": "number"},
            {"name": "yuanying_chuqiao_interval_seconds", "label": "元婴出窍间隔(秒)", "type": "number"},
            {"name": "chuangta_time", "label": "闯塔时间", "type": "text"},
            {"name": "zongmen_cmd_dianmao", "label": "点卯指令", "type": "text"},
            {"name": "zongmen_dianmao_time", "label": "点卯时间", "type": "text"},
            {"name": "zongmen_cmd_chuangong", "label": "传功指令", "type": "text"},
            {"name": "zongmen_chuangong_times", "label": "传功时间列表", "type": "text"},
            {"name": "zongmen_chuangong_xinde_text", "label": "传功心得文本", "type": "text"},
            {"name": "zongmen_catch_up", "label": "启动补做", "type": "checkbox"},
            {"name": "zongmen_action_spacing_seconds", "label": "宗门动作间隔(秒)", "type": "number"},
        ],
    ),
]


def _template_values_for_new(system_config: SystemConfig) -> dict[str, Any]:
    return {
        "name": "",
        "tg_api_id": "",
        "tg_api_hash": "",
        "tg_session_name": "",
        "game_chat_id": "",
        "topic_id": "",
        "my_name": "",
        "send_to_topic": True,
        "enabled": True,
        "dry_run": False,
        "log_level": system_config.log_level,
        "global_sends_per_minute": 6,
        "plugin_sends_per_minute": 3,
        "enable_biguan": True,
        "enable_daily": False,
        "enable_garden": False,
        "enable_xinggong": False,
        "enable_yuanying": False,
        "enable_zongmen": False,
        "action_cmd_biguan": ".闭关修炼",
        "biguan_extra_buffer_seconds": 60,
        "biguan_cooldown_jitter_min_seconds": 5,
        "biguan_cooldown_jitter_max_seconds": 15,
        "biguan_retry_jitter_min_seconds": 3,
        "biguan_retry_jitter_max_seconds": 8,
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
        "xinggong_guanxing_target_username": "salt9527",
        "xinggong_guanxing_preview_advance_seconds": 180,
        "xinggong_guanxing_shift_advance_seconds": 1,
        "xinggong_guanxing_watch_events": "星辰异象,地磁暴动",
        "yuanying_liefeng_interval_seconds": 43200,
        "yuanying_chuqiao_interval_seconds": 28800,
        "enable_chuangta": False,
        "chuangta_time": "14:15",
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
    for key, value in list(values.items()):
        if value is None:
            values[key] = ""
    return values


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
    raw["state_db_path"] = system_config.app_db_path
    raw["account_name"] = name or system_config.default_account_name
    raw["account_id"] = ""
    config = Config.from_mapping(raw)
    return name, enabled, config


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


def create_app() -> FastAPI:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        system_config = SystemConfig.load()
        logger = setup_root_logger(system_config)
        repository = AccountRepository(system_config.app_db_path, logger)
        migrated = repository.ensure_legacy_account(system_config)
        if migrated is not None:
            logger.warning("legacy_account_migrated account=%s id=%s", migrated.name, migrated.id)
        manager = RunnerManager(repository, system_config)
        await manager.start_enabled_accounts()
        app.state.system_config = system_config
        app.state.logger = logger
        app.state.repository = repository
        app.state.runner_manager = manager
        if system_config.web_admin_password == "changeme":
            logger.warning("web_admin_password_is_default please_change_it")
        try:
            yield
        finally:
            await manager.shutdown()
            repository.close()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _ctx(request: Request, **extra: Any) -> dict[str, Any]:
        _ = request
        return {
            "system_config": app.state.system_config,
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

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        accounts = repository.list_accounts()
        snapshots = manager.snapshots()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            _ctx(request, accounts=accounts, snapshots=snapshots),
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
                title="新增账号",
                action="/accounts/new",
                values=_template_values_for_new(system_config),
                form_sections=FORM_SECTIONS,
                error="",
            ),
        )

    @app.post("/accounts/new")
    async def account_create(request: Request):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        form = await request.form()
        values = dict(form)
        for key in CHECKBOX_FIELDS | {"enabled"}:
            values[key] = key in form
        try:
            name, enabled, config = _build_config_from_form(form, system_config)
            record = repository.create_account(name, config, enabled=enabled)
            if record.enabled:
                await manager.start_account(record.id, respect_enabled=True)
            return RedirectResponse("/", status_code=303)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "account_form.html",
                _ctx(
                    title="新增账号",
                    action="/accounts/new",
                    values=values,
                    form_sections=FORM_SECTIONS,
                    error=str(exc),
                ),
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
                title=f"编辑账号 #{account_id}",
                action=f"/accounts/{account_id}/edit",
                values=_template_values_for_account(record),
                form_sections=FORM_SECTIONS,
                error="",
            ),
        )

    @app.post("/accounts/{account_id}/edit")
    async def account_update(request: Request, account_id: int):
        system_config: SystemConfig = request.app.state.system_config
        if not _is_authenticated(request, system_config):
            return _redirect_login()
        repository: AccountRepository = request.app.state.repository
        manager: RunnerManager = request.app.state.runner_manager
        form = await request.form()
        values = dict(form)
        for key in CHECKBOX_FIELDS | {"enabled"}:
            values[key] = key in form
        try:
            name, enabled, config = _build_config_from_form(form, system_config)
            repository.update_account(account_id, name, config, enabled=enabled)
            await manager.sync_account(account_id)
            return RedirectResponse("/", status_code=303)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "account_form.html",
                _ctx(
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
        await manager.start_account(account_id, respect_enabled=False)
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
                account=record,
                snapshot=snapshot,
                content=content,
            ),
        )

    return app


def main() -> None:
    system_config = SystemConfig.load()
    uvicorn.run(
        create_app(),
        host=system_config.web_host,
        port=system_config.web_port,
    )
