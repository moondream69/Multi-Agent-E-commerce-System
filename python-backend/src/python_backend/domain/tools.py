"""工具定义与动态暴露机制(宪章:节点级动态绑定——LLM 看不到未授权工具)。

ToolRegistry 按授权节点登记工具;图节点调用 visible_for() 获取可见清单,
清单外的工具对 LLM 不可见(约束前移到 LLM 看见工具之前)。
风险三层分类(免审/审批/禁做)在增量 3/4 挂接业务工具时启用。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """工具的标准定义(OpenAI function calling 形状)。"""

    name: str
    description: str
    parameters: dict  # JSON Schema:{"type": "object", "properties": {...}, "required": [...]}

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


class ToolRegistry:
    """工具注册表:工具按节点授权,visible_for 返回该节点的可见工具清单。"""

    def __init__(self) -> None:
        self._tools: list[tuple[ToolDefinition, set[str]]] = []

    def register(self, tool: ToolDefinition, *, authorized_nodes: set[str]) -> None:
        self._tools.append((tool, authorized_nodes))

    def visible_for(self, node: str) -> list[ToolDefinition]:
        return [tool for tool, nodes in self._tools if node in nodes]

    def all(self) -> list[ToolDefinition]:
        return [tool for tool, _ in self._tools]
