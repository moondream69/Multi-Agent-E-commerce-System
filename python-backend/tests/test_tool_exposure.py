"""动态工具暴露机制测试(seam S9/B6):节点级绑定,未授权工具不进可见列表。"""

from python_backend.domain.tools import ToolDefinition, ToolRegistry


def tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=f"{name} 工具", parameters={"type": "object", "properties": {}})


class TestToolRegistry:
    def test_visible_for_returns_only_authorized_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(tool("read_inventory"), authorized_nodes={"order_management", "manager"})
        registry.register(tool("publish_product"), authorized_nodes={"human_console"})
        visible = [t.name for t in registry.visible_for("order_management")]
        assert visible == ["read_inventory"]  # publish_product 对 order_management 不可见

    def test_visible_for_unauthorized_node_is_empty(self) -> None:
        registry = ToolRegistry()
        registry.register(tool("read_inventory"), authorized_nodes={"order_management"})
        assert registry.visible_for("product_research") == []

    def test_empty_registry_returns_empty(self) -> None:
        assert ToolRegistry().visible_for("anything") == []

    def test_registration_order_is_stable(self) -> None:
        registry = ToolRegistry()
        registry.register(tool("a"), authorized_nodes={"n"})
        registry.register(tool("b"), authorized_nodes={"n"})
        registry.register(tool("c"), authorized_nodes={"n"})
        assert [t.name for t in registry.visible_for("n")] == ["a", "b", "c"]


def test_tool_definition_to_openai_format() -> None:
    definition = tool("read_inventory")
    openai_tool = definition.to_openai()
    assert openai_tool["type"] == "function"
    assert openai_tool["function"]["name"] == "read_inventory"
    assert openai_tool["function"]["description"] == "read_inventory 工具"
