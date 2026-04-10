from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import Config, SystemConfig
from .core.account_repository import AccountRecord, AccountRepository
from .core.dispatcher import Dispatcher
from .core.message_archive_repository import MessageArchiveInput, MessageArchiveRepository
from .core.rate_limit import RateLimiter
from .core.reliable_sender import ReliableSender
from .core.scheduler import Scheduler
from .core.state_store import SQLiteStateStore
from .plugins.biguan import AutoBiguanPlugin
from .plugins.chuangta import AutoChuangtaPlugin
from .plugins.daily import DailyPlugin
from .plugins.garden import AutoGardenPlugin
from .plugins.lingxiaogong import AutoLingxiaogongPlugin
from .plugins.xinggong import AutoXinggongPlugin
from .plugins.yuanying import AutoYuanyingPlugin
from .plugins.zongmen import AutoZongmenPlugin
from .tg_adapter import TGAdapter


class _FocusFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if record.levelno >= logging.WARNING:
            return True
        msg = record.getMessage()
        return msg.startswith(">>") or msg.startswith("<<")


_WS_RE = re.compile(r"\s+")


def _short_text(text: str, max_chars: int = 160) -> str:
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _reset_logger_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def _resolve_session_name(system_config: SystemConfig, session_name: str) -> str:
    raw = (session_name or "").strip()
    if not raw:
        return raw
    root = (system_config.session_root_dir or "").strip()
    session_path = Path(raw).expanduser()
    if not root or session_path.is_absolute():
        return str(session_path)
    base = Path(root).expanduser()
    if not base.is_absolute():
        base = Path.cwd() / base
    base.mkdir(parents=True, exist_ok=True)
    return str(base / raw)


def _in_scope(config: Config, text: str, reply_to_msg_id: int | None, is_reply_to_me: bool) -> bool:
    return (
        reply_to_msg_id == config.topic_id
        or (config.my_name and config.my_name in text)
        or is_reply_to_me
    )


def _extract_topic_id_from_event(event) -> int | None:
    message = getattr(event, "message", None)
    reply_to = getattr(message, "reply_to", None)
    reply_to_top_id = getattr(reply_to, "reply_to_top_id", None)
    if isinstance(reply_to_top_id, int) and reply_to_top_id > 0:
        return reply_to_top_id
    reply_to_msg_id = getattr(event, "reply_to_msg_id", None)
    is_forum_topic = bool(getattr(message, "forum_topic", False)) or bool(getattr(reply_to, "forum_topic", False))
    if is_forum_topic and isinstance(reply_to_msg_id, int) and reply_to_msg_id > 0:
        return reply_to_msg_id
    if bool(getattr(message, "forum_topic", False)):
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int) and message_id > 0:
            return message_id
    return None


def _extract_sender_name_from_event(event) -> str | None:
    sender = getattr(event, "sender", None)
    if sender is None:
        sender = getattr(getattr(event, "message", None), "sender", None)
    username = getattr(sender, "username", None)
    if isinstance(username, str) and username.strip():
        return username.strip()
    parts = []
    for attr in ("first_name", "last_name", "title"):
        value = getattr(sender, attr, None)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return " ".join(parts) or None


def _should_archive_plain_text(event, text: str, topic_id: int | None) -> bool:
    if topic_id is None or not text.strip():
        return False
    message = getattr(event, "message", None)
    return getattr(message, "media", None) is None


def setup_root_logger(system_config: SystemConfig) -> logging.Logger:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.WARNING, format=fmt)
    for noisy in ("telethon", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger("xiuxian_root")
    logger.setLevel(getattr(logging, system_config.log_level, logging.INFO))
    logger.propagate = False
    _reset_logger_handlers(logger)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    return logger


def build_account_logger(system_config: SystemConfig, account: AccountRecord) -> tuple[logging.Logger, Path]:
    log_dir = Path(system_config.log_dir).expanduser()
    if not log_dir.is_absolute():
        log_dir = Path.cwd() / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"account_{account.id}.log"

    logger = logging.getLogger(f"xiuxian_bot.account.{account.id}")
    numeric_level = getattr(logging, account.config.log_level or system_config.log_level, logging.INFO)
    logger.setLevel(numeric_level)
    logger.propagate = False
    _reset_logger_handlers(logger)

    fmt = f"%(asctime)s %(levelname)s [account:{account.name}#{account.id}] %(message)s"
    formatter = logging.Formatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(numeric_level)
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)

    if numeric_level >= logging.INFO and numeric_level != logging.DEBUG:
        stream_handler.addFilter(_FocusFilter())
        file_handler.addFilter(_FocusFilter())

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)
    return logger, log_path


def build_plugins(config: Config, logger: logging.Logger) -> list[object]:
    return [
        AutoBiguanPlugin(config, logger),
        DailyPlugin(config, logger),
        AutoGardenPlugin(config, logger),
        AutoChuangtaPlugin(config, logger),
        AutoLingxiaogongPlugin(config, logger),
        AutoXinggongPlugin(config, logger),
        AutoYuanyingPlugin(config, logger),
        AutoZongmenPlugin(config, logger),
    ]


@dataclass
class RunnerSnapshot:
    account_id: int
    state: str
    message: str
    log_path: str


class AccountRunner:
    def __init__(self, record: AccountRecord, system_config: SystemConfig) -> None:
        self.record = record
        self._system_config = system_config
        self._logger, self._log_path = build_account_logger(system_config, record)
        self._task: asyncio.Task[None] | None = None
        self._state = "stopped"
        self._message = ""
        self._stop_requested = False
        self._clear_runtime_pause_on_start = False

    @property
    def log_path(self) -> Path:
        return self._log_path

    def snapshot(self) -> RunnerSnapshot:
        return RunnerSnapshot(
            account_id=self.record.id,
            state=self._state,
            message=self._message,
            log_path=str(self._log_path),
        )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_requested = False
        self._state = "starting"
        self._message = ""
        self._task = asyncio.create_task(self._run(), name=f"account-runner-{self.record.id}")

    def set_manual_resume(self, enabled: bool) -> None:
        self._clear_runtime_pause_on_start = bool(enabled)

    async def stop(self) -> None:
        self._stop_requested = True
        task = self._task
        if task is None:
            self._state = "stopped"
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None
        self._state = "stopped"

    async def _run(self) -> None:
        config = self.record.config.with_identity(
            account_id=str(self.record.id),
            account_name=self.record.name,
            state_db_path=self._system_config.app_db_path,
        )
        resolved_session_name = _resolve_session_name(
            self._system_config,
            config.tg_session_name,
        )
        if resolved_session_name != config.tg_session_name:
            config = config.with_session_name(resolved_session_name)
        scheduler = Scheduler(self._logger)
        message_archive_repository = MessageArchiveRepository(
            self._system_config.app_db_path,
            self._logger,
        )
        state_store = SQLiteStateStore(
            self._system_config.app_db_path,
            self._logger,
            account_id=str(self.record.id),
        )
        limiter = RateLimiter(
            global_per_minute=config.global_sends_per_minute,
            plugin_per_minute=config.plugin_sends_per_minute,
        )
        adapter = TGAdapter(config, self._logger)
        sender = ReliableSender(
            send_message=adapter.send_message,
            limiter=limiter,
            logger=self._logger,
            dry_run=config.dry_run,
            min_interval_seconds=config.global_send_min_interval_seconds,
        )

        plugins = build_plugins(config, self._logger)
        for plugin in plugins:
            bind = getattr(plugin, "set_state_store", None)
            if callable(bind):
                bind(state_store)
            restore = getattr(plugin, "restore_state", None)
            if callable(restore):
                restore()
        dispatcher = Dispatcher(plugins, self._logger)

        recent_sent_ids: deque[int] = deque(maxlen=50)
        recent_sent_set: set[int] = set()
        xinggong = next(
            (plugin for plugin in plugins if getattr(plugin, "name", "") == "xinggong"),
            None,
        )
        yuanying = next(
            (plugin for plugin in plugins if getattr(plugin, "name", "") == "yuanying"),
            None,
        )
        if self._clear_runtime_pause_on_start and yuanying is not None:
            clear_runtime_pause = getattr(yuanying, "clear_runtime_pause", None)
            if callable(clear_runtime_pause):
                clear_runtime_pause(clear_progress=True)

        def _current_pause_message() -> str | None:
            if yuanying is None or not getattr(yuanying, "enabled", False):
                return None
            pause_reason = getattr(yuanying, "runtime_pause_reason", None)
            if not callable(pause_reason):
                return None
            result = pause_reason()
            return result if isinstance(result, str) and result else None

        pause_mode_active = False

        def _remember_sent(mid: int | None) -> None:
            if mid is None or mid in recent_sent_set:
                return
            if len(recent_sent_ids) == recent_sent_ids.maxlen:
                old = recent_sent_ids.popleft()
                recent_sent_set.discard(old)
            recent_sent_ids.append(mid)
            recent_sent_set.add(mid)

        async def _enter_pause_mode(reason: str) -> None:
            nonlocal pause_mode_active
            if pause_mode_active and self._state == "paused" and self._message == reason:
                return
            pause_mode_active = True
            self._state = "paused"
            self._message = reason
            self._logger.warning("account_paused reason=%s", reason)
            await scheduler.cancel_all()

        async def _send(
            plugin: str,
            text: str,
            reply_to_topic: bool,
            *,
            reply_to_msg_id: int | None = None,
        ) -> int | None:
            pause_message = _current_pause_message()
            if pause_message is not None:
                await _enter_pause_mode(pause_message)
                self._logger.warning(
                    "send_suppressed_for_pause plugin=%s text=%s",
                    plugin,
                    text,
                )
                return None
            while xinggong is not None and getattr(xinggong, "enabled", False):
                wait_seconds = xinggong.send_block_delay_seconds(plugin, text)
                if wait_seconds <= 0:
                    break
                self._logger.debug(
                    "send_suppressed_for_guanxing plugin=%s wait_seconds=%.1f text=%s",
                    plugin,
                    wait_seconds,
                    text,
                )
                await asyncio.sleep(wait_seconds)
            mid = await sender.send(
                plugin,
                text,
                bool(reply_to_topic and config.send_to_topic),
                reply_to_msg_id=reply_to_msg_id,
            )
            _remember_sent(mid)
            return mid

        async def _execute_action(action) -> None:
            pause_message = _current_pause_message()
            if pause_message is not None:
                await _enter_pause_mode(pause_message)
                self._logger.warning(
                    "action_suppressed_for_pause plugin=%s text=%s delay_seconds=%.1f",
                    action.plugin,
                    action.text,
                    action.delay_seconds,
                )
                return
            if action.delay_seconds and action.delay_seconds > 0:
                key = action.key or f"{action.plugin}:{action.text}"

                async def _scheduled() -> None:
                    await _send(action.plugin, action.text, action.reply_to_topic)

                await scheduler.schedule(
                    key=key,
                    delay_seconds=action.delay_seconds,
                    action=_scheduled,
                )
                return
            await _send(action.plugin, action.text, action.reply_to_topic)

        async def _archive_message_event(event, ctx, event_type: str) -> None:
            topic_id = _extract_topic_id_from_event(event)
            if not _should_archive_plain_text(event, ctx.text, topic_id):
                return
            try:
                message_archive_repository.archive_message(
                    MessageArchiveInput(
                        account_id=self.record.id,
                        chat_id=ctx.chat_id,
                        topic_id=topic_id,
                        message_id=ctx.message_id,
                        reply_to_msg_id=ctx.reply_to_msg_id,
                        sender_id=ctx.sender_id,
                        sender_name=_extract_sender_name_from_event(event),
                        raw_text=ctx.text,
                        event_type=event_type,
                        message_ts=ctx.ts,
                        is_reply=ctx.is_reply,
                        is_topic_message=True,
                    )
                )
            except Exception:
                self._logger.exception(
                    "message_archive_failed account_id=%s message_id=%s event_type=%s",
                    self.record.id,
                    ctx.message_id,
                    event_type,
                )

        async def _on_event(event, event_type: str) -> None:
            ctx = await adapter.build_context(event)
            await _archive_message_event(event, ctx, event_type)
            if not _in_scope(config, ctx.text, ctx.reply_to_msg_id, ctx.is_reply_to_me):
                return
            if adapter.me_id is not None and ctx.sender_id != adapter.me_id:
                interesting = (
                    ctx.is_reply_to_me
                    or (ctx.reply_to_msg_id in recent_sent_set)
                    or (config.my_name in ctx.text)
                    or ("周天星斗大阵" in ctx.text)
                    or ("观星台" in ctx.text)
                    or ("星盘显化" in ctx.text)
                    or ("天机阁快报" in ctx.text)
                    or ("天机异动" in ctx.text)
                    or ("星移失败" in ctx.text)
                )
                if interesting:
                    self._logger.info("<< %s", _short_text(ctx.text))

            actions = await dispatcher.dispatch(ctx)
            pause_message = _current_pause_message()
            if pause_message is not None:
                await _enter_pause_mode(pause_message)
                return
            for action in actions:
                await _execute_action(action)

        async def _on_new_message(event) -> None:
            await _on_event(event, "new")

        async def _on_edited_message(event) -> None:
            await _on_event(event, "edit")

        try:
            adapter.on_new_message(_on_new_message)
            adapter.on_message_edited(_on_edited_message)
            await adapter.start()
            pause_message = _current_pause_message()
            if pause_message is not None:
                await _enter_pause_mode(pause_message)
            else:
                for plugin in plugins:
                    if getattr(plugin, "enabled", False):
                        bootstrap = getattr(plugin, "bootstrap", None)
                        if callable(bootstrap):
                            await bootstrap(scheduler, _send)
                self._state = "running"
                self._message = ""
            await adapter.run_forever()
            if not self._stop_requested:
                self._state = "stopped"
        except asyncio.CancelledError:
            self._state = "stopped"
            raise
        except Exception as exc:
            self._state = "error"
            self._message = str(exc)
            self._logger.exception("account_runner_failed account_id=%s", self.record.id)
        finally:
            await scheduler.cancel_all()
            await adapter.stop()
            message_archive_repository.close()
            state_store.close()


class RunnerManager:
    def __init__(self, repository: AccountRepository, system_config: SystemConfig) -> None:
        self._repository = repository
        self._system_config = system_config
        self._runners: dict[int, AccountRunner] = {}
        self._lock = asyncio.Lock()

    async def start_enabled_accounts(self) -> None:
        for record in self._repository.list_accounts():
            if record.enabled:
                await self.start_account(record.id, respect_enabled=True)

    async def shutdown(self) -> None:
        async with self._lock:
            runners = list(self._runners.values())
            self._runners.clear()
        for runner in runners:
            await runner.stop()

    async def start_account(
        self,
        account_id: int,
        *,
        respect_enabled: bool = False,
        clear_runtime_pause: bool = False,
    ) -> None:
        record = self._repository.get_account(account_id)
        if record is None:
            return
        if respect_enabled and not record.enabled:
            return
        async with self._lock:
            existing = self._runners.get(account_id)
            if existing is not None:
                await existing.stop()
            runner = AccountRunner(record, self._system_config)
            set_manual_resume = getattr(runner, "set_manual_resume", None)
            if callable(set_manual_resume):
                set_manual_resume(clear_runtime_pause)
            self._runners[account_id] = runner
            await runner.start()

    async def stop_account(self, account_id: int) -> None:
        async with self._lock:
            runner = self._runners.pop(account_id, None)
        if runner is not None:
            await runner.stop()

    async def sync_account(self, account_id: int) -> None:
        record = self._repository.get_account(account_id)
        if record is None:
            await self.stop_account(account_id)
            return
        if not record.enabled:
            await self.stop_account(account_id)
            return
        await self.start_account(account_id, respect_enabled=False, clear_runtime_pause=False)

    def snapshots(self) -> dict[int, RunnerSnapshot]:
        return {account_id: runner.snapshot() for account_id, runner in self._runners.items()}

    def snapshot_for(self, account_id: int) -> RunnerSnapshot | None:
        runner = self._runners.get(account_id)
        return None if runner is None else runner.snapshot()
