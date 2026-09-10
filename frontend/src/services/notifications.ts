import { NotificationFeed } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

/** 通知真源拉取(增量 8-T2):当前用户通知历史(每组最近 50 条)+ 全量未读计数。 */
export async function fetchNotifications(): Promise<NotificationFeed> {
  const res = await apiFetch(`${BASE}/notifications`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as NotificationFeed;
}

/** 标记已读(增量 8-T2):幂等清零当前用户未读(服务端按用户已读)。 */
export async function markNotificationsRead(): Promise<void> {
  const res = await apiFetch(`${BASE}/notifications/read`, { method: 'POST' });
  if (!res.ok) throw new Error(await res.text());
}
