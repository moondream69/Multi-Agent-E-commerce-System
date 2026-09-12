import { ActionMeta, ApprovalListResponse } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

/** 动作元数据(action/risk/中文标签):审批中心标签渲染数据源(spec #8)。 */
export async function fetchActionMetadata(): Promise<ActionMeta[]> {
  const res = await apiFetch(`${BASE}/actions`);
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { actions: ActionMeta[] };
  return body.actions;
}

/** 全量未决批次(pending + shadow)+ 线程级计划旁挂(审批中心数据源,spec #34)。 */
export async function fetchOpenApprovals(): Promise<ApprovalListResponse> {
  const res = await apiFetch(`${BASE}/approvals`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ApprovalListResponse;
}

/** 对某线程的全部挂起批次一次提交决定(缺一不可,后端 422 拒绝部分提交)。 */
export async function decideBatches(
  threadId: string,
  decisions: Record<
    string,
    { decision: 'approve' | 'reject'; comment?: string }
  >,
): Promise<void> {
  const res = await apiFetch(`${BASE}/threads/${threadId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(decisions),
  });
  if (!res.ok) throw new Error(await res.text());
}

/** 影子批次一键补执行(A15,仅演练环境出现)。 */
export async function executeShadowBatch(
  threadId: string,
  batchId: string,
): Promise<void> {
  const res = await apiFetch(
    `${BASE}/threads/${threadId}/shadow-batches/${batchId}/execute`,
    { method: 'POST' },
  );
  if (!res.ok) throw new Error(await res.text());
}
