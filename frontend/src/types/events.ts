// 契约真源:契约测试 python-backend/tests/test_contract.py 对照本文件断言
export const AgentEventType = {
  REPORT_GENERATED: 'report.generated',
  PRODUCT_CREATED: 'product.created',
  PRODUCT_UPDATED: 'product.updated',
  ORDER_STATUS_CHANGED: 'order.status_changed',
  REPLY_GENERATED: 'reply.generated',
  ESCALATION_TRIGGERED: 'escalation.triggered',
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
