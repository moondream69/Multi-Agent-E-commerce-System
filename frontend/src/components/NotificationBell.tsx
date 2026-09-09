import { useCallback, useEffect, useRef, useState } from 'react';
import { kindLabel, useNotificationBells } from '../hooks/useNotificationBells';
import { useSocket } from '../hooks/useSocket';
import { EventType, NotificationMessage } from '../types/events';
import { theme } from '../theme';

// —— 通知铃铛(spec #9 A8/A9/A14):WS notification.created → 按 kind 归组 + 未读持久化 ——

const BELL_EVENTS = [EventType.NOTIFICATION_CREATED]; // 模块级常量:useSocket 依赖引用稳定

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

/** 顶栏全局铃铛:红色数字徽标(0 隐藏、99+ 封顶)+ 下拉面板(点外部关闭)。 */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { unread, lists, ingest, openPanel, closePanel } =
    useNotificationBells();

  const onEvent = useCallback(
    (_event: string, payload: Record<string, unknown>) => {
      ingest(payload as unknown as NotificationMessage);
    },
    [ingest],
  );
  useSocket(BELL_EVENTS, onEvent);

  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        closePanel();
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [open, closePanel]);

  const toggle = () => {
    if (open) {
      closePanel();
      setOpen(false);
    } else {
      openPanel();
      setOpen(true);
    }
  };

  const groups = Object.entries(lists).filter(([, items]) => items.length > 0);

  return (
    <div ref={rootRef} style={{ position: 'relative' }}>
      <button
        onClick={toggle}
        title="通知"
        style={{
          position: 'relative',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          fontSize: 16,
          padding: '2px 4px',
          lineHeight: 1,
        }}
      >
        🔔
        {unread > 0 && (
          <span
            style={{
              position: 'absolute',
              top: -8,
              right: -8,
              minWidth: 16,
              height: 16,
              borderRadius: theme.radius.full,
              background: theme.color.danger,
              color: '#fff',
              fontSize: 10,
              lineHeight: '16px',
              textAlign: 'center',
              padding: '0 4px',
            }}
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            top: 28,
            right: 0,
            width: 320,
            maxHeight: 420,
            overflowY: 'auto',
            background: theme.color.surface,
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.md,
            boxShadow: theme.shadow.card,
            padding: '8px 0',
            zIndex: 20,
          }}
        >
          {groups.length === 0 && (
            <div
              style={{
                padding: '16px 14px',
                fontSize: 12,
                color: theme.color.textMuted,
                textAlign: 'center',
              }}
            >
              暂无通知。订单变更与库存告警会出现在这里。
            </div>
          )}
          {groups.map(([kind, items]) => (
            <div key={kind} style={{ marginBottom: 4 }}>
              <div
                style={{
                  padding: '6px 14px',
                  fontSize: 11,
                  fontWeight: 600,
                  color: theme.color.textSecondary,
                  background: theme.color.bg,
                }}
              >
                {kindLabel(kind)}
                <span style={{ marginLeft: 6, color: theme.color.textMuted }}>
                  {items.length}
                </span>
              </div>
              {items.map((item) => (
                <div
                  key={item.notificationId}
                  style={{
                    padding: '8px 14px',
                    borderBottom: `1px solid ${theme.color.border}`,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 3,
                  }}
                >
                  <span style={{ fontSize: 12, color: theme.color.text }}>
                    {item.message}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      fontFamily: theme.font.mono,
                      color: theme.color.textMuted,
                    }}
                  >
                    {item.orderId != null && `订单 #${item.orderId} · `}
                    {formatTime(item.timestamp)}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
