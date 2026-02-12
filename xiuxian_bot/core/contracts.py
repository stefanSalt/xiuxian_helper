from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class MessageContext:
    chat_id: int
    message_id: int
    reply_to_msg_id: int | None
    sender_id: int | None
    text: str
    ts: datetime
    is_reply: bool
    is_reply_to_me: bool


@dataclass(frozen=True)
class SendAction:
    plugin: str
    text: str
    reply_to_topic: bool = True
    delay_seconds: float = 0.0
    key: str | None = None


class Plugin(Protocol):
    name: str
    enabled: bool
    priority: int

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None: ...

