from __future__ import annotations

import logging
from datetime import datetime

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

    async def start(self) -> None:
        await self._client.start()
        me = await self._client.get_me()
        self._me_id = me.id
        self._peer = await self._client.get_input_entity(self._config.game_chat_id)
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
            # Send into a forum topic without replying to any specific message.
            request = functions.messages.SendMessageRequest(
                peer=self._peer,
                message=text,
                reply_to=types.InputReplyToMessage(
                    reply_to_msg_id=reply_to_msg_id or 0,
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

        is_reply = bool(getattr(event, "is_reply", False))
        is_reply_to_me = False

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

        msg_date = getattr(getattr(event, "message", None), "date", None)
        ts = msg_date if isinstance(msg_date, datetime) else datetime.utcnow()

        return MessageContext(
            chat_id=event.chat_id,
            message_id=event.message.id,
            reply_to_msg_id=reply_to_msg_id,
            sender_id=event.sender_id,
            text=text,
            ts=ts,
            is_reply=is_reply,
            is_reply_to_me=is_reply_to_me,
        )
