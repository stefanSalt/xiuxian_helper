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
from .core.rate_limit import RateLimiter
from .core.reliable_sender import ReliableSender
from .core.scheduler import Scheduler
from .core.state_store import SQLiteStateStore
from .plugins.biguan import AutoBiguanPlugin
from .plugins.chuangta import AutoChuangtaPlugin
from .plugins.daily import DailyPlugin
from .plugins.garden import AutoGardenPlugin
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


def _in_scope(config: Config, text: str, reply_to_msg_id: int | None, is_reply_to_me: bool) -> bool:
    return (
        reply_to_msg_id == config.topic_id
        or (config.my_name and config.my_name in text)
        or is_reply_to_me
    )


def setup_root_logger(system_config: SystemConfig) -> logging.Logger:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.WARNING, format=fmt)
    for noisy in ("telethon", "asyncio", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger("xiuxian_root")
    logger.setLevel(getattr(logging, system_config.log_level, logging.INFO))
    logger.propagate = False
    logger.handlers.clear()
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
    logger.handlers.clear()

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
        scheduler = Scheduler(self._logger)
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

        def _remember_sent(mid: int | None) -> None:
            if mid is None or mid in recent_sent_set:
                return
            if len(recent_sent_ids) == recent_sent_ids.maxlen:
                old = recent_sent_ids.popleft()
                recent_sent_set.discard(old)
            recent_sent_ids.append(mid)
            recent_sent_set.add(mid)

        async def _send(
            plugin: str,
            text: str,
            reply_to_topic: bool,
            *,
            reply_to_msg_id: int | None = None,
        ) -> int | None:
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

        async def _on_event(event) -> None:
            ctx = await adapter.build_context(event)
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
            for action in actions:
                await _execute_action(action)

        try:
            adapter.on_new_message(_on_event)
            adapter.on_message_edited(_on_event)
            await adapter.start()
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

    async def start_account(self, account_id: int, *, respect_enabled: bool = False) -> None:
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
        await self.start_account(account_id, respect_enabled=False)

    def snapshots(self) -> dict[int, RunnerSnapshot]:
        return {account_id: runner.snapshot() for account_id, runner in self._runners.items()}

    def snapshot_for(self, account_id: int) -> RunnerSnapshot | None:
        runner = self._runners.get(account_id)
        return None if runner is None else runner.snapshot()
