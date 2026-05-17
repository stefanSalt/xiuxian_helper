from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from telethon import TelegramClient, events, utils
from telethon.errors.rpcbaseerrors import BadRequestError
from telethon.tl import functions, types

from .config import Config
from .core.contracts import MessageContext


@dataclass(frozen=True)
class SendAsOption:
    value: str
    label: str
    peer_id: int | None = None
    username: str = ""
    kind: str = ""
    premium_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "label": self.label,
            "peer_id": self.peer_id,
            "username": self.username,
            "kind": self.kind,
            "premium_required": self.premium_required,
        }


class TGAdapter:
    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        *,
        identity_name_provider: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._client = TelegramClient(config.tg_session_name, config.tg_api_id, config.tg_api_hash)
        self._me_id: int | None = None
        self._peer = None
        self._system_reply_source_ids: set[int] = set()
        self._identity_name_provider = identity_name_provider

    @property
    def me_id(self) -> int | None:
        return self._me_id

    def on_new_message(self, handler) -> None:
        @self._client.on(events.NewMessage(chats=self._config.game_chat_id))
        async def _wrapped(event) -> None:
            await handler(event)

    def on_message_edited(self, handler) -> None:
        @self._client.on(events.MessageEdited(chats=self._config.game_chat_id))
        async def _wrapped(event) -> None:
            await handler(event)

    def _iter_system_reply_source_usernames(self) -> list[str]:
        raw = str(self._config.system_reply_source_usernames or "").strip()
        if not raw:
            return []
        items: list[str] = []
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            token = token.removeprefix("https://t.me/")
            token = token.removeprefix("http://t.me/")
            token = token.removeprefix("t.me/")
            token = token.lstrip("@").strip()
            if token:
                items.append(token)
        return items

    async def _resolve_system_reply_source_ids(self) -> None:
        self._system_reply_source_ids = set()
        for username in self._iter_system_reply_source_usernames():
            try:
                entity = await self._client.get_entity(username)
            except Exception as exc:
                self._logger.warning(
                    "system_reply_source_resolve_failed username=%s error=%s",
                    username,
                    exc,
                )
                continue
            entity_id = getattr(entity, "id", None)
            if isinstance(entity_id, int) and entity_id > 0:
                self._system_reply_source_ids.add(entity_id)
            else:
                self._logger.warning(
                    "system_reply_source_missing_id username=%s entity_type=%s",
                    username,
                    type(entity).__name__,
                )

    async def start(self) -> None:
        await self._client.start()
        me = await self._client.get_me()
        self._me_id = me.id
        self._peer = await self._client.get_input_entity(self._config.game_chat_id)
        await self._resolve_system_reply_source_ids()
        self._logger.info("bound_user my_name=%s me_id=%s", self._config.my_name, me.id)

    async def run_forever(self) -> None:
        await self._client.run_until_disconnected()

    async def stop(self) -> None:
        await self._client.disconnect()

    def _extract_sent_message_id(self, result) -> int | None:
        # messages.SendMessageRequest may return Updates or UpdateShortSentMessage depending on peer type.
        msg_id = getattr(result, "id", None)
        if isinstance(msg_id, int) and msg_id > 0:
            return msg_id

        updates = getattr(result, "updates", None)
        if not updates:
            return None
        for upd in updates:
            msg = getattr(upd, "message", None)
            mid = getattr(msg, "id", None)
            if isinstance(mid, int) and mid > 0:
                return mid
        return None

    def _is_topic_closed_error(self, exc: BadRequestError) -> bool:
        message = str(getattr(exc, "message", "") or exc).upper()
        return "TOPIC_CLOSED" in message

    async def send_message(
        self,
        text: str,
        *,
        reply_to_topic: bool = True,
        reply_to_msg_id: int | None = None,
        send_as: str | None = None,
    ) -> int | None:
        send_as_peer = _coerce_send_as_peer(send_as)
        if reply_to_topic and self._config.send_to_topic:
            topic_reply_to_msg_id = reply_to_msg_id or self._config.topic_id
            # Forum topic messages are anchored to the topic starter message ID.
            request = functions.messages.SendMessageRequest(
                peer=self._peer,
                message=text,
                reply_to=types.InputReplyToMessage(
                    reply_to_msg_id=topic_reply_to_msg_id,
                    top_msg_id=self._config.topic_id,
                ),
                send_as=send_as_peer,
            )
            try:
                result = await self._client(request)
                return self._extract_sent_message_id(result)
            except BadRequestError as exc:
                if reply_to_msg_id is not None or not self._is_topic_closed_error(exc):
                    raise
                self._logger.warning(
                    "topic_closed_fallback chat_id=%s topic_id=%s text=%s",
                    self._config.game_chat_id,
                    self._config.topic_id,
                    text,
                )

        kwargs = {}
        if reply_to_msg_id is not None:
            kwargs["reply_to"] = reply_to_msg_id
        if send_as_peer is not None:
            kwargs["send_as"] = send_as_peer
        msg = await self._client.send_message(self._config.game_chat_id, text, **kwargs)
        mid = getattr(msg, "id", None)
        return mid if isinstance(mid, int) and mid > 0 else None

    async def build_context(self, event) -> MessageContext:
        text = event.raw_text or ""
        reply_to_msg_id = event.reply_to_msg_id
        sender_id = event.sender_id

        is_reply = bool(getattr(event, "is_reply", False))
        is_reply_to_me = False
        identity_names = (
            self._identity_name_provider() if callable(self._identity_name_provider) else self._config.all_identity_mentions
        )
        mentions_me = any(name and name in text for name in identity_names)
        is_from_system_identity = bool(
            isinstance(sender_id, int) and sender_id in self._system_reply_source_ids
        )
        sender = getattr(event, "sender", None)
        if sender is None:
            sender = getattr(getattr(event, "message", None), "sender", None)
        if sender is None and mentions_me:
            get_sender = getattr(event, "get_sender", None)
            if callable(get_sender):
                try:
                    sender = await get_sender()
                except Exception:
                    sender = None
        is_from_bot_sender = bool(getattr(sender, "bot", False))

        # Optimization: forum topic messages are also "replies" to the topic starter.
        # Only fetch the replied-to message when we actually need to disambiguate:
        #   - not the topic root
        #   - no explicit name mention
        if (
            is_reply
            and reply_to_msg_id is not None
            and reply_to_msg_id != self._config.topic_id
            and not mentions_me
        ):
            try:
                reply_msg = await event.get_reply_message()
            except Exception:
                reply_msg = None
            if reply_msg and self._me_id is not None and reply_msg.sender_id == self._me_id:
                is_reply_to_me = True
        is_system_reply = bool(
            (is_from_system_identity or is_from_bot_sender) and (is_reply_to_me or mentions_me)
        )

        msg_date = getattr(getattr(event, "message", None), "date", None)
        ts = msg_date if isinstance(msg_date, datetime) else datetime.now(timezone.utc)

        return MessageContext(
            chat_id=event.chat_id,
            message_id=event.message.id,
            reply_to_msg_id=reply_to_msg_id,
            sender_id=sender_id,
            text=text,
            ts=ts,
            is_reply=is_reply,
            is_reply_to_me=is_reply_to_me,
            is_from_system_identity=is_from_system_identity,
            is_system_reply=is_system_reply,
        )


async def list_send_as_options(config: Config, logger: logging.Logger) -> list[SendAsOption]:
    client = TelegramClient(config.tg_session_name, config.tg_api_id, config.tg_api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TG session 未登录，无法获取发送频道列表")
        peer = await client.get_input_entity(config.game_chat_id)
        result = await client(functions.channels.GetSendAsRequest(peer=peer))
        options = _send_as_options_from_result(result)
        logger.info("send_as_options_loaded count=%s chat_id=%s", len(options), config.game_chat_id)
        return options
    finally:
        await client.disconnect()


def _coerce_send_as_peer(send_as: str | int | None) -> str | int | None:
    if isinstance(send_as, int):
        return send_as
    value = str(send_as or "").strip()
    if not value:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _send_as_options_from_result(result) -> list[SendAsOption]:  # type: ignore[no-untyped-def]
    chats = {getattr(chat, "id", None): chat for chat in getattr(result, "chats", [])}
    users = {getattr(user, "id", None): user for user in getattr(result, "users", [])}
    options: list[SendAsOption] = []
    seen_values: set[str] = set()
    for item in getattr(result, "peers", []):
        peer = getattr(item, "peer", None)
        entity = _entity_for_send_as_peer(peer, chats, users)
        peer_id = _peer_id(peer)
        username = str(getattr(entity, "username", "") or "").strip()
        value = f"@{username.lstrip('@')}" if username else str(peer_id or "")
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        label = _send_as_label(entity, value)
        if getattr(item, "premium_required", False):
            label = f"{label}（需 Premium）"
        options.append(
            SendAsOption(
                value=value,
                label=label,
                peer_id=peer_id,
                username=username,
                kind=_send_as_kind(peer, entity),
                premium_required=bool(getattr(item, "premium_required", False)),
            )
        )
    return options


def _entity_for_send_as_peer(peer, chats, users):  # type: ignore[no-untyped-def]
    if isinstance(peer, types.PeerUser):
        return users.get(peer.user_id)
    if isinstance(peer, types.PeerChat):
        return chats.get(peer.chat_id)
    if isinstance(peer, types.PeerChannel):
        return chats.get(peer.channel_id)
    return None


def _peer_id(peer) -> int | None:  # type: ignore[no-untyped-def]
    try:
        value = utils.get_peer_id(peer)
    except Exception:
        return None
    return value if isinstance(value, int) else None


def _send_as_label(entity, value: str) -> str:  # type: ignore[no-untyped-def]
    title = str(getattr(entity, "title", "") or "").strip()
    if not title:
        first_name = str(getattr(entity, "first_name", "") or "").strip()
        last_name = str(getattr(entity, "last_name", "") or "").strip()
        title = " ".join(part for part in (first_name, last_name) if part)
    if not title:
        title = value
    username = str(getattr(entity, "username", "") or "").strip()
    suffix = f"@{username.lstrip('@')}" if username else value
    return title if title == suffix else f"{title} ({suffix})"


def _send_as_kind(peer, entity) -> str:  # type: ignore[no-untyped-def]
    if isinstance(peer, types.PeerUser):
        return "user"
    if isinstance(entity, types.Channel):
        return "channel"
    if isinstance(peer, types.PeerChat):
        return "chat"
    return "peer"
