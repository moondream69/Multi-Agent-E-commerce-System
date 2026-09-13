import { useCallback, useEffect, useMemo, useState } from 'react';
import { AgentMarkdown } from './AgentMarkdown';
import {
  decideBatches,
  executeShadowBatch,
  fetchActionMetadata,
  fetchOpenApprovals,
} from '../services/approvals';
import { useApprovalEvents } from '../hooks/useApprovalEvents';
import {
  ApprovalActionSnapshot,
  ApprovalBatch,
  ApprovalBatchStatus,
  SlicePlanSlice,
  ThreadPlan,
} from '../types/events';
import {
  AGENT_LABELS,
  ORDER_STATUS_LABELS,
  PRODUCT_STATUS_LABELS,
} from '../labels';

// —— 参数/状态的台账标签(系统术语 → 中文台账口径)——
// 动作标签由 GET /api/actions 提供(spec #8 注册表单一化,不再硬编码)。

const PARAM_LABELS: Record<string, string> = {
  product_id: '商品',
  order_id: '订单',
  new_price: '新价格',
  to_status: '目标状态',
};

const STATUS_LABELS: Record<ApprovalBatchStatus, string> = {
  pending: '待批',
  approved: '已批准',
  rejected: '已拒绝',
  expired: '已过期',
  shadow: '影子',
  executed: '已执行',
};

function actionLabel(action: string, labels: Record<string, string>): string {
  return labels[action] ?? action;
}

function paramLabel(key: string): string {
  return PARAM_LABELS[key] ?? key;
}

function statusLabel(value: unknown): string {
  if (typeof value !== 'string') return String(value);
  return ORDER_STATUS_LABELS[value] ?? PRODUCT_STATUS_LABELS[value] ?? value;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

function displayValue(value: unknown): string {
  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value);
  }
  if (value === null || value === undefined) return '';
  return JSON.stringify(value);
}

// —— 批次卡:状态脊线 + 流水号 + 动作台账 ——

/** 批次卡左侧脊线四态(尾随批次状态与已作决定;显式静态映射,勿动态拼类名)。 */
const SPINE_CLASS: Record<'shadow' | 'approve' | 'reject' | 'idle', string> = {
  shadow: 'bg-st-approval',
  approve: 'bg-brand',
  reject: 'bg-st-failed',
  idle: 'bg-line-strong',
};

function ActionRow({
  item,
  labels,
}: {
  item: ApprovalActionSnapshot;
  labels: Record<string, string>;
}) {
  const params = Object.entries(item.params ?? {});
  const snapshot = item.snapshot ?? {};
  const target =
    item.action === 'product.update_price'
      ? `新价格 ${displayValue(item.params?.new_price)}`
      : item.action === 'order.transition'
        ? `→ ${statusLabel(item.params?.to_status)}`
        : item.action === 'product.publish'
          ? '→ 在售'
          : item.action === 'product.unpublish'
            ? '→ 已下架'
            : item.action === 'order.cancel'
              ? '→ 已取消'
              : item.action === 'product.delete'
                ? '→ 已删除'
                : '';
  const baseline =
    snapshot && 'status' in snapshot && snapshot.status
      ? statusLabel(snapshot.status)
      : null;
  const title = snapshot && 'title' in snapshot ? String(snapshot.title) : null;

  return (
    <div className="py-2.5 pr-3.5 pl-[22px]">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-medium">
          {title ?? actionLabel(item.action, labels)}
        </span>
        <span className="font-mono text-[11px] text-ink-3">
          {params
            .map(([key, value]) => `${paramLabel(key)} ${displayValue(value)}`)
            .join(' · ')}
        </span>
      </div>
      <div className="mt-0.5 text-xs text-ink-2">
        {baseline ? `现状 ${baseline}` : ''}
        {baseline && target ? ' ' : ''}
        {target}
        {baseline === null && !target && '快照不可用'}
      </div>
    </div>
  );
}

type Decision = 'approve' | 'reject';

function BatchCard({
  batch,
  decision,
  comment,
  busy,
  labels,
  onDecision,
  onComment,
  onExecute,
}: {
  batch: ApprovalBatch;
  decision: Decision | null;
  comment: string;
  busy: boolean;
  labels: Record<string, string>;
  onDecision: (d: Decision) => void;
  onComment: (c: string) => void;
  onExecute: () => void;
}) {
  const isShadow = batch.mode === 'shadow';
  const spine = isShadow
    ? SPINE_CLASS.shadow
    : decision === 'approve'
      ? SPINE_CLASS.approve
      : decision === 'reject'
        ? SPINE_CLASS.reject
        : SPINE_CLASS.idle;

  return (
    <div className="flex overflow-hidden rounded-card border border-line bg-surface">
      <div className={`w-1 shrink-0 ${spine}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 px-3.5 pt-2">
          <span className="font-mono text-[11px] text-ink-3">
            #{shortId(batch.batchId)}
          </span>
          <span
            className={`rounded-md px-2 py-0.5 text-[11px] font-semibold tracking-[0.02em] ${
              isShadow
                ? 'bg-st-approval-bg text-st-approval'
                : 'bg-brand-soft text-brand'
            }`}
          >
            {actionLabel(batch.actionType, labels)}
          </span>
          {isShadow && (
            <span className="text-[11px] text-st-approval">
              影子段 · 未执行
            </span>
          )}
          <span className="ml-auto text-[11px] text-ink-3">
            {STATUS_LABELS[batch.status]}
          </span>
        </div>
        {batch.actions.map((item, index) => (
          <ActionRow
            key={`${batch.batchId}-${index}`}
            item={item}
            labels={labels}
          />
        ))}
        <div className="mt-0.5 flex items-center gap-2 border-t border-line px-3.5 pt-2.5 pb-3">
          {isShadow ? (
            <button
              onClick={onExecute}
              disabled={busy}
              className="cursor-pointer rounded-lg border border-st-approval/40 bg-st-approval-bg px-4 py-1.5 text-[13px] font-medium text-st-approval transition-colors hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? '执行中…' : '补执行'}
            </button>
          ) : (
            <>
              <input
                value={comment}
                onChange={(event) => onComment(event.target.value)}
                placeholder="备注(可选)"
                className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-2.5 py-1.5 text-[13px] outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
              />
              <button
                onClick={() => onDecision('reject')}
                disabled={busy}
                className={`cursor-pointer rounded-lg border px-4 py-1.5 text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  decision === 'reject'
                    ? 'border-st-failed bg-st-failed-bg font-medium text-st-failed'
                    : 'border-st-failed/50 text-st-failed hover:bg-st-failed-bg'
                }`}
              >
                拒绝
              </button>
              <button
                onClick={() => onDecision('approve')}
                disabled={busy}
                className={`cursor-pointer rounded-lg border px-4 py-1.5 text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  decision === 'approve'
                    ? 'border-brand bg-brand font-medium text-brand-contrast'
                    : 'border-brand/40 bg-brand-soft text-brand hover:brightness-105'
                }`}
              >
                批准
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// —— 任务上下文 + 后续计划预览(ADR-0005;spec #34)——

/** 计划切片 chip 三态:已过(灰)/ 当前(审批色高亮)/ 后续。 */
function sliceChipClass(no: number, current: number): string {
  if (no === current)
    return 'border-st-approval/50 bg-st-approval-bg text-st-approval';
  if (no < current) return 'border-line bg-surface-2 text-ink-3';
  return 'border-line bg-surface text-ink-2';
}

function PlanPreview({
  plan,
  currentSlice,
}: {
  plan: ThreadPlan;
  currentSlice: number;
}) {
  return (
    <div className="mb-2.5 rounded-lg border border-line bg-surface-2 px-3 py-2">
      <div className="flex items-baseline gap-2">
        <span className="shrink-0 text-[11px] text-ink-3">原始需求</span>
        <span
          className="truncate text-xs text-ink"
          title={plan.request ?? undefined}
        >
          {plan.request ?? '(无请求文本)'}
        </span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <span className="shrink-0 text-[11px] text-ink-3">切片计划</span>
        {plan.plan.slices.map((slice: SlicePlanSlice) => (
          <span
            key={slice.no}
            title={
              slice.depends_on.length > 0
                ? `依赖段 ${slice.depends_on.join(', ')}`
                : slice.description
            }
            className={`rounded-md border px-1.5 py-0.5 text-[11px] ${sliceChipClass(slice.no, currentSlice)}`}
          >
            <span className="font-mono">{slice.no}</span>{' '}
            {AGENT_LABELS[slice.agent] ?? slice.agent}
            {slice.no === currentSlice && ' · 本批'}
          </span>
        ))}
      </div>
    </div>
  );
}

// —— 审批中心视图 ——

export function ApprovalCenter() {
  const [batches, setBatches] = useState<ApprovalBatch[]>([]);
  const [plans, setPlans] = useState<Record<string, ThreadPlan>>({});
  const [error, setError] = useState<string | null>(null);
  const [actionLabels, setActionLabels] = useState<Record<string, string>>({});
  const [decisions, setDecisions] = useState<
    Record<string, { decision: Decision; comment: string }>
  >({});
  const [busyThread, setBusyThread] = useState<string | null>(null);
  const [busyBatch, setBusyBatch] = useState<string | null>(null);

  // 动作标签来自 GET /api/actions(spec #8 注册表单一化);加载失败不阻断审批中心,
  // actionLabel 兜底显示原始动作标识。
  useEffect(() => {
    fetchActionMetadata()
      .then((rows) => {
        setActionLabels(
          Object.fromEntries(rows.map((row) => [row.action, row.label])),
        );
      })
      .catch(() => {
        /* 标签加载失败:降级原始标识,审批中心仍可用 */
      });
  }, []);

  const refresh = useCallback(() => {
    fetchOpenApprovals()
      .then((body) => {
        setBatches(body.approvals);
        setPlans(body.plans);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // WS 实时通道:审批/任务事件到达即刷新(初始加载由上方 effect 负责)
  const connected = useApprovalEvents(refresh);

  const byThread = useMemo(() => {
    const groups = new Map<string, ApprovalBatch[]>();
    for (const batch of batches) {
      const list = groups.get(batch.threadId) ?? [];
      list.push(batch);
      groups.set(batch.threadId, list);
    }
    return groups;
  }, [batches]);

  const pendingCount = batches.filter(
    (b) => b.mode === 'approval' && b.status === 'pending',
  ).length;
  const shadowCount = batches.filter((b) => b.mode === 'shadow').length;

  const setDecision = (batchId: string, decision: Decision) => {
    setDecisions((prev) => ({
      ...prev,
      [batchId]: { decision, comment: prev[batchId]?.comment ?? '' },
    }));
  };

  const submitThread = async (
    threadId: string,
    threadBatches: ApprovalBatch[],
  ) => {
    const approvalBatches = threadBatches.filter(
      (b) => b.mode === 'approval' && b.status === 'pending',
    );
    const body: Record<string, { decision: Decision; comment?: string }> = {};
    for (const batch of approvalBatches) {
      const decided = decisions[batch.batchId];
      if (!decided) return; // 全部决定后才能提交(后端同样拒绝部分提交)
      body[batch.batchId] = {
        decision: decided.decision,
        comment: decided.comment || undefined,
      };
    }
    setBusyThread(threadId);
    try {
      await decideBatches(threadId, body);
      refresh();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyThread(null);
    }
  };

  const executeShadow = async (threadId: string, batchId: string) => {
    setBusyBatch(batchId);
    try {
      await executeShadowBatch(threadId, batchId);
      refresh();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyBatch(null);
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 pt-5 pb-8">
      <div className="mx-auto mb-4 flex max-w-[760px] items-baseline gap-3">
        <h2 className="m-0 text-[17px] font-semibold">审批中心</h2>
        <span className="text-xs text-ink-2">待批 {pendingCount}</span>
        {shadowCount > 0 && (
          <span className="text-xs text-st-approval">影子 {shadowCount}</span>
        )}
        <span
          className={`ml-auto font-mono text-xs ${connected ? 'text-st-done' : 'text-ink-3'}`}
        >
          {connected ? '实时通道已连接' : '实时通道未连接'}
        </span>
      </div>

      {error && (
        <div className="mx-auto mb-3 max-w-[760px] rounded-lg border border-st-failed/40 bg-st-failed-bg px-3.5 py-2.5 text-[13px] text-st-failed">
          {error}
        </div>
      )}

      {batches.length === 0 && !error && (
        <div className="mx-auto mt-16 max-w-[760px] text-center text-sm text-ink-3">
          暂无待批事项。新的审批会实时出现在这里。
        </div>
      )}

      <div className="mx-auto flex max-w-[760px] flex-col gap-4">
        {[...byThread.entries()].map(([threadId, threadBatches]) => {
          const first = threadBatches[0];
          const plan = plans[threadId];
          const approvalBatches = threadBatches.filter(
            (b) => b.mode === 'approval' && b.status === 'pending',
          );
          const allDecided = approvalBatches.every((b) => decisions[b.batchId]);
          return (
            <section
              key={threadId}
              className="rounded-card border border-line bg-surface p-3.5 shadow-card"
            >
              <div className="mb-2.5 flex items-baseline gap-2">
                <span className="font-mono text-[11px] text-ink-3">
                  任务 {shortId(threadId)}
                </span>
                <span className="text-[13px] font-medium">
                  切片 {first.sliceNo}
                </span>
                <span className="text-xs text-ink-2">
                  {actionLabel(first.actionType, actionLabels)}
                </span>
              </div>
              {/* 任务上下文 + 后续计划预览(ADR-0005):无任务行/无计划时不渲染该区 */}
              {plan && <PlanPreview plan={plan} currentSlice={first.sliceNo} />}
              {typeof first.runOutput?.answer === 'string' &&
                first.runOutput.answer.trim() !== '' && (
                  <div className="mb-2.5 rounded-lg border border-line bg-bg px-2.5 py-2 text-xs text-ink-2">
                    <AgentMarkdown>{first.runOutput.answer}</AgentMarkdown>
                  </div>
                )}
              <div className="flex flex-col gap-2.5">
                {threadBatches.map((batch) => (
                  <BatchCard
                    key={batch.batchId}
                    batch={batch}
                    decision={decisions[batch.batchId]?.decision ?? null}
                    comment={decisions[batch.batchId]?.comment ?? ''}
                    busy={
                      busyBatch === batch.batchId || busyThread === threadId
                    }
                    labels={actionLabels}
                    onDecision={(d) => setDecision(batch.batchId, d)}
                    onComment={(c) =>
                      setDecisions((prev) => ({
                        ...prev,
                        [batch.batchId]: {
                          decision: prev[batch.batchId]?.decision ?? 'approve',
                          comment: c,
                        },
                      }))
                    }
                    onExecute={() =>
                      void executeShadow(threadId, batch.batchId)
                    }
                  />
                ))}
              </div>
              {approvalBatches.length > 0 && (
                <div className="mt-2.5 flex justify-end">
                  <button
                    onClick={() => void submitThread(threadId, threadBatches)}
                    disabled={!allDecided || busyThread === threadId}
                    className="cursor-pointer rounded-lg bg-brand px-5 py-2 text-[13px] font-medium text-brand-contrast transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {busyThread === threadId
                      ? '提交中…'
                      : allDecided
                        ? '提交本任务决定'
                        : `逐批决定后提交(${approvalBatches.filter((b) => decisions[b.batchId]).length}/${approvalBatches.length})`}
                  </button>
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
