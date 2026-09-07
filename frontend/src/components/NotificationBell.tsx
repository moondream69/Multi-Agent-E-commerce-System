import React, { useEffect, useRef, useState } from 'react';
import { NotificationMessage } from '../types/events';
import { theme } from '../theme';

interface Props {
  items: NotificationMessage[];
  unread: number;
  onOpen: () => void;
  onClose: () => void;
}

// kind → 中文类型标签(通知信封契约字段)
const KIND_ZH: Record<string, string> = {
  order_status: '订单状态变更',
  inventory_alert: '库存告警',
};

/** Agent 卡片铃铛:红色数字徽标(0 隐藏、99+ 封顶)+ 下拉通知面板(点外部关闭)。 */
export function NotificationBell({ items, unread, onOpen, onClose }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [open]);

  const toggle = () => {
    if (open) {
      onClose();
      setOpen(false);
    } else {
      onOpen();
      setOpen(true);
    }
  };

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
            width: 300,
            maxHeight: 300,
            overflow: 'auto',
            background: theme.color.surface,
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.md,
            boxShadow: theme.shadow.card,
            zIndex: 10,
            padding: '8px 0',
          }}
        >
          {items.length === 0 ? (
            <p
              style={{
                padding: '8px 12px',
                margin: 0,
                fontSize: 12,
                color: theme.color.textMuted,
              }}
            >
              暂无通知
            </p>
          ) : (
            items.map((n) => (
              <div
                key={n.notificationId}
                style={{
                  padding: '6px 12px',
                  borderBottom: '1px solid #f1f5f9',
                  fontSize: 12,
                }}
              >
                <div style={{ color: theme.color.text, lineHeight: 1.5 }}>
                  {n.message}
                </div>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'baseline',
                    gap: 8,
                    marginTop: 2,
                  }}
                >
                  <span
                    style={{
                      color: theme.color.brand,
                      fontSize: 10,
                      fontFamily: theme.font.mono,
                    }}
                  >
                    {KIND_ZH[n.kind] ?? '通知'}
                  </span>
                  <span
                    style={{
                      color: theme.color.textMuted,
                      marginLeft: 'auto',
                      fontSize: 10,
                      fontFamily: theme.font.mono,
                    }}
                  >
                    {new Date(n.timestamp).toLocaleTimeString()}
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
