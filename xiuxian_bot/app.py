from __future__ import annotations

import asyncio
import logging
import re
from collections import deque

from .config import Config
from .core.dispatcher import Dispatcher
from .core.rate_limit import RateLimiter
from .core.scheduler import Scheduler
from .tg_adapter import TGAdapter
from .plugins.biguan import AutoBiguanPlugin
from .plugins.daily import DailyPlugin
from .plugins.garden import AutoGardenPlugin
from .plugins.xinggong import AutoXinggongPlugin
from .plugins.zongmen import AutoZongmenPlugin


class _FocusFilter(logging.Filter):
    """Only show 'interesting' info logs (commands + bot replies).

    - INFO: allow only lines starting with ">>" (sent) or "<<" (received)
    - WARNING/ERROR: always allow

    Users can set LOG_LEVEL=DEBUG to see full logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        if record.levelno >= logging.WARNING:
            return True
        msg = record.getMessage()
        return msg.startswith(">>") or msg.startswith("<<")


def _setup_logging(level: str) -> logging.Logger:
    fmt = "%(asctime)s %(levelname)s %(message)s"

    # Keep third-party logs quiet by default; show warnings/errors only.
    logging.basicConfig(level=logging.WARNING, format=fmt)
    for noisy in ("telethon", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger("xiuxian_bot")
    numeric_level = getattr(logging, level, logging.INFO)
    logger.setLevel(numeric_level)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setLevel(numeric_level)
    handler.setFormatter(logging.Formatter(fmt))
    if numeric_level >= logging.INFO and numeric_level != logging.DEBUG:
        handler.addFilter(_FocusFilter())
    logger.handlers.clear()
    logger.addHandler(handler)
    return logger


_WS_RE = re.compile(r"\s+")


def _short_text(text: str, max_chars: int = 160) -> str:
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _in_scope(config: Config, text: str, reply_to_msg_id: int | None, is_reply_to_me: bool) -> bool:
    # Global scope rule (match current behavior):
    # - messages in the configured topic
    # - OR explicit name mentions
    # - OR replies to my messages
    return (
        reply_to_msg_id == config.topic_id
        or (config.my_name and config.my_name in text)
        or is_reply_to_me
    )


async def run() -> None:
    config = Config.load()
    logger = _setup_logging(config.log_level)

    scheduler = Scheduler(logger)
    limiter = RateLimiter(
        global_per_minute=config.global_sends_per_minute,
        plugin_per_minute=config.plugin_sends_per_minute,
    )

    adapter = TGAdapter(config, logger)

    biguan = AutoBiguanPlugin(config, logger)
    daily = DailyPlugin(config, logger)
    garden = AutoGardenPlugin(config, logger)
    xinggong = AutoXinggongPlugin(config, logger)
    zongmen = AutoZongmenPlugin(config, logger)

    plugins = [
        biguan,
        daily,
        garden,
        xinggong,
        zongmen,
    ]
    dispatcher = Dispatcher(plugins, logger)

    recent_sent_ids: deque[int] = deque(maxlen=50)
    recent_sent_set: set[int] = set()

    def _remember_sent(mid: int | None) -> None:
        if mid is None:
            return
        if mid in recent_sent_set:
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
        reply_to_topic = bool(reply_to_topic and config.send_to_topic)
        if not limiter.allow(plugin):
            logger.warning("rate_limited plugin=%s text=%s", plugin, text)
            return None
        if config.dry_run:
            logger.info(">> %s (dry-run)", text)
            return None
        try:
            mid = await adapter.send_message(text, reply_to_topic=reply_to_topic, reply_to_msg_id=reply_to_msg_id)
        except Exception:
            logger.exception(
                "send_failed plugin=%s text=%s reply_to_topic=%s reply_to_msg_id=%s",
                plugin,
                text,
                reply_to_topic,
                reply_to_msg_id,
            )
            return None
        _remember_sent(mid)
        if reply_to_msg_id is None:
            logger.info(">> %s", text)
        else:
            logger.info(">> %s (reply_to=%s)", text, reply_to_msg_id)
        return mid

    async def _execute_action(action) -> None:
        if action.delay_seconds and action.delay_seconds > 0:
            key = action.key or f"{action.plugin}:{action.text}"

            async def _scheduled() -> None:
                await _send(action.plugin, action.text, action.reply_to_topic)

            logger.debug(
                "scheduled plugin=%s key=%s delay_seconds=%s text=%s",
                action.plugin,
                key,
                action.delay_seconds,
                action.text,
            )
            await scheduler.schedule(key=key, delay_seconds=action.delay_seconds, action=_scheduled)
            return

        await _send(action.plugin, action.text, action.reply_to_topic)

    async def _on_event(event) -> None:
        ctx = await adapter.build_context(event)
        if not _in_scope(config, ctx.text, ctx.reply_to_msg_id, ctx.is_reply_to_me):
            return
        if adapter.me_id is not None and ctx.sender_id != adapter.me_id:
            # Show only messages that are very likely bot replies to our own operations.
            interesting = (
                ctx.is_reply_to_me
                or (ctx.reply_to_msg_id in recent_sent_set)
                or (config.my_name in ctx.text)
                or ("周天星斗大阵" in ctx.text)
                or ("观星台" in ctx.text)
            )
            if interesting:
                logger.info("<< %s", _short_text(ctx.text))

        actions = await dispatcher.dispatch(ctx)
        if actions:
            plugins = ",".join(sorted({a.plugin for a in actions}))
            logger.debug("rx plugins=%s text=%r", plugins, _short_text(ctx.text))
        for action in actions:
            await _execute_action(action)

    adapter.on_new_message(_on_event)
    adapter.on_message_edited(_on_event)

    await adapter.start()
    if config.enable_garden:
        # Bootstrap: send one status command so the plugin can start its poll loop.
        await _send("garden", ".小药园", True)
    if xinggong.enabled:
        # Bootstrap: send one status command so the plugin can start its poll loop.
        await _send("xinggong", ".观星台", True)
        await xinggong.bootstrap(scheduler, _send)
    if zongmen.enabled:
        await zongmen.bootstrap(scheduler, _send)
    try:
        await adapter.run_forever()
    finally:
        await scheduler.cancel_all()
        await adapter.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n已退出。")
        raise SystemExit(0)
    except ValueError as exc:
        print(f"[config error] {exc}")
        print("请先复制 .env.example 为 .env 并填写必要配置，然后重新运行。")
        raise SystemExit(2) from exc
