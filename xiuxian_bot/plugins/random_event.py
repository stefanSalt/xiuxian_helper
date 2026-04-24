from __future__ import annotations

import logging

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..domain.text_normalizer import normalize_match_text


class AutoRandomEventPlugin:
    name = "random_event"
    priority = 95

    _CHOICE_ANCHORS = (
        normalize_match_text("南陇侯"),
        normalize_match_text("你有10分钟内做出抉择"),
        normalize_match_text("回复本消息"),
        normalize_match_text(".交换法宝"),
        normalize_match_text(".交换功法"),
        normalize_match_text(".拒绝交易"),
    )
    _PREVIEW_ANCHORS = (
        normalize_match_text("天机异象"),
        normalize_match_text("强横神念"),
        normalize_match_text("神念扫过此界"),
        normalize_match_text("洞府附近停留"),
    )
    _RESULT_ANCHORS = (
        normalize_match_text("抉择超时"),
        normalize_match_text("魔君之怒"),
        normalize_match_text("南陇侯的交易"),
        normalize_match_text("强行将其侍妾"),
        normalize_match_text("作为回报"),
    )

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self.enabled = bool(config.enable_random_event_nanlonghou)
        self._action = config.random_event_nanlonghou_action.strip() or ".交换 功法"
        self._handled_choice_message_ids: set[int] = set()

    def _matches_identity(self, normalized_text: str) -> bool:
        identity = self._config.active_identity
        return any(token and token in normalized_text for token in identity.normalized_tokens())

    def _is_choice_message(self, normalized_text: str) -> bool:
        return all(anchor in normalized_text for anchor in self._CHOICE_ANCHORS)

    def _is_preview_message(self, normalized_text: str) -> bool:
        return normalize_match_text("南陇侯") in normalized_text and all(
            anchor in normalized_text for anchor in self._PREVIEW_ANCHORS
        )

    def _is_result_message(self, normalized_text: str) -> bool:
        return any(anchor in normalized_text for anchor in self._RESULT_ANCHORS)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        normalized_text = normalize_match_text(ctx.text)
        if not normalized_text:
            return None
        if self._is_preview_message(normalized_text):
            self._logger.debug("nanlonghou_preview_seen message_id=%s", ctx.message_id)
            return None
        if self._is_result_message(normalized_text):
            self._logger.debug("nanlonghou_result_seen message_id=%s", ctx.message_id)
            return None
        if not self._is_choice_message(normalized_text):
            return None
        if not self._matches_identity(normalized_text):
            return None
        if ctx.message_id in self._handled_choice_message_ids:
            return None
        self._handled_choice_message_ids.add(ctx.message_id)
        return [
            SendAction(
                plugin=self.name,
                text=self._action,
                reply_to_topic=True,
                reply_to_msg_id=ctx.message_id,
                key=f"random_event.nanlonghou.{ctx.message_id}",
            )
        ]
