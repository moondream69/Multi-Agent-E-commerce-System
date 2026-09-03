"""工具接口(镜像 src/common/interfaces/tool.interface.ts)。"""

from __future__ import annotations

from typing import Any, Protocol

from python_backend.domain.tasks import ToolDefinition


class ToolProtocol(Protocol):
    definition: ToolDefinition

    async def execute(self, params: dict[str, Any]) -> Any: ...
