from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import Config, SystemConfig
from .core.account_repository import AccountRecord, AccountRepository
from .core.contracts import MessageContext, SendAction
from .core.dispatcher import Dispatcher
from .core.identity_switch import IdentitySwitchCoordinator, unique_best_identity_match
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
from .plugins.luoyunzong import LuoyunzongPlugin
from .plugins.random_event import AutoRandomEventPlugin
from .plugins.random_text import RandomTextPlugin
from .plugins.shiqie import ShiqiePlugin
from .plugins.wild_explore import WildExplorePlugin
from .plugins.xinggong import AutoXinggongPlugin
from .plugins.yuanying import AutoYuanyingPlugin
from .plugins.zongmen import AutoZongmenPlugin
from .tg_adapter import TGAdapter
from .domain.text_normalizer import normalize_match_text


class _FocusFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if record.levelno >= logging.WARNING:
            return True
        msg = record.getMessage()
        return (
            msg.startswith(">>")
            or msg.startswith("<<")
            or msg.startswith("luoyunzong_")
            or msg.startswith("random_text_")
        )


_WS_RE = re.compile(r"\s+")
_GUANXING_EVENT_ANCHORS = (
    normalize_match_text("星盘显化"),
    normalize_match_text("天机异动"),
)


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
        or any(name and name in text for name in config.all_identity_mentions)
        or is_reply_to_me
    )


def _is_guanxing_route_candidate(text: str) -> bool:
    normalized = normalize_match_text(text)
    return any(anchor and anchor in normalized for anchor in _GUANXING_EVENT_ANCHORS)


def _is_luoyunzong_status_route_candidate(text: str) -> bool:
    return "落云宗" in text and "灵眼之树" in text


def _is_luoyunzong_public_guard_route_candidate(text: str) -> bool:
    return (
        ("古剑门来袭" in text and "护山大阵" in text and ".协同守山" in text)
        or ("守护成功" in text and "古剑门" in text and "成功击退" in text)
    )


def _is_xinggong_qizhen_invite_route_candidate(text: str) -> bool:
    return "周天星斗大阵-启" in text


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


def _extract_media_placeholder(event) -> str | None:
    message = getattr(event, "message", None)
    if message is None:
        return None
    media_markers = (
        ("photo", "[image]"),
        ("sticker", "[sticker]"),
        ("voice", "[voice]"),
        ("audio", "[audio]"),
        ("video_note", "[video_note]"),
        ("video", "[video]"),
        ("gif", "[gif]"),
        ("poll", "[poll]"),
        ("document", "[file]"),
    )
    for attr, marker in media_markers:
        if getattr(message, attr, None) is not None:
            return marker
    if getattr(message, "media", None) is not None:
        return "[media]"
    return None


def _build_archivable_text(event, text: str) -> str:
    content = (text or "").strip()
    media_placeholder = _extract_media_placeholder(event)
    if media_placeholder and content:
        return f"{media_placeholder}\n{content}"
    if media_placeholder:
        return media_placeholder
    return content


def _should_archive_message(archive_text: str, topic_id: int | None) -> bool:
    return bool(topic_id is not None and archive_text.strip())


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
        AutoRandomEventPlugin(config, logger),
        RandomTextPlugin(config, logger),
        ShiqiePlugin(config, logger),
        WildExplorePlugin(config, logger),
        LuoyunzongPlugin(config, logger),
        AutoXinggongPlugin(config, logger),
        AutoYuanyingPlugin(config, logger),
        AutoZongmenPlugin(config, logger),
    ]


@dataclass
class _IdentityRuntime:
    identity_key: str
    config: Config
    plugins: list[object]
    dispatcher: Dispatcher
    state_store: SQLiteStateStore
    xinggong: object | None
    lingxiaogong: object | None
    luoyunzong: object | None
    yuanying: object | None
    random_text: object | None


@dataclass(frozen=True)
class _SentMessageBinding:
    identity_key: str
    plugin: str


class _ScopedScheduler:
    def __init__(self, scheduler: Scheduler, scope: str) -> None:
        self._scheduler = scheduler
        self._scope = scope

    async def schedule(self, *, key: str, delay_seconds: float, action) -> None:  # type: ignore[no-untyped-def]
        await self._scheduler.schedule(
            key=f"{self._scope}:{key}",
            delay_seconds=delay_seconds,
            action=action,
        )


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
        base_config = self.record.config.with_identity(
            account_id=str(self.record.id),
            account_name=self.record.name,
            state_db_path=self._system_config.app_db_path,
        )
        resolved_session_name = _resolve_session_name(self._system_config, base_config.tg_session_name)
        if resolved_session_name != base_config.tg_session_name:
            base_config = base_config.with_session_name(resolved_session_name)

        scheduler = Scheduler(self._logger)
        message_archive_repository = MessageArchiveRepository(self._system_config.app_db_path, self._logger)
        state_store = SQLiteStateStore(
            self._system_config.app_db_path,
            self._logger,
            account_id=str(self.record.id),
        )
        limiter = RateLimiter(
            global_per_minute=base_config.global_sends_per_minute,
            plugin_per_minute=base_config.plugin_sends_per_minute,
        )
        adapter = TGAdapter(
            base_config,
            self._logger,
            identity_name_provider=lambda: base_config.all_identity_mentions,
        )
        sender = ReliableSender(
            send_message=adapter.send_message,
            limiter=limiter,
            logger=self._logger,
            dry_run=base_config.dry_run,
            min_interval_seconds=base_config.global_send_min_interval_seconds,
        )
        identity_switch = IdentitySwitchCoordinator(
            base_config,
            state_store.for_account(f"{self.record.id}::__identity__"),
            self._logger,
            lambda plugin, text, reply_to_topic, *, reply_to_msg_id=None: sender.send(
                plugin,
                text,
                bool(reply_to_topic and base_config.send_to_topic),
                reply_to_msg_id=reply_to_msg_id,
            ),
        )

        runtimes: dict[str, _IdentityRuntime] = {}
        luoyunzong_global_state_store = state_store.for_account("__global__:luoyunzong")
        for identity in base_config.identities:
            identity_config = base_config.apply_identity(identity.key)
            identity_state_store = state_store.for_account(f"{self.record.id}:{identity.key}")
            plugins = build_plugins(identity_config, self._logger)
            for plugin in plugins:
                bind = getattr(plugin, "set_state_store", None)
                if callable(bind):
                    bind(identity_state_store)
                bind_global = getattr(plugin, "set_global_state_store", None)
                if callable(bind_global):
                    bind_global(luoyunzong_global_state_store)
                restore = getattr(plugin, "restore_state", None)
                if callable(restore):
                    restore()
            runtimes[identity.key] = _IdentityRuntime(
                identity_key=identity.key,
                config=identity_config,
                plugins=plugins,
                dispatcher=Dispatcher(plugins, self._logger),
                state_store=identity_state_store,
                xinggong=next((p for p in plugins if getattr(p, "name", "") == "xinggong"), None),
                lingxiaogong=next((p for p in plugins if getattr(p, "name", "") == "lingxiaogong"), None),
                luoyunzong=next((p for p in plugins if getattr(p, "name", "") == "luoyunzong"), None),
                yuanying=next((p for p in plugins if getattr(p, "name", "") == "yuanying"), None),
                random_text=next((p for p in plugins if getattr(p, "name", "") == "random_text"), None),
            )

        if self._clear_runtime_pause_on_start:
            active_runtime = runtimes.get(identity_switch.active_identity_key)
            if active_runtime is not None and active_runtime.yuanying is not None:
                clear_runtime_pause = getattr(active_runtime.yuanying, "clear_runtime_pause", None)
                if callable(clear_runtime_pause):
                    clear_runtime_pause(clear_progress=True)

        recent_sent_ids: deque[int] = deque(maxlen=50)
        recent_sent_bindings: dict[int, _SentMessageBinding] = {}
        xinggong_qizhen_assisted_invite_ids: deque[int] = deque(maxlen=100)
        xinggong_qizhen_assisted_invite_id_set: set[int] = set()
        xinggong_qizhen_assist_lock = asyncio.Lock()
        pause_mode_active = False
        identity_send_lock = asyncio.Lock()

        def _active_runtime() -> _IdentityRuntime:
            runtime = runtimes.get(identity_switch.active_identity_key)
            if runtime is None:
                raise RuntimeError(f"missing runtime for identity={identity_switch.active_identity_key}")
            return runtime

        def _current_pause_message() -> str | None:
            runtime = _active_runtime()
            yuanying = runtime.yuanying
            if yuanying is None or not getattr(yuanying, "enabled", False):
                return None
            pause_reason = getattr(yuanying, "runtime_pause_reason", None)
            if not callable(pause_reason):
                return None
            result = pause_reason()
            return result if isinstance(result, str) and result else None

        def _remember_sent(mid: int | None, *, identity_key: str, plugin: str) -> None:
            if mid is None:
                return
            if mid in recent_sent_bindings:
                recent_sent_bindings[mid] = _SentMessageBinding(identity_key=identity_key, plugin=plugin)
                return
            if len(recent_sent_ids) == recent_sent_ids.maxlen:
                old = recent_sent_ids.popleft()
                recent_sent_bindings.pop(old, None)
            recent_sent_ids.append(mid)
            recent_sent_bindings[mid] = _SentMessageBinding(identity_key=identity_key, plugin=plugin)

        def _binding_for_message_id(message_id: int | None) -> _SentMessageBinding | None:
            if message_id is None:
                return None
            return recent_sent_bindings.get(message_id)

        def _remember_xinggong_qizhen_assist(message_id: int | None) -> None:
            if message_id is None or message_id in xinggong_qizhen_assisted_invite_id_set:
                return
            if len(xinggong_qizhen_assisted_invite_ids) == xinggong_qizhen_assisted_invite_ids.maxlen:
                old = xinggong_qizhen_assisted_invite_ids.popleft()
                xinggong_qizhen_assisted_invite_id_set.discard(old)
            xinggong_qizhen_assisted_invite_ids.append(message_id)
            xinggong_qizhen_assisted_invite_id_set.add(message_id)

        def _should_auto_return_after_send(runtime: _IdentityRuntime, plugin: str, text: str) -> bool:
            plugin_obj = next(
                (item for item in runtime.plugins if getattr(item, "name", "") == plugin),
                None,
            )
            if plugin_obj is None:
                return True
            should_return = getattr(plugin_obj, "should_auto_return_after_send", None)
            if not callable(should_return):
                return True
            return bool(should_return(text))

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
            identity_key: str | None = None,
        ) -> int | None:
            target_key = identity_key or identity_switch.active_identity_key
            runtime = runtimes[target_key]
            identity = runtime.config.active_identity
            is_channel_identity = identity.is_channel
            send_as = identity.send_as.strip() if is_channel_identity else ""
            if is_channel_identity and not send_as:
                self._logger.warning(
                    "channel_identity_missing_send_as identity=%s plugin=%s text=%s",
                    target_key,
                    plugin,
                    text,
                )
                return None
            xinggong = runtime.xinggong
            while xinggong is not None and getattr(xinggong, "enabled", False):
                wait_seconds = xinggong.send_block_delay_seconds(plugin, text)
                if wait_seconds <= 0:
                    break
                self._logger.debug(
                    "send_suppressed_for_guanxing plugin=%s identity=%s wait_seconds=%.1f text=%s",
                    plugin,
                    target_key,
                    wait_seconds,
                    text,
                )
                await asyncio.sleep(wait_seconds)

            async with identity_send_lock:
                if not is_channel_identity and not await identity_switch.ensure_identity(target_key):
                    self._logger.warning("identity_switch_failed target=%s plugin=%s text=%s", target_key, plugin, text)
                    return None
                pause_message = _current_pause_message() if target_key == identity_switch.active_identity_key else None
                if pause_message is not None:
                    await _enter_pause_mode(pause_message)
                    self._logger.warning("send_suppressed_for_pause plugin=%s text=%s", plugin, text)
                    return None
                send_kwargs = {
                    "reply_to_msg_id": reply_to_msg_id,
                    "identity_key": target_key,
                }
                if send_as:
                    send_kwargs["send_as"] = send_as
                mid = await sender.send(
                    plugin,
                    text,
                    bool(reply_to_topic and runtime.config.send_to_topic),
                    **send_kwargs,
                )
                _remember_sent(mid, identity_key=target_key, plugin=plugin)
                random_text = runtime.random_text
                if mid is not None and plugin != "random_text" and random_text is not None:
                    next_message = getattr(random_text, "next_message", None)
                    mark_sent = getattr(random_text, "mark_sent", None)
                    if callable(next_message) and callable(mark_sent):
                        random_text_message = next_message()
                        if isinstance(random_text_message, str) and random_text_message.strip():
                            random_send_kwargs = {"identity_key": target_key}
                            if send_as:
                                random_send_kwargs["send_as"] = send_as
                            random_mid = await sender.send(
                                "random_text",
                                random_text_message.strip(),
                                bool(runtime.config.send_to_topic),
                                **random_send_kwargs,
                            )
                            if random_mid is not None:
                                _remember_sent(
                                    random_mid,
                                    identity_key=target_key,
                                    plugin="random_text",
                                )
                                mark_sent()
                if (
                    mid is not None
                    and target_key != "main"
                    and not is_channel_identity
                    and base_config.auto_return_main_after_avatar_action
                    and base_config.identity_by_key("main") is not None
                    and _should_auto_return_after_send(runtime, plugin, text)
                ):
                    async def _return_main() -> None:
                        async with identity_send_lock:
                            if identity_switch.active_identity_key != target_key:
                                return
                            if not await identity_switch.ensure_identity("main"):
                                self._logger.warning(
                                    "identity_return_main_failed from=%s plugin=%s text=%s",
                                    target_key,
                                    plugin,
                                    text,
                                )

                    await scheduler.schedule(
                        key="__identity__:return_main",
                        delay_seconds=float(base_config.auto_return_main_delay_seconds),
                        action=_return_main,
                    )
                return mid

        async def _execute_action(action, *, identity_key: str) -> None:
            if identity_key == identity_switch.active_identity_key:
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
                    await _send(
                        action.plugin,
                        action.text,
                        action.reply_to_topic,
                        reply_to_msg_id=action.reply_to_msg_id,
                        identity_key=identity_key,
                    )

                await scheduler.schedule(
                    key=f"{identity_key}:{key}",
                    delay_seconds=action.delay_seconds,
                    action=_scheduled,
                )
                return
            await _send(
                action.plugin,
                action.text,
                action.reply_to_topic,
                reply_to_msg_id=action.reply_to_msg_id,
                identity_key=identity_key,
            )

        async def _archive_message_event(event, ctx, event_type: str) -> None:
            if not base_config.enable_message_archive:
                return
            topic_id = _extract_topic_id_from_event(event)
            archive_text = _build_archivable_text(event, ctx.text)
            if not _should_archive_message(archive_text, topic_id):
                return
            identity_key: str | None = None
            if adapter.me_id is not None and ctx.sender_id == adapter.me_id:
                binding = _binding_for_message_id(ctx.message_id)
                if binding is not None:
                    identity_key = binding.identity_key
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
                        identity_key=identity_key,
                        raw_text=archive_text,
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

        def _identify_runtime_from_text(text: str) -> _IdentityRuntime | None:
            normalized = normalize_match_text(text)
            if not normalized:
                return None
            matched = unique_best_identity_match(normalized, base_config.identities)
            return runtimes.get(matched.key) if matched is not None else None

        def _runtime_for_context(ctx) -> _IdentityRuntime:
            binding = _binding_for_message_id(ctx.reply_to_msg_id)
            if binding is not None:
                runtime = runtimes.get(binding.identity_key)
                if runtime is not None:
                    return runtime
            matched = _identify_runtime_from_text(ctx.text)
            if matched is not None:
                return matched
            return _active_runtime()

        def _guanxing_listener_runtime() -> _IdentityRuntime | None:
            for identity in base_config.identities:
                runtime = runtimes.get(identity.key)
                if runtime is None:
                    continue
                if runtime.config.enable_xinggong and runtime.config.enable_xinggong_guanxing:
                    return runtime
            return None

        def _lingxiaogong_status_runtime(text: str) -> _IdentityRuntime | None:
            candidates: list[_IdentityRuntime] = []
            for identity in base_config.identities:
                runtime = runtimes.get(identity.key)
                if runtime is None:
                    continue
                plugin = runtime.lingxiaogong
                if plugin is None or not getattr(plugin, "enabled", False):
                    continue
                expects = getattr(plugin, "expects_status_feedback", None)
                if callable(expects) and expects(text):
                    candidates.append(runtime)
            return candidates[0] if len(candidates) == 1 else None

        async def _dispatch_xinggong_qizhen_invite(ctx: MessageContext) -> list[tuple[SendAction, str]]:
            async with xinggong_qizhen_assist_lock:
                if ctx.message_id in xinggong_qizhen_assisted_invite_id_set:
                    return []
                for identity in base_config.identities:
                    candidate = runtimes.get(identity.key)
                    if candidate is None:
                        continue
                    plugin = candidate.xinggong
                    if plugin is None or not getattr(plugin, "enabled", False):
                        continue
                    actions = await plugin.on_message(ctx)
                    if actions:
                        _remember_xinggong_qizhen_assist(ctx.message_id)
                        return [(action, candidate.identity_key) for action in actions]
            return []

        async def _dispatch_luoyunzong_status(
            ctx: MessageContext,
            *,
            personal_runtime: _IdentityRuntime | None,
        ) -> list[tuple[SendAction, str]]:
            action_items: list[tuple[SendAction, str]] = []
            for identity in base_config.identities:
                candidate = runtimes.get(identity.key)
                if candidate is None:
                    continue
                plugin = candidate.luoyunzong
                if plugin is None or not getattr(plugin, "enabled", False):
                    continue
                if personal_runtime is not None and candidate.identity_key == personal_runtime.identity_key:
                    actions = await candidate.dispatcher.dispatch(ctx)
                else:
                    on_global_status = getattr(plugin, "on_global_status", None)
                    if not callable(on_global_status):
                        continue
                    actions = await on_global_status(ctx)
                if actions:
                    action_items.extend((action, candidate.identity_key) for action in actions)
            return action_items

        async def _on_event(event, event_type: str) -> None:
            ctx = await adapter.build_context(event)
            identity_switch.observe(ctx)
            await _archive_message_event(event, ctx, event_type)
            in_scope = _in_scope(base_config, ctx.text, ctx.reply_to_msg_id, ctx.is_reply_to_me)
            is_bound_reply = _binding_for_message_id(ctx.reply_to_msg_id) is not None
            is_luoyunzong_status = _is_luoyunzong_status_route_candidate(ctx.text)
            is_luoyunzong_public_guard = _is_luoyunzong_public_guard_route_candidate(ctx.text)
            is_luoyunzong_event = is_luoyunzong_status or is_luoyunzong_public_guard
            is_xinggong_qizhen_invite = _is_xinggong_qizhen_invite_route_candidate(ctx.text)
            if not in_scope and not is_bound_reply and not is_luoyunzong_event and not is_xinggong_qizhen_invite:
                return
            runtime = _runtime_for_context(ctx)
            if in_scope:
                runtime = _lingxiaogong_status_runtime(ctx.text) or runtime
                if _is_guanxing_route_candidate(ctx.text):
                    runtime = _guanxing_listener_runtime() or runtime
            if adapter.me_id is not None and ctx.sender_id != adapter.me_id:
                interesting = (
                    ctx.is_reply_to_me
                    or (ctx.reply_to_msg_id in recent_sent_bindings)
                    or any(name and name in ctx.text for name in base_config.all_identity_mentions)
                    or ("周天星斗大阵" in ctx.text)
                    or ("观星台" in ctx.text)
                    or ("星盘显化" in ctx.text)
                    or ("天机阁快报" in ctx.text)
                    or ("天机异动" in ctx.text)
                    or ("星移失败" in ctx.text)
                    or is_luoyunzong_event
                )
                if interesting:
                    self._logger.info("<< %s", _short_text(ctx.text))

            if is_luoyunzong_event:
                action_items = await _dispatch_luoyunzong_status(
                    ctx,
                    personal_runtime=runtime if in_scope and is_luoyunzong_status else None,
                )
            elif is_xinggong_qizhen_invite:
                action_items = await _dispatch_xinggong_qizhen_invite(ctx)
            else:
                actions = await runtime.dispatcher.dispatch(ctx)
                action_items = [(action, runtime.identity_key) for action in actions]
            pause_message = _current_pause_message() if runtime.identity_key == identity_switch.active_identity_key else None
            if pause_message is not None and runtime.identity_key == identity_switch.active_identity_key:
                await _enter_pause_mode(pause_message)
                return
            for action, action_identity_key in action_items:
                await _execute_action(action, identity_key=action_identity_key)

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
                for identity_key, runtime in runtimes.items():
                    scoped_scheduler = _ScopedScheduler(scheduler, identity_key)
                    for plugin in runtime.plugins:
                        if getattr(plugin, "enabled", False):
                            bootstrap = getattr(plugin, "bootstrap", None)
                            if callable(bootstrap):
                                async def _scoped_send(
                                    plugin_name: str,
                                    text: str,
                                    reply_to_topic: bool,
                                    *,
                                    reply_to_msg_id: int | None = None,
                                    _identity_key: str = identity_key,
                                ) -> int | None:
                                    return await _send(
                                        plugin_name,
                                        text,
                                        reply_to_topic,
                                        reply_to_msg_id=reply_to_msg_id,
                                        identity_key=_identity_key,
                                    )
                                await bootstrap(
                                    scoped_scheduler,
                                    _scoped_send,
                                )
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

    def update_system_config(self, system_config: SystemConfig) -> None:
        self._system_config = system_config

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
