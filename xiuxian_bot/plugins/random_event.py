from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..domain.text_normalizer import normalize_match_text


@dataclass(frozen=True)
class _RandomEventDefinition:
    key: str
    enabled: bool
    action: str
    choice_anchors: tuple[str, ...]
    preview_anchors: tuple[str, ...]
    result_anchors: tuple[str, ...]


class AutoRandomEventPlugin:
    name = "random_event"
    priority = 95

    _NANLONGHOU_CHOICE_ANCHORS = (
        normalize_match_text("南陇侯"),
        normalize_match_text("你有10分钟内做出抉择"),
        normalize_match_text("回复本消息"),
        normalize_match_text(".交换法宝"),
        normalize_match_text(".交换功法"),
        normalize_match_text(".拒绝交易"),
    )
    _NANLONGHOU_PREVIEW_ANCHORS = (
        normalize_match_text("天机异象"),
        normalize_match_text("强横神念"),
        normalize_match_text("神念扫过此界"),
        normalize_match_text("洞府附近停留"),
    )
    _NANLONGHOU_RESULT_ANCHORS = (
        normalize_match_text("抉择超时"),
        normalize_match_text("魔君之怒"),
        normalize_match_text("南陇侯的交易"),
        normalize_match_text("强行将其侍妾"),
        normalize_match_text("作为回报"),
    )
    _JIYIN_CHOICE_ANCHORS = (
        normalize_match_text("无法抗拒的意志锁定了你的神魂"),
        normalize_match_text("你必须在180分钟内做出抉择"),
        normalize_match_text("回复本消息"),
        normalize_match_text(".献上魂魄"),
        normalize_match_text(".收敛气息"),
    )
    _JIYIN_PREVIEW_ANCHORS = (
        normalize_match_text("天机异象"),
        normalize_match_text("魔君降临"),
        normalize_match_text("无尽魔海"),
        normalize_match_text("极阴祖师"),
        normalize_match_text("停留了片刻"),
    )
    _JIYIN_RESULT_ANCHORS = (
        normalize_match_text("已做出抉择"),
        normalize_match_text("神念开始对其进行审视"),
        normalize_match_text("神魂成功穿透"),
        normalize_match_text("极阴的欣赏"),
        normalize_match_text("神魂碾压"),
        normalize_match_text("侥幸逃脱"),
    )

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._events = (
            _RandomEventDefinition(
                key="nanlonghou",
                enabled=bool(config.enable_random_event_nanlonghou),
                action=config.random_event_nanlonghou_action.strip() or ".交换 功法",
                choice_anchors=self._NANLONGHOU_CHOICE_ANCHORS,
                preview_anchors=self._NANLONGHOU_PREVIEW_ANCHORS,
                result_anchors=self._NANLONGHOU_RESULT_ANCHORS,
            ),
            _RandomEventDefinition(
                key="jiyin",
                enabled=bool(config.enable_random_event_jiyin),
                action=config.random_event_jiyin_action.strip() or ".献上魂魄",
                choice_anchors=self._JIYIN_CHOICE_ANCHORS,
                preview_anchors=self._JIYIN_PREVIEW_ANCHORS,
                result_anchors=self._JIYIN_RESULT_ANCHORS,
            ),
        )
        self.enabled = any(event.enabled for event in self._events)
        self._handled_choice_keys: set[tuple[str, int]] = set()

    def _matches_identity(self, normalized_text: str) -> bool:
        identity = self._config.active_identity
        return any(token and token in normalized_text for token in identity.normalized_tokens())

    def _is_choice_message(self, normalized_text: str, event: _RandomEventDefinition) -> bool:
        return all(anchor in normalized_text for anchor in event.choice_anchors)

    def _is_preview_message(self, normalized_text: str, event: _RandomEventDefinition) -> bool:
        return all(anchor in normalized_text for anchor in event.preview_anchors)

    def _is_result_message(self, normalized_text: str, event: _RandomEventDefinition) -> bool:
        return any(anchor in normalized_text for anchor in event.result_anchors)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        normalized_text = normalize_match_text(ctx.text)
        if not normalized_text:
            return None
        for event in self._events:
            if not event.enabled:
                continue
            if self._is_preview_message(normalized_text, event):
                self._logger.debug("%s_preview_seen message_id=%s", event.key, ctx.message_id)
                return None
            if self._is_result_message(normalized_text, event):
                self._logger.debug("%s_result_seen message_id=%s", event.key, ctx.message_id)
                return None
            if not self._is_choice_message(normalized_text, event):
                continue
            if not self._matches_identity(normalized_text):
                return None
            choice_key = (event.key, ctx.message_id)
            if choice_key in self._handled_choice_keys:
                return None
            self._handled_choice_keys.add(choice_key)
            return [
                SendAction(
                    plugin=self.name,
                    text=event.action,
                    reply_to_topic=True,
                    reply_to_msg_id=ctx.message_id,
                    key=f"random_event.{event.key}.{ctx.message_id}",
                )
            ]
        return None
