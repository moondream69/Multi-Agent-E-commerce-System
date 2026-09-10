import { useCallback, useEffect, useState } from 'react';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { closeTicket, fetchTickets } from '../services/tickets';
import { TicketItem } from '../types/events';
import { theme } from '../theme';

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
    <div
      style={{
        borderTop: `1px solid ${theme.color.border}`,
        padding: '10px 16px 6px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        maxHeight: 200,
        minHeight: 0,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span
          style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
        >
          升级工单
        </span>
        <span style={{ fontSize: 11, color: theme.color.textMuted }}>
          未结 {openCount} / 共 {tickets.length}
        </span>
      </div>
      {error && (
        <div style={{ fontSize: 12, color: theme.color.danger }}>{error}</div>
      )}
      <div
        style={{
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
          minHeight: 0,
        }}
      >
        {tickets.length === 0 && (
          <div style={{ fontSize: 12, color: theme.color.textMuted }}>
            暂无工单(客服升级人工后出现在这里)
          </div>
        )}
        {tickets.map((ticket) => (
          <div
            key={ticket.ticketId}
            style={{
              display: 'flex',
              alignItems: 'baseline',
              gap: 6,
              fontSize: 12,
              padding: '4px 8px',
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              background:
                ticket.status === 'open' ? theme.color.surface : theme.color.bg,
            }}
          >
            <span
              style={{
                whiteSpace: 'nowrap',
                color:
                  ticket.status === 'open'
                    ? theme.color.warning
                    : theme.color.textMuted,
              }}
            >
              {ticket.status === 'open' ? '未结' : '已结'}
            </span>
            <span
              title={ticket.message}
              style={{
                flex: 1,
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                color: theme.color.text,
              }}
            >
              {ticket.message}
            </span>
            {ticket.customerName && (
              <span
                style={{ color: theme.color.textMuted, whiteSpace: 'nowrap' }}
              >
                {ticket.customerName}
              </span>
            )}
            <span
              style={{ color: theme.color.textMuted, whiteSpace: 'nowrap' }}
            >
              {shortTime(ticket.createdAt)}
            </span>
            {ticket.status === 'open' && (
              <button
                onClick={() => void close(ticket.ticketId)}
                title="结单(记录处理时间)"
                style={{
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: 12,
                  color: theme.color.brand,
                  whiteSpace: 'nowrap',
                  padding: 0,
                }}
              >
                结单
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
