"""选品分析 Agent 工具清单(spec #7):情报检索 → 评分 → 报告 → 草稿(A4 工具化)。"""

from python_backend.domain.tools import ToolDefinition


def _object(**properties: dict) -> dict:
    """JSON Schema 对象:required 只含描述中不带「(可选)」的参数。"""
    required = [name for name, spec in properties.items() if "(可选)" not in spec.get("description", "")]
    return {"type": "object", "properties": properties, "required": required}


PRODUCT_TOOLS = [
    ToolDefinition(
        name="trend_query",
        description="查询品类市场趋势情报(Milvus 市场情报库检索),返回相关情报条目",
        parameters=_object(query={"type": "string", "description": "趋势查询关键词,如「宠物饮水机」"}),
    ),
    ToolDefinition(
        name="competitor_analysis",
        description="检索竞品相关情报(价格、评分、市场表现),返回情报条目",
        parameters=_object(query={"type": "string", "description": "竞品分析查询关键词"}),
    ),
    ToolDefinition(
        name="scoring",
        description="对候选商品多维评分(市场潜力/竞争程度/利润率/运营难度),输出分数与等级",
        parameters=_object(
            product_title={"type": "string", "description": "候选商品标题"},
            features={"type": "string", "description": "候选商品关键特征描述(可选)"},
        ),
    ),
    ToolDefinition(
        name="generate_report",
        description="基于情报与评分生成结构化选品分析报告(含结论与风险)",
        parameters=_object(context={"type": "string", "description": "情报检索与评分结果汇总"}),
    ),
    ToolDefinition(
        name="draft_create",
        description="创建商品草稿(免审,不进审批):选品报告完成后将结论落为 draft",
        parameters=_object(
            sku={"type": "string", "description": "商品 SKU"},
            title={"type": "string", "description": "商品标题"},
            price={"type": "number", "description": "商品价格"},
            category={"type": "string", "description": "品类名称"},
            description={"type": "string", "description": "商品描述(可选)"},
        ),
    ),
]
