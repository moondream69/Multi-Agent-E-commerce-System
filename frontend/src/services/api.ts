import {
  AgentInfo,
  ApprovalRequest,
  ConversationMeta,
  SessionMessages,
} from '../types/events';
import { clearToken, getToken } from './auth';

const BASE = '/api';

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

/** 令牌过期统一处理:REST 401 与 WS connect_error 都走这里。 */
export function handleAuthError() {
  clearToken();
  onUnauthorized?.();
}

async function authFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401) handleAuthError();
  return res;
}

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await authFetch('/dashboard/agents');
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as AgentInfo[];
}

/** 会话元数据列表(按更新时间倒序,最近 50 个)。 */
export async function fetchConversations(): Promise<ConversationMeta[]> {
  const res = await authFetch('/conversations');
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ConversationMeta[];
}

/** 单个会话的消息历史。 */
export async function fetchSessionMessages(
  sessionId: string,
): Promise<SessionMessages> {
  const res = await authFetch(`/conversations/${sessionId}/messages`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as SessionMessages;
}

/** 硬删除会话。 */
export async function deleteSession(sessionId: string): Promise<void> {
  const res = await authFetch(`/conversations/${sessionId}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(await res.text());
}

export async function fetchApprovals(
  status?: string,
): Promise<ApprovalRequest[]> {
  const query = status ? `?status=${status}` : '';
  const res = await authFetch(`/approvals${query}`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ApprovalRequest[];
}

export async function decideApproval(
  approvalId: string,
  approve: boolean,
  comment?: string,
): Promise<ApprovalRequest> {
  const res = await authFetch(`/approvals/${approvalId}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approve, comment }),
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ApprovalRequest;
}

export async function executeShadowApproval(
  approvalId: string,
): Promise<ApprovalRequest> {
  const res = await authFetch(`/approvals/${approvalId}/execute`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ApprovalRequest;
}
