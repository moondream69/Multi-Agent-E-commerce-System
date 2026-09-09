import { ConversationMeta } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

/** 会话列表(A2:当前用户,updated_at 倒序)。 */
export async function fetchConversations(): Promise<ConversationMeta[]> {
  const res = await apiFetch(`${BASE}/conversations`);
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { conversations: ConversationMeta[] };
  return body.conversations;
}

/** 删除会话(A2):有挂起审批批次时后端 409,错误信息透传。 */
export async function deleteConversation(sessionId: string): Promise<void> {
  const res = await apiFetch(
    `${BASE}/conversations/${encodeURIComponent(sessionId)}`,
    {
      method: 'DELETE',
    },
  );
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // 非 JSON 响应:保留 statusText
    }
    throw new Error(detail);
  }
}
