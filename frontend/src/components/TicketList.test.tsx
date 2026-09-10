import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TicketList } from './TicketList';
import { closeTicket, fetchTickets } from '../services/tickets';
import { TicketItem } from '../types/events';

vi.mock('../hooks/useSocket', () => ({ useSocket: () => true }));
vi.mock('../services/tickets', () => ({
  fetchTickets: vi.fn(),
  closeTicket: vi.fn(),
}));

const fetchTicketsMock = vi.mocked(fetchTickets);
const closeTicketMock = vi.mocked(closeTicket);

const OPEN_TICKET: TicketItem = {
  ticketId: 1,
  message: '物流太慢了,我要投诉!',
  status: 'open',
  customerName: '张伟',
  createdAt: '2026-09-10T09:00:00Z',
  resolvedAt: null,
};

const CLOSED_TICKET: TicketItem = {
  ticketId: 2,
  message: '已处理的工单',
  status: 'closed',
  customerName: null,
  createdAt: '2026-09-09T09:00:00Z',
  resolvedAt: '2026-09-09T10:00:00Z',
};

describe('TicketList(A11 收口)', () => {
  beforeEach(() => {
    fetchTicketsMock.mockReset();
    closeTicketMock.mockReset();
  });

  it('渲染工单:未结计数 / 买家名 / 创建时间 / 仅未结可结单;空态文案', async () => {
    fetchTicketsMock.mockResolvedValue([OPEN_TICKET, CLOSED_TICKET]);
    render(<TicketList />);
    expect(await screen.findByText('物流太慢了,我要投诉!')).toBeTruthy();
    expect(screen.getByText('未结 1 / 共 2')).toBeTruthy();
    expect(screen.getByText('张伟')).toBeTruthy();
    expect(screen.getAllByText(/\d{2}\/\d{2} \d{2}:\d{2}/)).toHaveLength(2); // 创建时间落款(两张工单)
    expect(screen.getAllByText('结单')).toHaveLength(1);
  });

  it('结单:调用 closeTicket 并刷新列表', async () => {
    fetchTicketsMock.mockResolvedValue([OPEN_TICKET]);
    closeTicketMock.mockResolvedValue({
      ...OPEN_TICKET,
      status: 'closed',
      resolvedAt: '2026-09-10T10:00:00Z',
    });
    render(<TicketList />);
    fireEvent.click(await screen.findByText('结单'));
    await waitFor(() => expect(closeTicketMock).toHaveBeenCalledWith(1));
    await waitFor(() => expect(fetchTicketsMock).toHaveBeenCalledTimes(2));
  });

  it('无工单:空态提示', async () => {
    fetchTicketsMock.mockResolvedValue([]);
    render(<TicketList />);
    expect(await screen.findByText(/暂无工单/)).toBeTruthy();
  });
});
