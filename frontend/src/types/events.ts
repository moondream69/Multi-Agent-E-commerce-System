// 契约真源:契约测试 python-backend/tests/test_contract.py 对照本文件断言
export const AgentEventType = {
  REPORT_GENERATED: 'report.generated',
  PRODUCT_CREATED: 'product.created',
  PRODUCT_UPDATED: 'product.updated',
  ORDER_STATUS_CHANGED: 'order.status_changed',
  REPLY_GENERATED: 'reply.generated',
  ESCALATION_TRIGGERED: 'escalation.triggered',
  INVENTORY_ALERT: 'inventory.alert',
  CUSTOMER_NOTIFICATION: 'customer.notification',
  TASK_ASSIGNED: 'task.assigned',
  TASK_COMPLETED: 'task.completed',
  TASK_FAILED: 'task.failed',
  AGENT_STATUS_CHANGED: 'agent.status_changed',
  APPROVAL_REQUESTED: 'approval.requested',
  APPROVAL_DECIDED: 'approval.decided',
} as const;

export type AgentEventType =
  (typeof AgentEventType)[keyof typeof AgentEventType];

export type AgentStatus = 'idle' | 'busy' | 'error' | 'offline';

export interface AgentEvent {
  id: string;
  type: AgentEventType;
  source: string;
  timestamp: string;
  payload?: unknown;
  correlationId?: string;
}

export interface AgentStatusChangedPayload {
  agentId: string;
  status: AgentStatus;
  taskId?: string;
}

export interface AgentInfo {
  id: string;
  name: string;
  description: string;
  status: AgentStatus;
  tools: { name: string; description: string; parameters: unknown[] }[];
}

// chat:response 三种形状手动同步(契约测试覆盖)
export interface TaskCreatedResponse {
  type: 'task_created';
  taskId: string;
  taskType: string;
  text: string;
  timestamp: string;
}

export interface TaskResultResponse {
  type: 'task_result';
  taskId: string;
  agentId: string;
  status: string;
  output: Record<string, unknown>;
  steps?: unknown[];
  timestamp: string;
}

export interface TaskErrorResponse {
  type: 'task_error';
  taskId: string;
  error: string;
  timestamp: string;
}

export type ChatResponse =
  TaskCreatedResponse | TaskResultResponse | TaskErrorResponse;

// —— 买家前台(REST store 路由 + 订单事件)——

export type OrderStatus =
  | 'pending'
  | 'confirmed'
  | 'processing'
  | 'shipped'
  | 'delivered'
  | 'cancelled'
  | 'returned';

export interface Product {
  id: string;
  sku: string;
  title: string;
  description: string | null;
  price: number;
  category: string;
  currency: string;
  platform: string | null;
  status: string;
  createdAt: string;
  updatedAt: string;
}

export interface Order {
  id: string;
  productId: string;
  customerId: string | null;
  status: OrderStatus;
  totalAmount: number;
  currency: string;
  platform: string | null;
  metadata: unknown;
  createdAt: string;
  updatedAt: string;
  product: Product | null;
}

export interface OrderStatusChangedPayload {
  orderId: string;
  from?: string | null;
  to: string;
  productId?: string;
  totalAmount?: number;
}

// —— 业务事件 payload(工具 emit,经 agent:event 下发)——

export interface ReportGeneratedPayload {
  title?: string;
  report?: string;
}

export interface ProductCreatedPayload {
  product?: Product;
}

export interface ProductUpdatedPayload {
  productId?: string;
  status?: string;
}

export interface ReplyGeneratedPayload {
  scenario?: string;
  templateId?: string;
}

export interface EscalationTriggeredPayload {
  orderId?: string;
  reason?: string;
}

export interface InventoryAlertPayload {
  productName?: string;
  currentStock?: number;
  threshold?: number;
  message?: string;
}

export interface CustomerNotificationPayload {
  message?: string;
  agentId?: string;
  orderId?: string;
  kind?: string;
}

// 客服主动通知(chat:notification,独立于 chat:response 三形状)
export interface NotificationMessage {
  type: 'chat:notification';
  notificationId: string;
  message: string;
  agentId: string;
  kind: string;
  orderId?: string | null;
  timestamp: string;
}

// —— 会话(issue #5:多会话,conversations 表每 (customerId, sessionId) 一行)——

export interface ConversationMessage {
  role: string;
  content: string;
  timestamp: string;
  taskId?: string;
}

export interface ConversationMeta {
  sessionId: string;
  title: string;
  updatedAt: string;
  messageCount: number;
}

export interface SessionMessages {
  sessionId: string;
  messages: ConversationMessage[];
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

export interface TaskLifecyclePayload {
  threadId: string;
  status: 'created' | 'interrupted' | 'completed' | 'failed';
  error?: string | null;
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
