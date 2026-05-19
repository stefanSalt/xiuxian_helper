from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Awaitable

from ..config import Config
from ..core.contracts import MessageContext, SendAction
from ..core.scheduler import Scheduler
from ..core.state_store import SQLiteStateStore, deserialize_datetime, serialize_datetime

SendFn = Callable[[str, str, bool], Awaitable[int | None]]
NowFn = Callable[[], datetime]


class QilingPlugin:
    """器灵自动化：发现本命器灵，并按独立开关执行抚摸、温养、静修试炼。"""

    name = "qiling"
    priority = 10

    _CMD_LIST = ".我的器灵"
    _CMD_TOUCH = ".抚摸法宝"
    _CMD_NURTURE = ".温养器灵"
    _CMD_TRIAL = ".器灵试炼"

    _STATE_KEY = "qiling"
    _LOOP_KEY = "qiling.loop"
    _ACTION_SPACING_SECONDS = 15
    _DISCOVERY_INTERVAL_SECONDS = 3600
    _PENDING_ACTION_TTL_SECONDS = 5 * 60
    _TOUCH_COOLDOWN_SECONDS = 2 * 3600
    _NURTURE_COOLDOWN_SECONDS = 6 * 3600
    _TRIAL_COOLDOWN_SECONDS = 8 * 3600
    _MISSING_ARTIFACT_RETRY_SECONDS = 24 * 3600
    _VALID_TRIAL_ROUTES = {"静修", "寻宝", "斗战"}
    _DATETIME_KEYS = (
        "touch_next_at",
        "nurture_next_at",
        "trial_next_at",
        "protect_next_at",
        "invalid_until",
    )

    def __init__(
        self,
        config: Config,
        logger: logging.Logger,
        *,
        now_fn: NowFn | None = None,
    ) -> None:
        self._logger = logger
        self.enabled = bool(getattr(config, "enable_qiling", False))
        self._identity_key = str(getattr(config, "active_identity_key", "main") or "main")
        self._configured_artifacts = self._split_names(
            str(getattr(config, "qiling_artifact_names", "") or "")
        )
        self._touch_enabled = bool(getattr(config, "qiling_enable_touch", True))
        self._nurture_enabled = bool(getattr(config, "qiling_enable_nurture", True))
        self._trial_enabled = bool(getattr(config, "qiling_enable_trial", True))
        trial_route = str(getattr(config, "qiling_trial_route", "静修") or "").strip() or "静修"
        self._trial_route = trial_route if trial_route in self._VALID_TRIAL_ROUTES else "静修"

        self._scheduler: Scheduler | None = None
        self._send: SendFn | None = None
        self._state_store: SQLiteStateStore | None = None
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

        self._discovered_artifacts: list[str] = []
        self._states: dict[str, dict[str, datetime | None]] = {}
        self._pending_action: str | None = None
        self._pending_artifact: str | None = None
        self._pending_action_started_at: datetime | None = None

    def set_state_store(self, store: SQLiteStateStore) -> None:
        self._state_store = store

    def restore_state(self) -> None:
        if self._state_store is None:
            return
        state = self._state_store.load_state(self._STATE_KEY)
        raw_names = state.get("discovered_artifacts", [])
        if isinstance(raw_names, list):
            self._discovered_artifacts = self._dedupe_names(str(item) for item in raw_names)
        raw_artifacts = state.get("artifacts", {})
        if isinstance(raw_artifacts, dict):
            for raw_name, raw_state in raw_artifacts.items():
                name = str(raw_name or "").strip()
                if not name or not isinstance(raw_state, dict):
                    continue
                artifact_state = self._artifact_state(name)
                for key in self._DATETIME_KEYS:
                    artifact_state[key] = deserialize_datetime(raw_state.get(key))
        for name in self._configured_artifacts:
            self._artifact_state(name)

    async def bootstrap(self, scheduler: Scheduler, send: SendFn) -> None:
        if not self.enabled or not self._any_action_enabled():
            return
        self._scheduler = scheduler
        self._send = send
        for name in self._configured_artifacts:
            self._artifact_state(name)
        await self._schedule_loop(0.0)

    async def on_message(self, ctx: MessageContext) -> list[SendAction] | None:
        if not self.enabled:
            return None
        text = (ctx.text or "").strip()
        if not text:
            return None

        parsed_names = self._parse_artifact_list(text)
        if parsed_names is not None:
            if not self._configured_artifacts:
                self._discovered_artifacts = parsed_names
            for name in parsed_names:
                self._artifact_state(name)
            self._save_state()
            await self._schedule_loop(0.0 if parsed_names else float(self._DISCOVERY_INTERVAL_SECONDS))
            self._logger.info(
                "qiling_artifacts_updated identity=%s artifacts=%s",
                self._identity_key,
                ",".join(parsed_names) or "-",
            )
            return None

        if "你尚未唤醒任何法宝器灵" in text:
            if not self._configured_artifacts:
                self._discovered_artifacts = []
            self._save_state()
            await self._schedule_loop(float(self._DISCOVERY_INTERVAL_SECONDS))
            return None

        if self._parse_detail_status(text):
            self._save_state()
            await self._schedule_loop(self._next_loop_delay_seconds())
            return None

        if self._feedback_kind(text) is not None:
            await self._handle_feedback(text)
            return None

        return None

    async def _loop(self) -> None:
        if not self.enabled or self._send is None:
            return
        if self._pending_action is not None and not self._expire_pending_action():
            await self._schedule_loop(float(self._ACTION_SPACING_SECONDS))
            return

        names = self._artifact_names()
        if not names:
            await self._send(self.name, self._CMD_LIST, True)
            await self._schedule_loop(float(self._DISCOVERY_INTERVAL_SECONDS))
            return

        due = self._next_due_action()
        if due is None:
            await self._schedule_loop(self._next_loop_delay_seconds())
            return

        action, artifact, command = due
        self._set_pending_action(action, artifact)
        self._save_state()
        await self._send(self.name, command, True)
        await self._schedule_loop(float(self._ACTION_SPACING_SECONDS))

    async def _schedule_loop(self, delay_seconds: float) -> None:
        if self._scheduler is None:
            return

        async def _runner() -> None:
            await self._loop()

        await self._scheduler.schedule(
            key=self._LOOP_KEY,
            delay_seconds=max(0.0, float(delay_seconds)),
            action=_runner,
        )

    def _next_due_action(self) -> tuple[str, str, str] | None:
        now = self._now()
        for artifact in self._artifact_names():
            state = self._artifact_state(artifact)
            invalid_until = state.get("invalid_until")
            if invalid_until is not None and invalid_until > now:
                continue
            for action in ("touch", "nurture", "trial"):
                if not self._action_enabled(action):
                    continue
                next_at = state.get(f"{action}_next_at")
                if next_at is None or next_at <= now:
                    return action, artifact, self._command(action, artifact)
        return None

    def _next_loop_delay_seconds(self) -> float:
        now = self._now()
        names = self._artifact_names()
        if not names:
            return float(self._DISCOVERY_INTERVAL_SECONDS)
        delays: list[float] = []
        for artifact in names:
            state = self._artifact_state(artifact)
            invalid_until = state.get("invalid_until")
            if invalid_until is not None and invalid_until > now:
                delays.append((invalid_until - now).total_seconds())
                continue
            for action in ("touch", "nurture", "trial"):
                if not self._action_enabled(action):
                    continue
                next_at = state.get(f"{action}_next_at")
                if next_at is None or next_at <= now:
                    return 0.0
                delays.append((next_at - now).total_seconds())
        if not delays:
            return float(self._DISCOVERY_INTERVAL_SECONDS)
        return max(0.0, min(delays))

    def _command(self, action: str, artifact: str) -> str:
        if action == "touch":
            return f"{self._CMD_TOUCH} {artifact}"
        if action == "nurture":
            return f"{self._CMD_NURTURE} {artifact}"
        return f"{self._CMD_TRIAL} {artifact} {self._trial_route}"

    async def _handle_feedback(self, text: str) -> None:
        kind = self._feedback_kind(text)
        if kind is None:
            return
        artifact = self._pending_artifact
        if artifact is None:
            names = self._artifact_names()
            artifact = names[0] if len(names) == 1 else None
        if artifact is None:
            return

        state = self._artifact_state(artifact)
        remaining: int | None = None
        now = self._now()
        if kind == "touch_cooldown":
            remaining = self._parse_duration_seconds(text)
            state["touch_next_at"] = now + timedelta(
                seconds=remaining if remaining is not None else self._TOUCH_COOLDOWN_SECONDS
            )
        elif kind == "touch_success":
            state["touch_next_at"] = now + timedelta(seconds=self._TOUCH_COOLDOWN_SECONDS)
        elif kind == "nurture_cooldown":
            remaining = self._parse_duration_seconds(text)
            state["nurture_next_at"] = now + timedelta(
                seconds=remaining if remaining is not None else self._NURTURE_COOLDOWN_SECONDS
            )
        elif kind == "nurture_success":
            state["nurture_next_at"] = now + timedelta(seconds=self._NURTURE_COOLDOWN_SECONDS)
        elif kind == "nurture_resource_missing":
            state["nurture_next_at"] = now + timedelta(seconds=self._NURTURE_COOLDOWN_SECONDS)
        elif kind == "trial_cooldown":
            remaining = self._parse_duration_seconds(text)
            state["trial_next_at"] = now + timedelta(
                seconds=remaining if remaining is not None else self._TRIAL_COOLDOWN_SECONDS
            )
        elif kind == "trial_success":
            remaining = self._parse_trial_success_cooldown_seconds(text)
            state["trial_next_at"] = now + timedelta(
                seconds=remaining if remaining is not None else self._TRIAL_COOLDOWN_SECONDS
            )
        elif kind == "missing_artifact":
            retry_at = now + timedelta(seconds=self._MISSING_ARTIFACT_RETRY_SECONDS)
            state["touch_next_at"] = retry_at
            state["nurture_next_at"] = retry_at
            state["trial_next_at"] = retry_at
            state["invalid_until"] = retry_at
        else:
            return

        self._clear_pending_action()
        self._save_state()
        await self._schedule_loop(self._next_loop_delay_seconds())
        self._logger.info(
            "qiling_feedback identity=%s artifact=%s kind=%s next_delay=%.1f",
            self._identity_key,
            artifact,
            kind,
            self._next_loop_delay_seconds(),
        )

    def _feedback_kind(self, text: str) -> str | None:
        if "器灵也是需要休息" in text and "再与它互动" in text:
            return "touch_cooldown"
        if self._looks_like_touch_success(text):
            return "touch_success"
        if "器灵方才吞纳过灵机" in text and "再行温养" in text:
            return "nurture_cooldown"
        if "温养器灵需要" in text and "尚缺" in text:
            return "nurture_resource_missing"
        if "【温养器灵】" in text:
            return "nurture_success"
        if "器灵试炼刚结束不久" in text and "再启程" in text:
            return "trial_cooldown"
        if "【器灵试炼·" in text:
            return "trial_success"
        if "你没有这件拥有器灵的法宝" in text or "器灵并未回应这个名字" in text:
            return "missing_artifact"
        return None

    def _looks_like_touch_success(self, text: str) -> bool:
        if "【温养器灵】" in text or "【器灵试炼" in text:
            return False
        normalized = re.sub(r"\s+", "", text)
        return "默契+" in normalized and "经验+" in normalized

    def _parse_artifact_list(self, text: str) -> list[str] | None:
        if "【本命器灵录】" not in text:
            return None
        names: list[str] = []
        for match in re.finditer(r"依附于:\s*(.+?)\)\s*-\s*状态", text):
            name = match.group(1).strip()
            if name:
                names.append(name)
        return self._dedupe_names(names)

    def _parse_detail_status(self, text: str) -> bool:
        if "【器灵玉鉴】" not in text:
            return False
        artifact = self._parse_detail_artifact_name(text)
        if not artifact:
            return False
        self._remember_discovered_artifact(artifact)
        state = self._artifact_state(artifact)
        field_map = {
            "温养": "nurture_next_at",
            "试炼": "trial_next_at",
            "护主": "protect_next_at",
        }
        parsed = False
        for label, key in field_map.items():
            match = re.search(rf"{label}状态[:：]\s*([^\n\r]+)", text)
            if match is None:
                continue
            value = match.group(1).strip()
            if "此刻可" in value or "可温养" in value or "可试炼" in value or "可催发" in value:
                state[key] = None
                parsed = True
                continue
            remaining = self._parse_duration_seconds(value)
            if remaining is not None:
                state[key] = self._now() + timedelta(seconds=remaining)
                parsed = True
        return parsed

    def _parse_detail_artifact_name(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if "【器灵玉鉴】" not in line:
                continue
            candidate = line.replace("【器灵玉鉴】", "").strip()
            if not candidate and index + 1 < len(lines):
                candidate = lines[index + 1].strip()
            return re.sub(r"^[^\u3400-\u9fffA-Za-z0-9]+", "", candidate).strip()
        return ""

    def _parse_duration_seconds(self, text: str) -> int | None:
        matched = False

        def _pick(pattern: str) -> int:
            nonlocal matched
            match = re.search(pattern, text)
            if match is None:
                return 0
            matched = True
            return int(match.group(1))

        days = _pick(r"(\d+)\s*天")
        hours = _pick(r"(\d+)\s*小时")
        minutes = _pick(r"(\d+)\s*(?:分钟|分)")
        seconds = _pick(r"(\d+)\s*秒")
        if not matched:
            return None
        return days * 86400 + hours * 3600 + minutes * 60 + seconds

    def _parse_trial_success_cooldown_seconds(self, text: str) -> int | None:
        match = re.search(r"下次试炼冷却[:：]\s*([^\n\r。]+)", text)
        if match is None:
            return None
        return self._parse_duration_seconds(match.group(1))

    def _artifact_names(self) -> list[str]:
        if self._configured_artifacts:
            return list(self._configured_artifacts)
        return list(self._discovered_artifacts)

    def _artifact_state(self, artifact: str) -> dict[str, datetime | None]:
        name = artifact.strip()
        state = self._states.get(name)
        if state is None:
            state = {key: None for key in self._DATETIME_KEYS}
            self._states[name] = state
        return state

    def _remember_discovered_artifact(self, artifact: str) -> None:
        name = artifact.strip()
        if not name:
            return
        if not self._configured_artifacts and name not in self._discovered_artifacts:
            self._discovered_artifacts.append(name)
        self._artifact_state(name)

    def _save_state(self) -> None:
        if self._state_store is None:
            return
        artifacts: dict[str, dict[str, str | None]] = {}
        for name, state in self._states.items():
            artifacts[name] = {key: serialize_datetime(state.get(key)) for key in self._DATETIME_KEYS}
        self._state_store.save_state(
            self._STATE_KEY,
            {
                "discovered_artifacts": self._discovered_artifacts,
                "artifacts": artifacts,
            },
        )

    def _set_pending_action(self, action: str, artifact: str) -> None:
        self._pending_action = action
        self._pending_artifact = artifact
        self._pending_action_started_at = self._now()

    def _clear_pending_action(self) -> None:
        self._pending_action = None
        self._pending_artifact = None
        self._pending_action_started_at = None

    def _expire_pending_action(self) -> bool:
        if self._pending_action is None:
            return False
        if self._pending_action_started_at is None:
            self._pending_action_started_at = self._now()
            return False
        age = (self._now() - self._pending_action_started_at).total_seconds()
        if age < self._PENDING_ACTION_TTL_SECONDS:
            return False
        self._logger.warning(
            "qiling_pending_expired identity=%s artifact=%s action=%s age_seconds=%.1f",
            self._identity_key,
            self._pending_artifact or "-",
            self._pending_action,
            age,
        )
        self._clear_pending_action()
        self._save_state()
        return True

    def _action_enabled(self, action: str) -> bool:
        if action == "touch":
            return self._touch_enabled
        if action == "nurture":
            return self._nurture_enabled
        if action == "trial":
            return self._trial_enabled
        return False

    def _any_action_enabled(self) -> bool:
        return self._touch_enabled or self._nurture_enabled or self._trial_enabled

    def _now(self) -> datetime:
        now = self._now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now

    def _split_names(self, raw: str) -> list[str]:
        return self._dedupe_names(part.strip() for part in re.split(r"[,，、\n\r]+", raw))

    def _dedupe_names(self, names) -> list[str]:  # type: ignore[no-untyped-def]
        result: list[str] = []
        for raw in names:
            name = str(raw or "").strip()
            if name and name not in result:
                result.append(name)
        return result
