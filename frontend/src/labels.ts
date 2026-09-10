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
