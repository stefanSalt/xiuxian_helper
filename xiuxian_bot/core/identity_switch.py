from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from ..config import Config, IdentityProfile
from ..core.contracts import MessageContext
from ..core.state_store import SQLiteStateStore
from ..domain.text_normalizer import normalize_match_text

_STATE_PLUGIN = "__identity_runtime__"


@dataclass
class _PendingSwitch:
    target_key: str
    command_msg_id: int | None
    requested_at: datetime
    future: asyncio.Future[bool]


class IdentitySwitchCoordinator:
    def __init__(
        self,
        config: Config,
        state_store: SQLiteStateStore,
        logger: logging.Logger,
        send_message: Callable[..., Awaitable[int | None]],
    ) -> None:
        self._config = config
        self._state_store = state_store
        self._logger = logger
        self._send_message = send_message
        self._switch_lock = asyncio.Lock()
        self._pending_switch: _PendingSwitch | None = None
        persisted_key = str(state_store.load_state(_STATE_PLUGIN).get("active_identity_key", "")).strip()
        persisted = config.identity_by_key(persisted_key)
        self._active_identity_key = persisted.key if persisted is not None else config.active_identity.key
        self._save_state()

    @property
    def active_identity_key(self) -> str:
        return self._active_identity_key

    @property
    def active_identity(self) -> IdentityProfile:
        identity = self._config.identity_by_key(self._active_identity_key)
        return identity if identity is not None else self._config.active_identity

    @property
    def all_identity_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for identity in self._config.identities:
            candidate = identity.my_name.strip()
            if candidate and candidate not in names:
                names.append(candidate)
        return tuple(names)

    def _save_state(self) -> None:
        self._state_store.save_state(
            _STATE_PLUGIN,
            {"active_identity_key": self._active_identity_key},
        )

    def mark_active(self, identity_key: str) -> bool:
        identity = self._config.identity_by_key(identity_key)
        if identity is None:
            return False
        if identity.key == self._active_identity_key:
            return False
        self._active_identity_key = identity.key
        self._save_state()
        self._logger.info("identity_active_changed identity_key=%s label=%s", identity.key, identity.label)
        return True

    def _iter_keywords(self, raw: str) -> tuple[str, ...]:
        tokens: list[str] = []
        for part in str(raw or "").split(","):
            normalized = normalize_match_text(part)
            if normalized and normalized not in tokens:
                tokens.append(normalized)
        return tuple(tokens)

    def _matches_keywords(self, normalized_text: str, raw_keywords: str) -> bool:
        return any(keyword in normalized_text for keyword in self._iter_keywords(raw_keywords))

    def _matches_identity(self, normalized_text: str, identity: IdentityProfile) -> bool:
        return any(token in normalized_text for token in identity.normalized_tokens())

    def observe(self, ctx: MessageContext) -> None:
        normalized_text = normalize_match_text(ctx.text)
        if not normalized_text:
            return

        if self._matches_keywords(normalized_text, self._config.switch_back_success_keywords):
            self.mark_active("main")
            if self._pending_switch is not None and not self._pending_switch.future.done():
                if self._config.identity_by_key(self._pending_switch.target_key) is not None:
                    self._pending_switch.future.set_result(self._pending_switch.target_key == "main")
                self._pending_switch = None
            return

        for identity in self._config.identities:
            if identity.is_main:
                continue
            if self._matches_keywords(normalized_text, self._config.switch_success_keywords) and self._matches_identity(
                normalized_text, identity
            ):
                self.mark_active(identity.key)
                if (
                    self._pending_switch is not None
                    and self._pending_switch.target_key == identity.key
                    and not self._pending_switch.future.done()
                ):
                    self._pending_switch.future.set_result(True)
                    self._pending_switch = None
                return

        pending = self._pending_switch
        if pending is None:
            return
        related = bool(
            ctx.is_effective_reply
            or (pending.command_msg_id is not None and ctx.reply_to_msg_id == pending.command_msg_id)
        )
        if not related:
            return
        if self._matches_keywords(normalized_text, self._config.switch_failure_keywords):
            if not pending.future.done():
                pending.future.set_result(False)
            self._pending_switch = None
            return

        main_identity = self._config.identity_by_key("main")
        if (
            main_identity is not None
            and pending.target_key == "main"
            and self._matches_keywords(normalized_text, self._config.switch_back_success_keywords)
        ):
            self.mark_active("main")
            if not pending.future.done():
                pending.future.set_result(True)
            self._pending_switch = None
            return

        target = self._config.identity_by_key(pending.target_key)
        if (
            target is not None
            and self._matches_keywords(normalized_text, self._config.switch_success_keywords)
            and self._matches_identity(normalized_text, target)
        ):
            self.mark_active(target.key)
            if not pending.future.done():
                pending.future.set_result(True)
            self._pending_switch = None

    def observe_text(self, text: str) -> None:
        ctx = MessageContext(
            chat_id=0,
            message_id=0,
            reply_to_msg_id=None,
            sender_id=None,
            text=text,
            ts=datetime.now(),
            is_reply=False,
            is_reply_to_me=True,
        )
        self.observe(ctx)

    async def ensure_identity(self, identity_key: str, *, timeout_seconds: float = 30.0, retry_delay_seconds: float = 10.0) -> bool:
        target = self._config.identity_by_key(identity_key)
        if target is None:
            raise ValueError(f"Unknown identity key: {identity_key}")
        async with self._switch_lock:
            while True:
                if self._active_identity_key == target.key:
                    return True
                command = self._build_switch_command(target)
                future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                command_msg_id = await self._send_message(
                    "__identity__",
                    command,
                    bool(self._config.send_to_topic),
                )
                self._pending_switch = _PendingSwitch(
                    target_key=target.key,
                    command_msg_id=command_msg_id,
                    requested_at=datetime.now(),
                    future=future,
                )
                try:
                    switched = await asyncio.wait_for(future, timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    switched = False
                    self._logger.warning(
                        "identity_switch_timeout target=%s wait_seconds=%.1f",
                        target.key,
                        timeout_seconds,
                    )
                finally:
                    if self._pending_switch is not None and self._pending_switch.future is future:
                        self._pending_switch = None
                if switched:
                    return True
                await asyncio.sleep(max(1.0, retry_delay_seconds))

    def _build_switch_command(self, identity: IdentityProfile) -> str:
        target = self._config.switch_back_target if identity.is_main else (identity.switch_target or identity.my_name or identity.key)
        return self._config.switch_command_template.format(target=target)
