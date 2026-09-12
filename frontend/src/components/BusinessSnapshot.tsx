import { useCallback, useEffect, useState } from 'react';
import { useSocket } from '../hooks/useSocket';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { ORDER_STATUS_LABELS } from '../labels';
import { fetchReportSummary } from '../services/reports';
import { EventType, ReportSummary } from '../types/events';

// 模块级常量:useSocket 依赖引用稳定,避免每次渲染重连
const NOTIFICATION_EVENTS = [EventType.NOTIFICATION_CREATED];

/** 经营快照(spec #11 / 2026-09 重绘):订单分布 / 成交额 / 低库存 / 未结工单;零 LLM 纯 SQL。 */
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
  // 任务终态事件后自动刷新(订单/库存随审批 apply 变化),无轮询
  useTaskTerminalEvents(refresh);
  // REST 下单/库存告警在提交后广播 notification.created(不产生任务终态事件)——
  // 快照须同时订阅通知,否则纯下单流量下订单/成交额不更新(走查缺陷)
  useSocket(NOTIFICATION_EVENTS, refresh);

  const statusEntries = summary ? Object.entries(summary.orders.byStatus) : [];

  return (
    <div className="flex flex-wrap items-stretch gap-x-6 gap-y-3 rounded-card border border-line bg-surface px-5 py-3.5 shadow-card">
      {!summary && !error && (
        <span className="self-center text-xs text-ink-3">加载中…</span>
      )}
      {error && (
        <span className="self-center text-xs text-st-failed">{error}</span>
      )}
      {summary && (
        <>
          <div className="flex min-w-[120px] flex-col gap-1">
            <span className="text-[11px] text-ink-3">订单</span>
            <span className="tnum text-xl leading-none font-semibold">
              {summary.orders.total}
            </span>
            {statusEntries.length > 0 && (
              <span className="flex flex-wrap gap-1">
                {statusEntries.map(([status, count]) => (
                  <span
                    key={status}
                    className="tnum rounded-full bg-surface-2 px-1.5 py-0.5 text-[10px] text-ink-2"
                  >
                    {ORDER_STATUS_LABELS[status] ?? status} {count}
                  </span>
                ))}
              </span>
            )}
          </div>

          <div className="flex min-w-[150px] flex-col gap-1 border-line pl-6 sm:border-l">
            <span className="text-[11px] text-ink-3">
              近 {summary.revenue.windowDays} 日成交额
            </span>
            <span className="flex items-baseline gap-1.5">
              <span className="tnum text-xl leading-none font-semibold">
                {summary.revenue.amount}
              </span>
              <span className="text-[11px] text-ink-3">
                {summary.revenue.baseCurrency}
              </span>
              {summary.revenue.unconverted > 0 && (
                <span className="tnum rounded-full bg-st-approval-bg px-1.5 py-0.5 text-[10px] text-st-approval">
                  待核 {summary.revenue.unconverted}
                </span>
              )}
            </span>
          </div>

          <div
            className="flex min-w-[180px] flex-col gap-1 border-line pl-6 sm:border-l"
            title={summary.lowStock
              .map((item) => `${item.title}(库存 ${item.stock})`)
              .join('、')}
          >
            <span className="text-[11px] text-ink-3">低库存</span>
            <span
              className={`tnum text-xl leading-none font-semibold ${
                summary.lowStock.length > 0 ? 'text-st-approval' : ''
              }`}
            >
              {summary.lowStock.length}
            </span>
            {summary.lowStock.length > 0 && (
              <span className="max-w-[320px] truncate text-[11px] text-ink-2">
                {summary.lowStock
                  .slice(0, 3)
                  .map((item) => `${item.title}(库存 ${item.stock})`)
                  .join('、')}
                {summary.lowStock.length > 3 ? ' …' : ''}
              </span>
            )}
          </div>

          <div className="flex min-w-[90px] flex-col gap-1 border-line pl-6 sm:border-l">
            <span className="text-[11px] text-ink-3">未结工单</span>
            <span className="tnum text-xl leading-none font-semibold">
              {summary.tickets.open}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
