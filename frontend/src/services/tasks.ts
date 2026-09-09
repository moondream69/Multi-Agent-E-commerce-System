import { TaskDetail, TaskListItem } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

/** 发起任务(B16:session_id 可选,默认 default)。 */
export async function createTask(
  request: string,
  sessionId?: string,
): Promise<{ threadId: string; status: string; summary?: string }> {
  const res = await apiFetch(`${BASE}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request, session_id: sessionId }),
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as {
    threadId: string;
    status: string;
    summary?: string;
  };
}

/** 任务列表(驾驶舱数据源);sessionId 给定时只取该会话的任务(A2 历史隔离)。 */
export async function fetchTasks(sessionId?: string): Promise<TaskListItem[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const res = await apiFetch(`${BASE}/tasks${query}`);
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { tasks: TaskListItem[] };
  return body.tasks;
}

/** 任务详情(切片时间线数据源)。 */
export async function fetchTaskDetail(threadId: string): Promise<TaskDetail> {
  const res = await apiFetch(`${BASE}/tasks/${threadId}`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as TaskDetail;
}
