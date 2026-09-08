import { ApprovalBatch } from '../types/events';

const BASE = '/api';

/** 全量未决批次(pending + shadow,审批中心数据源)。 */
export async function fetchOpenApprovals(): Promise<ApprovalBatch[]> {
  const res = await fetch(`${BASE}/approvals`);
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { approvals: ApprovalBatch[] };
  return body.approvals;
}

/** 对某线程的全部挂起批次一次提交决定(缺一不可,后端 422 拒绝部分提交)。 */
export async function decideBatches(
  threadId: string,
  decisions: Record<
    string,
    { decision: 'approve' | 'reject'; comment?: string }
  >,
): Promise<void> {
  const res = await fetch(`${BASE}/threads/${threadId}/resume`, {
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
  const res = await fetch(
    `${BASE}/threads/${threadId}/shadow-batches/${batchId}/execute`,
    { method: 'POST' },
  );
  if (!res.ok) throw new Error(await res.text());
}
