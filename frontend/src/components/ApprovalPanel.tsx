import React, { useCallback, useEffect, useState } from 'react';
import {
  decideApproval,
  executeShadowApproval,
  fetchApprovals,
} from '../services/api';
import { ApprovalRequest } from '../types/events';
import { theme } from '../theme';

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  pending: { text: '待审批', color: theme.color.warning },
  shadow: { text: '影子建议', color: theme.color.brand },
  approved: { text: '已通过', color: theme.color.success },
  rejected: { text: '已拒绝', color: theme.color.danger },
  expired: { text: '已超时', color: theme.color.textMuted },
  executed: { text: '已执行', color: theme.color.success },
};

const TOOL_LABEL: Record<string, string> = {
  product_crud: '商品管理',
  order_workflow: '订单工作流',
};

/** 工具入参快照 → 人类可读文本(确定性格式化,不烧 LLM)。 */
function formatParams(params: Record<string, unknown>): string {
  const lines: string[] = [];
  const order = (key: string, label: string) => {
    const v = params[key];
    if (v !== undefined && v !== null && v !== '') {
      const text =
        typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'
          ? String(v)
          : JSON.stringify(v);
      lines.push(`${label}: ${text}`);
    }
  };
  order('orderId', '订单号');
  order('sku', 'SKU');
  order('productId', '商品');
  order('status', '目标状态');
  order('to', '流转至');
  order('action', '动作');
  order('title', '标题');
  order('price', '价格');
  return lines.length ? lines.join(' · ') : JSON.stringify(params);
}

export default function ApprovalPanel() {
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [rejecting, setRejecting] = useState<Record<string, string>>({});
  const [error, setError] = useState('');

  const refresh = useCallback(() => {
    fetchApprovals()
      .then(setRequests)
      .catch((err) => setError(err instanceof Error ? err.message : '加载失败'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const pending = requests.filter((r) => r.status === 'pending');
  const shadows = requests.filter((r) => r.status === 'shadow');
  const history = requests.filter(
    (r) => r.status !== 'pending' && r.status !== 'shadow',
  );

  const handleDecide = async (id: string, approve: boolean) => {
    const comment = !approve ? rejecting[id] : undefined;
    try {
      await decideApproval(id, approve, comment);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    }
  };

  const handleExecute = async (id: string) => {
    try {
      await executeShadowApproval(id);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : '执行失败');
    }
  };

  if (loading)
    return (
      <div style={{ padding: 24, color: theme.color.textSecondary }}>
        加载中...
      </div>
    );

  const card = (r: ApprovalRequest, actions?: React.ReactNode) => (
    <div
      key={r.id}
      style={{
        background: theme.color.surface,
        border: `1px solid ${theme.color.border}`,
        borderRadius: theme.radius.sm,
        padding: '12px 16px',
        marginBottom: 10,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
        }}
      >
        <span
          style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
        >
          {TOOL_LABEL[r.toolName] ?? r.toolName}
        </span>
        <span
          style={{
            fontSize: 12,
            fontFamily: theme.font.mono,
            color: theme.color.textSecondary,
          }}
        >
          {formatParams(r.params)}
        </span>
        <span
          style={{
            fontSize: 12,
            padding: '2px 8px',
            borderRadius: theme.radius.full,
            background: theme.color.bg,
            color: STATUS_LABEL[r.status].color,
          }}
        >
          {STATUS_LABEL[r.status].text}
        </span>
      </div>
      <div
        style={{
          marginTop: 6,
          fontSize: 12,
          color: theme.color.textMuted,
          fontFamily: theme.font.mono,
        }}
      >
        {r.requestedBy ?? '系统'} · {new Date(r.createdAt).toLocaleString()}
        {r.decidedBy ? ` · 决定人: ${r.decidedBy}` : ''}
      </div>
      {r.comment && (
        <div style={{ marginTop: 6, fontSize: 12, color: theme.color.danger }}>
          拒绝原因: {r.comment}
        </div>
      )}
      {r.result && (
        <div
          style={{
            marginTop: 6,
            fontSize: 12,
            color: theme.color.textSecondary,
          }}
        >
          结果: {JSON.stringify(r.result).slice(0, 120)}
        </div>
      )}
      {actions}
    </div>
  );

  return (
    <div
      style={{
        flex: 1,
        padding: 24,
        overflow: 'auto',
        background: theme.color.bg,
      }}
    >
      {error && (
        <div
          style={{ marginBottom: 12, color: theme.color.danger, fontSize: 13 }}
        >
          {error}
        </div>
      )}
      <button
        onClick={refresh}
        style={{
          marginBottom: 12,
          padding: '6px 14px',
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.sm,
          background: theme.color.surface,
          color: theme.color.textSecondary,
          fontSize: 13,
          cursor: 'pointer',
        }}
      >
        刷新
      </button>

      <h2
        style={{ margin: '12px 0 10px', fontSize: 15, color: theme.color.text }}
      >
        待审批 ({pending.length})
      </h2>
      {pending.length === 0 && (
        <div
          style={{
            color: theme.color.textMuted,
            fontSize: 13,
            marginBottom: 12,
          }}
        >
          暂无待审批操作
        </div>
      )}
      {pending.map((r) =>
        card(
          r,
          <div
            style={{
              marginTop: 10,
              display: 'flex',
              gap: 8,
              alignItems: 'center',
            }}
          >
            <button
              onClick={() => {
                void handleDecide(r.id, true);
              }}
              style={{
                padding: '6px 16px',
                border: 'none',
                borderRadius: theme.radius.sm,
                background: theme.color.success,
                color: '#fff',
                fontSize: 13,
                cursor: 'pointer',
              }}
            >
              通过
            </button>
            <input
              value={rejecting[r.id] ?? ''}
              onChange={(e) =>
                setRejecting((prev) => ({ ...prev, [r.id]: e.target.value }))
              }
              placeholder="拒绝原因(可选)"
              style={{
                flex: 1,
                padding: '6px 10px',
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.sm,
                fontSize: 13,
                outline: 'none',
              }}
            />
            <button
              onClick={() => {
                void handleDecide(r.id, false);
              }}
              style={{
                padding: '6px 16px',
                border: 'none',
                borderRadius: theme.radius.sm,
                background: theme.color.danger,
                color: '#fff',
                fontSize: 13,
                cursor: 'pointer',
              }}
            >
              拒绝
            </button>
          </div>,
        ),
      )}

      <h2
        style={{ margin: '16px 0 10px', fontSize: 15, color: theme.color.text }}
      >
        影子建议 ({shadows.length})
      </h2>
      {shadows.length === 0 && (
        <div
          style={{
            color: theme.color.textMuted,
            fontSize: 13,
            marginBottom: 12,
          }}
        >
          影子模式关闭时无建议记录
        </div>
      )}
      {shadows.map((r) =>
        card(
          r,
          <div style={{ marginTop: 10 }}>
            <button
              onClick={() => {
                void handleExecute(r.id);
              }}
              style={{
                padding: '6px 16px',
                border: 'none',
                borderRadius: theme.radius.sm,
                background: theme.color.brand,
                color: '#fff',
                fontSize: 13,
                cursor: 'pointer',
              }}
            >
              补执行
            </button>
            <span
              style={{
                marginLeft: 10,
                fontSize: 12,
                color: theme.color.textMuted,
              }}
            >
              忽略则保持不动
            </span>
          </div>,
        ),
      )}

      <h2
        style={{ margin: '16px 0 10px', fontSize: 15, color: theme.color.text }}
      >
        历史 ({history.length})
      </h2>
      {history.length === 0 && (
        <div style={{ color: theme.color.textMuted, fontSize: 13 }}>
          暂无历史记录
        </div>
      )}
      {history.map((r) => card(r))}
    </div>
  );
}
