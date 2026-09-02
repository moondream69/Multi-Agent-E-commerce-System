// 与后端 src/common/interfaces/event.interface.ts / agent.interface.ts / task.interface.ts 手动同步
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

export type AgentEventType = typeof AgentEventType[keyof typeof AgentEventType];

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
