import { useCallback, useEffect, useState } from 'react';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { ORDER_STATUS_LABELS } from '../labels';
import { fetchReportSummary } from '../services/reports';
import { ReportSummary } from '../types/events';
import { theme } from '../theme';

/** 经营快照(spec #11):订单分布 / 近 7 日成交额 / 低库存 / 未结工单;零 LLM 纯 SQL 聚合。 */
export function BusinessSnapshot() {
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchReportSummary()
      .then((row) => {
        setSummary(row);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);
  // 任务终态事件后自动刷新(订单/库存随审批 apply 与下单变化),无轮询
  useTaskTerminalEvents(refresh);

  const statusEntries = summary ? Object.entries(summary.orders.byStatus) : [];

  return (
    <div
      style={{
        borderBottom: `1px solid ${theme.color.border}`,
        padding: '9px 20px',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '4px 22px',
        fontSize: 12,
        color: theme.color.textSecondary,
        background: theme.color.surface,
      }}
    >
      <span style={{ fontWeight: 600, color: theme.color.text }}>经营快照</span>
      {!summary && !error && (
        <span style={{ color: theme.color.textMuted }}>加载中…</span>
      )}
      {error && <span style={{ color: theme.color.danger }}>{error}</span>}
      {summary && (
        <>
          <span>
            订单 {summary.orders.total}
            {statusEntries.length > 0 &&
              `(${statusEntries
                .map(
                  ([status, count]) =>
                    `${ORDER_STATUS_LABELS[status] ?? status} ${count}`,
                )
                .join(' / ')})`}
          </span>
          <span>
            近 {summary.revenue.windowDays} 日成交额 {summary.revenue.amount}{' '}
            {summary.revenue.baseCurrency}
            {summary.revenue.unconverted > 0 && (
              <span style={{ color: theme.color.warning }}>
                (待核 {summary.revenue.unconverted})
              </span>
            )}
          </span>
          <span
            title={summary.lowStock
              .map((item) => `${item.title}(库存 ${item.stock})`)
              .join('、')}
          >
            低库存 {summary.lowStock.length}
            {summary.lowStock.length > 0 &&
              `:${summary.lowStock
                .slice(0, 3)
                .map((item) => `${item.title}(库存 ${item.stock})`)
                .join('、')}${summary.lowStock.length > 3 ? ' …' : ''}`}
          </span>
          <span>未结工单 {summary.tickets.open}</span>
        </>
      )}
    </div>
  );
}
