"""客服 Agent 工具清单(spec #7):verify 节点只暴露查证工具,draft 节点暴露起草工具。

查证优先硬约束(B12)由图级边约束实现:未查证(evidence 为 0)不可达 draft 节点。
查证工具 = faq_search / knowledge_search / order_lookup / product_lookup
(issue #38:商品查证计入证据集合;issue #50:统一检索一次查 FAQ + 市场情报)。
"""

from python_backend.domain.tools import ToolDefinition


def _object(**properties: dict) -> dict:
    """JSON Schema 对象:required 只含描述中不带「(可选)」的参数。"""
    required = [name for name, spec in properties.items() if "(可选)" not in spec.get("description", "")]
    return {"type": "object", "properties": properties, "required": required}


VERIFY_TOOLS = [
    ToolDefinition(
        name="faq_search",
        description="检索 FAQ 知识库(Milvus),获取与买家问题相关的标准解答",
        parameters=_object(query={"type": "string", "description": "买家问题的检索关键词"}),
    ),
    # issue #50 统一检索:一次查 FAQ + 市场情报两集合——只暴露给客服域(ADR-0007 边界);
    # 既有三工具原样不动(票面要求:不删、不改返回结构),本工具是客服侧的两集合入口
    ToolDefinition(
        name="knowledge_search",
        description="统一检索知识库:一次查询 FAQ 与市场情报两库,返回与问题相关的条目",
        parameters=_object(query={"type": "string", "description": "检索关键词(买家问题相关内容)"}),
    ),
    ToolDefinition(
        name="order_lookup",
        description="查询订单详情(只读):按订单 ID 查单笔,或按客户 ID 查其全部订单",
        parameters=_object(
            order_id={"type": "integer", "description": "订单 ID(order_id 与 customer_id 二选一)(可选)"},
            customer_id={"type": "integer", "description": "客户 ID(order_id 与 customer_id 二选一)(可选)"},
        ),
    ),
    # issue #38:同名工具按各 Agent 各自声明(order_management/tools.py 同款先例),
    # 动作经全局 REGISTRY 解析(product_lookup = risk auto 免审直行,#35 已注册)
    ToolDefinition(
        name="product_lookup",
        description=(
            "按 SKU 或标题定位商品(只读),返回候选列表(含 id/sku/title/price/status/stock)。"
            "买家消息涉及具体商品(价格/库存/在售状态)时用本工具查证"
        ),
        parameters=_object(
            sku={"type": "string", "description": "商品 SKU,精确匹配(sku 与 title 至少给一)(可选)"},
            title={"type": "string", "description": "商品标题关键词,模糊匹配(sku 与 title 至少给一)(可选)"},
        ),
    ),
]

DRAFT_TOOLS = [
    ToolDefinition(
        name="translate",
        description="将文本翻译到目标语言(如 en/fr/de/ja),保持语气自然",
        parameters=_object(
            text={"type": "string", "description": "待翻译文本"},
            target_locale={"type": "string", "description": "目标语言代码,如 en, es, fr, de, ja"},
        ),
    ),
    ToolDefinition(
        name="generate_draft",
        description="基于买家消息与查证证据起草客服回复(语言与买家消息一致,先引证据再作答)",
        parameters=_object(
            buyer_message={"type": "string", "description": "买家原始消息"},
            evidence={"type": "string", "description": "查证证据(FAQ/订单查询结果)"},
        ),
    ),
    ToolDefinition(
        name="manage_template",
        description="管理回复模板:get 按 场景+语言 查,create/update 需 scenario/locale/template",
        parameters=_object(
            action={"type": "string", "description": "get | create | update"},
            scenario={"type": "string", "description": "场景标识(可选)"},
            locale={"type": "string", "description": "语言代码(可选)"},
            template={"type": "string", "description": "模板文本,含变量占位如 {order_id}(可选)"},
        ),
    ),
    ToolDefinition(
        name="sentiment_analysis",
        description="判断买家消息情绪:positive / negative / neutral(可选工具)",
        parameters=_object(text={"type": "string", "description": "买家消息文本"}),
    ),
    ToolDefinition(
        name="escalate_ticket",
        description="创建升级工单(免审):升级不再是断头事件,工单落表并界面可见",
        parameters=_object(
            message={"type": "string", "description": "升级原因与上下文"},
            customer_id={"type": "integer", "description": "客户 ID(可选)"},
        ),
    ),
]
