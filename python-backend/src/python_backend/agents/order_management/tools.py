"""订单管理 Agent 工具清单(spec #7)。

免审(直行):只读查询、异常检测、draft 内部编辑;
审批(收集打包):一切对外状态变更——上架/下架/改价/删除/订单流转/取消。
order.create 留增量 5(与 CSV/REST 数据入口一起,spec #7 Out of Scope),不暴露。
"""

from python_backend.domain.tools import ToolDefinition


def _object(**properties: dict) -> dict:
    """JSON Schema 对象:required 只含描述中不带「(可选)」的参数。"""
    required = [name for name, spec in properties.items() if "(可选)" not in spec.get("description", "")]
    return {"type": "object", "properties": properties, "required": required}


_ORDER_STATUSES = "pending|confirmed|processing|shipped|delivered|cancelled|returned"

ORDER_TOOLS = [
    # —— 免审:只读 ——
    ToolDefinition(
        name="list_orders",
        description=f"查询订单列表(只读)。可选按状态过滤;状态枚举仅限: {_ORDER_STATUSES}",
        parameters=_object(
            status={"type": "string", "description": f"订单状态筛选({_ORDER_STATUSES}),不传返回全部(可选)"},
        ),
    ),
    ToolDefinition(
        name="check_inventory",
        description="检查商品真实库存(读库,非自报):低于安全线给出五档告警文案",
        parameters=_object(
            product_id={"type": "integer", "description": "商品 ID"},
            threshold={"type": "integer", "description": "安全库存阈值"},
        ),
    ),
    ToolDefinition(
        name="detect_anomalies",
        description="检测订单描述文本中的异常关键词(退货/退款/投诉/破损/延迟/丢失)",
        parameters=_object(description={"type": "string", "description": "订单描述文本"}),
    ),
    ToolDefinition(
        name="list_approvals",
        description="查询审批批次列表(只读)。可选按状态过滤: pending|approved|rejected|expired|shadow|executed",
        parameters=_object(
            status={"type": "string", "description": "审批状态筛选,不传返回全部(可选)"},
        ),
    ),
    # —— 免审:draft 内部编辑 ——
    ToolDefinition(
        name="draft_create",
        description="创建商品草稿(draft,免审)",
        parameters=_object(
            sku={"type": "string", "description": "商品 SKU(唯一)"},
            title={"type": "string", "description": "商品标题"},
            price={"type": "number", "description": "商品价格"},
            category={"type": "string", "description": "品类名称"},
            description={"type": "string", "description": "商品描述(可选)"},
        ),
    ),
    ToolDefinition(
        name="draft_edit",
        description="编辑商品草稿(免审,仅 draft 状态可编辑):传哪些字段改哪些",
        parameters=_object(
            product_id={"type": "integer", "description": "商品 ID"},
            title={"type": "string", "description": "新标题(可选)"},
            price={"type": "number", "description": "新价格(可选)"},
            category={"type": "string", "description": "新品类(可选)"},
            description={"type": "string", "description": "新描述(可选)"},
        ),
    ),
    # —— 审批:对外状态变更(调用后登记待人工批准,不会立即生效)——
    ToolDefinition(
        name="product_publish",
        description="上架商品(审批动作:调用后登记待人工批准,批准前不生效)",
        parameters=_object(product_id={"type": "integer", "description": "商品 ID"}),
    ),
    ToolDefinition(
        name="product_unpublish",
        description="下架商品(审批动作:调用后登记待人工批准,批准前不生效)",
        parameters=_object(product_id={"type": "integer", "description": "商品 ID"}),
    ),
    ToolDefinition(
        name="product_update_price",
        description="修改商品价格(审批动作:调用后登记待人工批准,批准前不生效)",
        parameters=_object(
            product_id={"type": "integer", "description": "商品 ID"},
            new_price={"type": "number", "description": "新价格"},
        ),
    ),
    ToolDefinition(
        name="product_delete",
        description="删除商品(审批动作:调用后登记待人工批准;有订单关联时会被拒绝)",
        parameters=_object(product_id={"type": "integer", "description": "商品 ID"}),
    ),
    ToolDefinition(
        name="order_transition",
        description=(
            "订单状态流转(审批动作:调用后登记待人工批准,批准前不生效)。"
            "合法流转: pending→confirmed/cancelled, confirmed→processing/cancelled, "
            "processing→shipped, shipped→delivered, delivered→returned"
        ),
        parameters=_object(
            order_id={"type": "integer", "description": "订单 ID"},
            to_status={"type": "string", "description": f"目标状态({_ORDER_STATUSES})"},
        ),
    ),
    ToolDefinition(
        name="order_cancel",
        description="取消订单(审批动作:调用后登记待人工批准;仅 pending/confirmed 可取消)",
        parameters=_object(order_id={"type": "integer", "description": "订单 ID"}),
    ),
]
