import { TicketItem } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

/** 工单列表(A11 收口:客服升级实体化的界面可见面)。 */
export async function fetchTickets(): Promise<TicketItem[]> {
  const res = await apiFetch(`${BASE}/tickets`);
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { tickets: TicketItem[] };
  return body.tickets;
}

/** 工单结单(open→closed);已结重复请求幂等返回当前状态。 */
export async function closeTicket(ticketId: number): Promise<TicketItem> {
  const res = await apiFetch(`${BASE}/tickets/${ticketId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'closed' }),
  });
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { ticket: TicketItem };
  return body.ticket;
}
