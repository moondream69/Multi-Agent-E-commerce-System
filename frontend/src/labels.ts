// —— 领域标签(系统术语 → 中文台账口径):跨视图共用,勿在组件内重复定义 ——

export const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  confirmed: '已确认',
  processing: '处理中',
  shipped: '已发货',
  delivered: '已送达',
  returned: '已退货',
  cancelled: '已取消',
};

export const PRODUCT_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  active: '在售',
  inactive: '已下架',
};

/** 业务 Agent 中文名(驾驶舱切片/审批中心计划 chips 共用;键 = 后端 agent 标识)。 */
export const AGENT_LABELS: Record<string, string> = {
  product_research: '选品',
  order_management: '订单',
  customer_service: '客服',
};
