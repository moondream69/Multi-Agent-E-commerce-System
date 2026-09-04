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
}

// 客服主动通知(chat:notification,独立于 chat:response 三形状)
export interface NotificationMessage {
  type: 'chat:notification';
  notificationId: string;
  message: string;
  agentId: string;
  orderId?: string | null;
  timestamp: string;
}
