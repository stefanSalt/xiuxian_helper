from __future__ import annotations

import logging

from .contracts import MessageContext, Plugin, SendAction


class Dispatcher:
    def __init__(self, plugins: list[Plugin], logger: logging.Logger) -> None:
        self._logger = logger
        self._plugins = sorted(plugins, key=lambda p: getattr(p, "priority", 0), reverse=True)

    async def dispatch(self, ctx: MessageContext) -> list[SendAction]:
        actions: list[SendAction] = []
        for plugin in self._plugins:
            if not getattr(plugin, "enabled", True):
                continue
            try:
                result = await plugin.on_message(ctx)
            except Exception:
                self._logger.exception("plugin_error name=%s", getattr(plugin, "name", plugin))
                continue
            if result:
                actions.extend(result)
        return actions

