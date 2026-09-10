// 契约真源:契约测试 python-backend/tests/test_contract.py 对照本文件断言
// 历史清理(spec #9):旧系统聊天响应三形状、Agent 事件系列与未消费 payload 接口已删除。

// —— WS 事件名(实况墙/审批通道/通知铃铛的订阅源)——

export const EventType = {
  TASK_CREATED: 'task.created',
  TASK_INTERRUPTED: 'task.interrupted',
  TASK_COMPLETED: 'task.completed',
  TASK_FAILED: 'task.failed',
  APPROVAL_REQUESTED: 'approval.requested',
  APPROVAL_DECIDED: 'approval.decided',
  NOTIFICATION_CREATED: 'notification.created',
} as const;

export type EventType = (typeof EventType)[keyof typeof EventType];

export interface TaskLifecyclePayload {
  threadId: string;
  status: 'created' | 'interrupted' | 'completed' | 'failed';
  error?: string | null;
}

// —— 切片式审批批次(spec #7:切片内同类高危动作打包真实参数快照,批内同进同退)——

export type ApprovalBatchStatus =
  'pending' | 'approved' | 'rejected' | 'expired' | 'shadow' | 'executed';

export interface ApprovalActionSnapshot {
  action: string;
  params: Record<string, unknown>;
  snapshot: Record<string, unknown> | null;
}

export interface ApprovalBatch {
  batchId: string;
  threadId: string;
  sliceNo: number;
  actionType: string;
  actions: ApprovalActionSnapshot[];
  status: ApprovalBatchStatus;
  mode: 'approval' | 'shadow';
  comment: string | null;
  result: Record<string, unknown> | null;
  runOutput: Record<string, unknown> | null;
}

// 一次 interrupt 携带切片全部批次(spec #7),逐批决定一次提交
export interface ApprovalRequestedPayload {
  threadId: string;
  sliceNo: number;
  agent: string;
  batches: ApprovalBatchRequested[];
}

export interface ApprovalBatchRequested {
  batchId: string;
  actionType: string;
  actions: ApprovalActionSnapshot[];
}

export interface ApprovalDecidedPayload {
  threadId: string;
  batchId: string;
  decision: 'approve' | 'reject';
  comment?: string | null;
}

// —— 通知铃铛(spec #9 A8/A9/A14:notification.created)——

// kind 已知集合:order_status / inventory_alert / fx_missing;未知 kind 前端归入兜底组
export interface NotificationMessage {
  notificationId: string;
  message: string;
  kind: string;
  orderId: number | null;
  timestamp: string;
}

// —— 会话(A2 多会话:新建/切换/删除、标题截断 20 字、空白会话惰性落库)——

export interface ConversationMeta {
  sessionId: string;
  title: string;
  updatedAt: string;
  messageCount: number;
}

// —— 动作元数据(spec #8:GET /api/actions,前端标签不再硬编码)——

export interface ActionMeta {
  action: string;
  risk: 'auto' | 'approval';
  label: string;
}

// —— 认证(spec #8 A1:POST /api/auth/login)——

export interface LoginResponse {
  token: string;
  username: string;
}

// —— 驾驶舱(spec #8:任务列表/详情端点)——

export interface TaskListItem {
  threadId: string;
  sessionId: string;
  type: string;
  status: string;
  title: string;
  createdAt: string | null;
}

export interface SlicePlanSlice {
  no: number;
  agent: string;
  description: string;
  depends_on: number[];
  approval_points: string[];
}

export interface TaskDetail {
  threadId: string;
  sessionId: string;
  type: string;
  status: string;
  request: string | null;
  plan: { slices: SlicePlanSlice[] } | null;
  results: Record<string, unknown> | null;
  result: { summary?: string; error?: string } | null;
  batches: ApprovalBatch[];
  createdAt: string | null;
}

// —— 起草工作台(spec #8 B11/B19:POST /api/drafting)——

export interface DraftingEvidence {
  faq_hits: Array<{
    id: string;
    score: number;
    payload: Record<string, unknown>;
  }>;
  order: Record<string, unknown> | null;
  order_id: number | null;
}

export interface DraftingResponse {
  draft: string;
  evidence: DraftingEvidence;
}

// —— CSV 导入(spec #8 B8:POST /api/import/products|orders)——

export interface ImportReport {
  created: number;
  skipped: number;
  errors: Array<{ row: number; reason: string }>;
}

// —— 商品只读列表(spec #9:GET /api/products,模拟流量发现商品)——

export interface ProductListItem {
  id: number;
  sku: string;
  title: string;
  price: string;
  currency: string;
  category: string;
  status: string;
  stock: number;
  alertThreshold: number;
}

// —— 工单(A11 收口:GET /api/tickets + PATCH 结单)——

export interface TicketItem {
  ticketId: number;
  message: string;
  status: 'open' | 'closed';
  customerName: string | null;
  createdAt: string | null;
  resolvedAt: string | null;
}

// —— 经营快照(spec #11:GET /api/reports/summary,零 LLM 纯 SQL 聚合)——

export interface ReportSummary {
  orders: { byStatus: Record<string, number>; total: number };
  revenue: {
    windowDays: number;
    baseCurrency: string;
    amount: string;
    unconverted: number;
  };
  lowStock: Array<{
    productId: number;
    sku: string;
    title: string;
    stock: number;
    alertThreshold: number;
  }>;
  tickets: { open: number };
}
