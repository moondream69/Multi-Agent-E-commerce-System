import { useEffect, useState } from 'react';
import { io, Socket } from 'socket.io-client';
import { theme } from '../theme';

// —— 事件流实况墙(A13):WS 五事件,语义配色 + 中文标签,与切片时间线分工共存 ——

interface WallEvent {
  id: string;
  event: string;
  payload: Record<string, unknown>;
  at: string;
}

const EVENT_LABELS: Record<string, string> = {
  'task.created': '任务已创建',
  'task.interrupted': '任务挂起',
  'task.completed': '任务完成',
  'task.failed': '任务失败',
  'approval.requested': '审批请求',
  'approval.decided': '审批已决定',
};

function eventColor(event: string): string {
  switch (event) {
    case 'task.completed':
    case 'approval.decided':
      return theme.color.success;
    case 'task.interrupted':
    case 'approval.requested':
      return theme.color.warning;
    case 'task.failed':
      return theme.color.danger;
    default:
      return theme.color.textSecondary;
  }
}

const EVENT_NAMES = Object.keys(EVENT_LABELS);
const MAX_EVENTS = 50;

function shortId(id: unknown): string {
  return typeof id === 'string' || typeof id === 'number'
    ? String(id).slice(0, 8)
    : '';
}

export function EventWall() {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<WallEvent[]>([]);

  useEffect(() => {
    const socket: Socket = io({ path: '/socket.io' });
    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));
    const push = (event: string) => (payload: Record<string, unknown>) => {
      setEvents((prev) =>
        [
          ...prev,
          {
            id: `${Date.now()}-${Math.random()}`,
            event,
            payload,
            at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
          },
        ].slice(-MAX_EVENTS),
      );
    };
    for (const name of EVENT_NAMES) {
      socket.on(name, push(name));
    }
    return () => {
      socket.disconnect();
    };
  }, []);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        borderLeft: `1px solid ${theme.color.border}`,
        background: theme.color.bg,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 8,
          padding: '10px 14px',
          borderBottom: `1px solid ${theme.color.border}`,
        }}
      >
        <span
          style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
        >
          事件流实况墙
        </span>
        <span
          style={{
            fontSize: 11,
            fontFamily: theme.font.mono,
            color: connected ? theme.color.success : theme.color.textMuted,
          }}
        >
          {connected ? '已连接' : '未连接'}
        </span>
      </div>
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '8px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}
      >
        {events.length === 0 && (
          <div
            style={{
              fontSize: 12,
              color: theme.color.textMuted,
              textAlign: 'center',
              marginTop: 40,
            }}
          >
            暂无事件。任务与审批活动会实时出现在这里。
          </div>
        )}
        {events.map((item) => (
          <div
            key={item.id}
            style={{
              background: theme.color.surface,
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              padding: '6px 10px',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: theme.radius.full,
                background: eventColor(item.event),
                flexShrink: 0,
              }}
            />
            <span
              style={{
                fontSize: 12,
                fontWeight: 500,
                color: eventColor(item.event),
                whiteSpace: 'nowrap',
              }}
            >
              {EVENT_LABELS[item.event] ?? item.event}
            </span>
            <span
              style={{
                fontSize: 11,
                fontFamily: theme.font.mono,
                color: theme.color.textMuted,
              }}
            >
              {shortId(item.payload.threadId)}
            </span>
            <span
              style={{
                marginLeft: 'auto',
                fontSize: 11,
                fontFamily: theme.font.mono,
                color: theme.color.textMuted,
              }}
            >
              {item.at}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
