"""订单管理 Agent(spec #7):ReAct 子图,商品/订单日常运营。

高危动作(上架/下架/改价/删除/流转/取消)调用后不会立即生效——它们会被登记进
审批批次,由人工批准后统一执行;草稿编辑与只读查询免审直行。
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from python_backend.agents.base import ToolCallingLlmClient, build_react_agent
from python_backend.agents.executor import Executor
from python_backend.agents.order_management.tools import ORDER_TOOLS
from python_backend.domain.tools import ToolRegistry

SYSTEM_PROMPT = """你是订单管理 Agent,服务于跨境电商卖家。职责:
- 商品:创建/编辑草稿(免审直行);上架、下架、改价、删除等对外状态变更需要人工审批——
  你调用这些工具后动作会登记待批,批准前不会生效,请如实告知用户「已提交审批」。
- 订单:查询列表与详情、检查库存(读真实库存数据)、检测异常;创建订单会扣减真实库存,
  属于审批动作(登记待批,批准后生效);订单状态流转与取消同样需要人工审批。
- 用户以 SKU 或标题指代商品、而你没有其商品 ID 时,先用 product_lookup 解析出 ID 再操作;
  标题命中多条候选时列出候选让用户确认,不得任选其一。
- 库存检查必须用 check_inventory 读库,不得自行猜测库存数字。

只输出事实性结论,数据不足时如实说明。"""


def build_order_agent(
    *, executor: Executor, llm: ToolCallingLlmClient, step_limit: int = 10
) -> tuple[CompiledStateGraph, ToolRegistry]:
    """构建订单子图,返回 (图, 工具注册表)。"""
    registry = ToolRegistry()
    for tool in ORDER_TOOLS:
        registry.register(tool, authorized_nodes={"agent"})
    graph = build_react_agent(
        name="order_management",
        system_prompt=SYSTEM_PROMPT,
        registry=registry,
        executor=executor,
        llm=llm,
        step_limit=step_limit,
    )
    return graph, registry
