import { useCallback, useEffect, useState } from 'react';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { closeTicket, fetchTickets } from '../services/tickets';
import { TicketItem } from '../types/events';

/** 工单时间落款(月-日 时:分;缺值留空)。 */
function shortTime(value: string | null): string {
  if (!value) return '';
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

/** 工单列表(A11 收口):客服升级实体化的界面可见面,可人工结单。 */
export function TicketList() {
  const [tickets, setTickets] = useState<TicketItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchTickets()
      .then((rows) => {
        setTickets(rows);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);
  // 工单由客服任务内的 escalate 产生:任务终态事件到达即刷新(挂载时也已拉取)
  useTaskTerminalEvents(refresh);

  const close = async (ticketId: number) => {
    setError(null);
    try {
      await closeTicket(ticketId);
      refresh();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const openCount = tickets.filter((ticket) => ticket.status === 'open').length;

  return (
    <div className="flex max-h-[200px] min-h-0 flex-col gap-1.5 border-t border-line px-4 pt-2.5 pb-1.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-semibold">升级工单</span>
        <span className="text-[11px] text-ink-3">
          未结 {openCount} / 共 {tickets.length}
        </span>
      </div>
      {error && <div className="text-xs text-st-failed">{error}</div>}
      <div className="flex min-h-0 flex-col gap-1 overflow-y-auto">
        {tickets.length === 0 && (
          <div className="text-xs text-ink-3">
            暂无工单(客服升级人工后出现在这里)
          </div>
        )}
        {tickets.map((ticket) => {
          const open = ticket.status === 'open';
          return (
            <div
              key={ticket.ticketId}
              className={`flex items-baseline gap-1.5 rounded-lg border border-line px-2 py-1 text-xs ${
                open ? 'bg-surface' : 'bg-surface-2'
              }`}
            >
              <span
                className={`shrink-0 ${open ? 'text-st-approval' : 'text-ink-3'}`}
              >
                {open ? '未结' : '已结'}
              </span>
              <span className="min-w-0 flex-1 truncate" title={ticket.message}>
                {ticket.message}
              </span>
              {ticket.customerName && (
                <span className="shrink-0 text-ink-3">
                  {ticket.customerName}
                </span>
              )}
              <span className="tnum shrink-0 text-ink-3">
                {shortTime(ticket.createdAt)}
              </span>
              {open && (
                <button
                  onClick={() => void close(ticket.ticketId)}
                  title="结单(记录处理时间)"
                  className="shrink-0 cursor-pointer border-none bg-transparent p-0 text-xs text-brand hover:underline"
                >
                  结单
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
