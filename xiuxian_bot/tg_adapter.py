from __future__ import annotations

import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl import functions, types

from .config import Config
from .core.contracts import MessageContext


class TGAdapter:
    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._client = TelegramClient(config.tg_session_name, config.tg_api_id, config.tg_api_hash)
        self._me_id: int | None = None
        self._peer = None
        self._system_reply_source_ids: set[int] = set()

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

    async def send_message(
        self,
        text: str,
        *,
        reply_to_topic: bool = True,
        reply_to_msg_id: int | None = None,
    ) -> int | None:
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
            )
            result = await self._client(request)
            return self._extract_sent_message_id(result)

        kwargs = {}
        if reply_to_msg_id is not None:
            kwargs["reply_to"] = reply_to_msg_id
        msg = await self._client.send_message(self._config.game_chat_id, text, **kwargs)
        mid = getattr(msg, "id", None)
        return mid if isinstance(mid, int) and mid > 0 else None

    async def build_context(self, event) -> MessageContext:
        text = event.raw_text or ""
        reply_to_msg_id = event.reply_to_msg_id
        sender_id = event.sender_id

        is_reply = bool(getattr(event, "is_reply", False))
        is_reply_to_me = False
        mentions_me = bool(self._config.my_name and self._config.my_name in text)
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
            and self._config.my_name not in text
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
