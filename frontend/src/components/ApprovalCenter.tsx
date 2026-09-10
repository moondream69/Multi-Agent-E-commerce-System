import { useCallback, useEffect, useMemo, useState } from 'react';
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
} from '../types/events';
import { theme } from '../theme';
import { ORDER_STATUS_LABELS } from '../labels';

// —— 参数/状态的台账标签(系统术语 → 中文台账口径)——
// 动作标签由 GET /api/actions 提供(spec #8 注册表单一化,不再硬编码)。

const PARAM_LABELS: Record<string, string> = {
  product_id: '商品',
  order_id: '订单',
  new_price: '新价格',
  to_status: '目标状态',
};

const PRODUCT_STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  active: '在售',
  inactive: '已下架',
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

// —— 批次卡:状态脊线 + 流水号 + 动作台账 ——

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
    <div style={{ padding: '10px 14px 10px 22px' }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
        <span
          style={{ fontSize: 13, color: theme.color.text, fontWeight: 500 }}
        >
          {title ?? actionLabel(item.action, labels)}
        </span>
        <span
          style={{
            fontSize: 11,
            fontFamily: theme.font.mono,
            color: theme.color.textMuted,
          }}
        >
          {params
            .map(([key, value]) => `${paramLabel(key)} ${displayValue(value)}`)
            .join(' · ')}
        </span>
      </div>
      <div
        style={{ marginTop: 3, fontSize: 12, color: theme.color.textSecondary }}
      >
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
    ? theme.color.warning
    : decision === 'approve'
      ? theme.color.brand
      : decision === 'reject'
        ? theme.color.danger
        : theme.color.border;

  return (
    <div
      style={{
        display: 'flex',
        background: theme.color.surface,
        border: `1px solid ${theme.color.border}`,
        borderRadius: theme.radius.md,
        boxShadow: theme.shadow.card,
        overflow: 'hidden',
      }}
    >
      <div style={{ width: 4, background: spine, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 14px 0',
          }}
        >
          <span
            style={{
              fontSize: 11,
              fontFamily: theme.font.mono,
              color: theme.color.textMuted,
            }}
          >
            #{shortId(batch.batchId)}
          </span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: '0.02em',
              padding: '2px 8px',
              borderRadius: theme.radius.sm,
              background: isShadow
                ? theme.color.warningBg
                : theme.color.brandSoft,
              color: isShadow ? theme.color.warning : theme.color.brand,
            }}
          >
            {actionLabel(batch.actionType, labels)}
          </span>
          {isShadow && (
            <span style={{ fontSize: 11, color: theme.color.warning }}>
              影子段 · 未执行
            </span>
          )}
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 11,
              color: theme.color.textMuted,
            }}
          >
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
        <div
          style={{
            display: 'flex',
            gap: 8,
            alignItems: 'center',
            padding: '0 14px 12px',
            borderTop: `1px solid ${theme.color.border}`,
            paddingTop: 10,
            marginTop: 2,
          }}
        >
          {isShadow ? (
            <button
              onClick={onExecute}
              disabled={busy}
              style={{
                padding: '6px 16px',
                border: 'none',
                borderRadius: theme.radius.sm,
                background: theme.color.warning,
                color: '#fff',
                cursor: busy ? 'not-allowed' : 'pointer',
                fontSize: 13,
                opacity: busy ? 0.6 : 1,
              }}
            >
              {busy ? '执行中…' : '补执行'}
            </button>
          ) : (
            <>
              <input
                value={comment}
                onChange={(event) => onComment(event.target.value)}
                placeholder="备注(可选)"
                style={{
                  flex: 1,
                  padding: '6px 10px',
                  border: `1px solid ${theme.color.border}`,
                  borderRadius: theme.radius.sm,
                  fontSize: 13,
                  background: theme.color.bg,
                  color: theme.color.text,
                }}
              />
              <button
                onClick={() => onDecision('reject')}
                disabled={busy}
                style={{
                  padding: '6px 16px',
                  border: `1px solid ${theme.color.danger}`,
                  borderRadius: theme.radius.sm,
                  background:
                    decision === 'reject' ? theme.color.danger : 'transparent',
                  color: decision === 'reject' ? '#fff' : theme.color.danger,
                  cursor: busy ? 'not-allowed' : 'pointer',
                  fontSize: 13,
                  opacity: busy ? 0.6 : 1,
                }}
              >
                拒绝
              </button>
              <button
                onClick={() => onDecision('approve')}
                disabled={busy}
                style={{
                  padding: '6px 16px',
                  border: 'none',
                  borderRadius: theme.radius.sm,
                  background:
                    decision === 'approve'
                      ? theme.color.brand
                      : theme.color.brandSoft,
                  color: decision === 'approve' ? '#fff' : theme.color.brand,
                  cursor: busy ? 'not-allowed' : 'pointer',
                  fontSize: 13,
                  opacity: busy ? 0.6 : 1,
                }}
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

// —— 审批中心视图 ——

export function ApprovalCenter() {
  const [batches, setBatches] = useState<ApprovalBatch[]>([]);
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
      .then((rows) => {
        setBatches(rows);
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
    <div
      style={{
        flex: 1,
        minHeight: 0,
        overflow: 'auto',
        background: theme.color.bg,
        padding: '20px 24px 32px',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 12,
          maxWidth: 760,
          margin: '0 auto 16px',
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 17,
            fontWeight: 600,
            color: theme.color.text,
          }}
        >
          审批中心
        </h2>
        <span style={{ fontSize: 12, color: theme.color.textSecondary }}>
          待批 {pendingCount}
        </span>
        {shadowCount > 0 && (
          <span style={{ fontSize: 12, color: theme.color.warning }}>
            影子 {shadowCount}
          </span>
        )}
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 12,
            fontFamily: theme.font.mono,
            color: connected ? theme.color.success : theme.color.textMuted,
          }}
        >
          {connected ? '实时通道已连接' : '实时通道未连接'}
        </span>
      </div>

      {error && (
        <div
          style={{
            maxWidth: 760,
            margin: '0 auto 12px',
            padding: '10px 14px',
            border: `1px solid ${theme.color.danger}`,
            borderRadius: theme.radius.sm,
            background: theme.color.dangerBg,
            color: theme.color.danger,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {batches.length === 0 && !error && (
        <div
          style={{
            maxWidth: 760,
            margin: '64px auto',
            textAlign: 'center',
            color: theme.color.textMuted,
            fontSize: 14,
          }}
        >
          暂无待批事项。新的审批会实时出现在这里。
        </div>
      )}

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          maxWidth: 760,
          margin: '0 auto',
        }}
      >
        {[...byThread.entries()].map(([threadId, threadBatches]) => {
          const first = threadBatches[0];
          const approvalBatches = threadBatches.filter(
            (b) => b.mode === 'approval' && b.status === 'pending',
          );
          const allDecided = approvalBatches.every((b) => decisions[b.batchId]);
          return (
            <section
              key={threadId}
              style={{
                background: theme.color.surface,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.md,
                padding: '12px 14px 14px',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: 'baseline',
                  marginBottom: 10,
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    fontFamily: theme.font.mono,
                    color: theme.color.textMuted,
                  }}
                >
                  任务 {shortId(threadId)}
                </span>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: theme.color.text,
                  }}
                >
                  切片 {first.sliceNo}
                </span>
                <span
                  style={{ fontSize: 12, color: theme.color.textSecondary }}
                >
                  {first.actionType}
                </span>
              </div>
              {typeof first.runOutput?.answer === 'string' &&
                first.runOutput.answer.trim() !== '' && (
                  <div
                    style={{
                      fontSize: 12,
                      color: theme.color.textSecondary,
                      margin: '0 0 10px',
                      background: theme.color.bg,
                      border: `1px solid ${theme.color.border}`,
                      borderRadius: theme.radius.sm,
                      padding: '8px 10px',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {first.runOutput.answer}
                  </div>
                )}
              <div
                style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
              >
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
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'flex-end',
                    marginTop: 10,
                  }}
                >
                  <button
                    onClick={() => void submitThread(threadId, threadBatches)}
                    disabled={!allDecided || busyThread === threadId}
                    style={{
                      padding: '8px 20px',
                      border: 'none',
                      borderRadius: theme.radius.sm,
                      background: theme.color.brand,
                      color: '#fff',
                      cursor:
                        allDecided && busyThread !== threadId
                          ? 'pointer'
                          : 'not-allowed',
                      fontSize: 13,
                      opacity: allDecided ? 1 : 0.5,
                    }}
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
