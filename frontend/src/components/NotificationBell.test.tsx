import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NotificationBell } from './NotificationBell';
import {
  fetchNotifications,
  markNotificationsRead,
} from '../services/notifications';
import { NotificationFeed, NotificationMessage } from '../types/events';

// WS 事件回调捕获:mock useSocket 记录 onEvent,测试内直接触发
const socket = vi.hoisted(() => ({
  handler: null as
    ((event: string, payload: Record<string, unknown>) => void) | null,
}));

vi.mock('../hooks/useSocket', () => ({
  useSocket: (
    _events: string[],
    onEvent: (event: string, payload: Record<string, unknown>) => void,
  ) => {
    socket.handler = onEvent;
    return true;
  },
}));

vi.mock('../services/notifications', () => ({
  fetchNotifications: vi.fn(),
  markNotificationsRead: vi.fn(),
}));

const fetchMock = vi.mocked(fetchNotifications);
const markReadMock = vi.mocked(markNotificationsRead);

function notification(
  notificationId: string,
  kind = 'order_status',
): NotificationMessage {
  return {
    notificationId,
    message: `消息 ${notificationId}`,
    kind,
    orderId: 1,
    timestamp: '2026-09-11T00:00:00+00:00',
  };
}

function feed(overrides: Partial<NotificationFeed> = {}): NotificationFeed {
  return { notifications: [], unread: 0, ...overrides };
}

function emitSocket(event: string, payload: unknown) {
  act(() => {
    socket.handler?.(event, payload as Record<string, unknown>);
  });
}

/** 铃铛按钮内的徽标查询(排除面板分组头部的条数计数)。 */
function badge(text: string) {
  return within(screen.getByTitle('通知')).queryByText(text);
}

describe('NotificationBell(增量 8-T2:服务端真源)', () => {
  beforeEach(() => {
    socket.handler = null;
    fetchMock.mockReset();
    markReadMock.mockReset();
    fetchMock.mockResolvedValue(feed());
    markReadMock.mockResolvedValue(undefined);
    localStorage.clear();
  });

  it('挂载即拉取服务端真源:徽标显示未读、面板按 kind 归组', async () => {
    fetchMock.mockResolvedValue(
      feed({
        notifications: [
          notification('n-1'),
          notification('n-2', 'inventory_alert'),
        ],
        unread: 3,
      }),
    );

    render(<NotificationBell />);

    expect(await screen.findByText('3')).toBeTruthy();
    fireEvent.click(screen.getByTitle('通知'));
    expect(screen.getByText('订单状态')).toBeTruthy();
    expect(screen.getByText('库存告警')).toBeTruthy();
    expect(screen.getByText('消息 n-1')).toBeTruthy();
    expect(screen.getByText('消息 n-2')).toBeTruthy();
  });

  it('WS 入账按 notificationId 去重:重复投递不重复展示、未读不重复累计', async () => {
    render(<NotificationBell />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    emitSocket('notification.created', notification('dup-1'));
    emitSocket('notification.created', notification('dup-1'));

    expect(badge('1')).toBeTruthy();
    fireEvent.click(screen.getByTitle('通知'));
    expect(screen.getAllByText('消息 dup-1')).toHaveLength(1);
  });

  it('打开面板即标记已读(POST):徽标清零', async () => {
    fetchMock.mockResolvedValue(
      feed({ notifications: [notification('n-1')], unread: 2 }),
    );
    render(<NotificationBell />);
    expect(await screen.findByText('2')).toBeTruthy();

    fireEvent.click(screen.getByTitle('通知'));

    expect(markReadMock).toHaveBeenCalledTimes(1);
    expect(badge('2')).toBeNull();
  });

  it('面板打开期间新到通知:入账可见且立即标记已读,未读不累计', async () => {
    render(<NotificationBell />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fireEvent.click(screen.getByTitle('通知'));
    markReadMock.mockClear();

    emitSocket('notification.created', notification('live-1'));

    expect(screen.getByText('消息 live-1')).toBeTruthy();
    expect(markReadMock).toHaveBeenCalledTimes(1);
    expect(badge('1')).toBeNull();
  });

  it('拉取失败保持空态:无徽标、面板可打开(不阻塞其余 UI)', async () => {
    fetchMock.mockRejectedValue(new Error('offline'));
    render(<NotificationBell />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    expect(badge('1')).toBeNull();
    fireEvent.click(screen.getByTitle('通知'));
    expect(screen.getByText(/暂无通知/)).toBeTruthy();
  });

  it('localStorage 铃铛键不再读写:以服务端数据为准,入账不回写旧键', async () => {
    const legacy = JSON.stringify({ unread: 99, lists: {} });
    localStorage.setItem('notification-bells-v1', legacy);
    fetchMock.mockResolvedValue(
      feed({ notifications: [notification('n-1')], unread: 1 }),
    );

    render(<NotificationBell />);
    expect(await screen.findByText('1')).toBeTruthy(); // 服务端未读,不是旧键的 99
    emitSocket('notification.created', notification('n-2'));

    expect(localStorage.getItem('notification-bells-v1')).toBe(legacy);
  });

  it('收到 notification.read poke:重拉真源使状态与拉取结果一致,且不触发写请求', async () => {
    fetchMock.mockResolvedValue(
      feed({ notifications: [notification('n-1')], unread: 2 }),
    );
    render(<NotificationBell />);
    expect(await screen.findByText('2')).toBeTruthy();

    fetchMock.mockResolvedValue(
      feed({ notifications: [notification('n-9')], unread: 0 }),
    );
    emitSocket('notification.read', {});

    await waitFor(() => expect(badge('2')).toBeNull()); // 未读随重拉归零(不本地自算)
    expect(markReadMock).not.toHaveBeenCalled(); // poke 无回环:不触发任何写请求
    fireEvent.click(screen.getByTitle('通知'));
    expect(screen.getByText('消息 n-9')).toBeTruthy(); // 列表即重拉快照
  });
});
