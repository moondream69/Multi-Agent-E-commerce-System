import { useCallback, useEffect, useRef, useState } from 'react';
import { kindLabel, useNotificationBells } from '../hooks/useNotificationBells';
import { useSocket } from '../hooks/useSocket';
import { EventType, NotificationMessage } from '../types/events';

// —— 通知铃铛(spec #9 A8/A9/A14;增量 8-T2/#17 服务端真源 + 多端已读同步)——

const BELL_EVENTS = [
  EventType.NOTIFICATION_CREATED,
  EventType.NOTIFICATION_READ,
]; // 模块级常量:useSocket 依赖引用稳定

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

/** 顶栏全局铃铛:红色数字徽标(0 隐藏、99+ 封顶)+ 下拉面板(点外部关闭)。 */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { unread, lists, ingest, refresh, openPanel, closePanel } =
    useNotificationBells();

  const onEvent = useCallback(
    (event: string, payload: Record<string, unknown>) => {
      if (event === EventType.NOTIFICATION_READ) {
        void refresh(); // 同账号他端已读:凭自身 token 重拉(空载荷 poke 不携带数值,不本地自算)
        return;
      }
      ingest(payload as unknown as NotificationMessage);
    },
    [ingest, refresh],
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
    <div ref={rootRef} className="relative">
      <button
        onClick={toggle}
        title="通知"
        className="relative cursor-pointer rounded-lg border border-line px-2 py-1 text-sm leading-none text-ink-2 transition-colors hover:bg-surface-2"
      >
        🔔
        {unread > 0 && (
          <span className="absolute -top-1.5 -right-1.5 min-w-4 rounded-full bg-st-failed px-1 text-center text-[10px] leading-4 font-medium text-white">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute top-9 right-0 z-20 max-h-[420px] w-[330px] overflow-y-auto rounded-card border border-line bg-surface py-1.5 shadow-pop">
          {groups.length === 0 && (
            <div className="px-3.5 py-4 text-center text-xs text-ink-3">
              暂无通知。订单变更与库存告警会出现在这里。
            </div>
          )}
          {groups.map(([kind, items]) => (
            <div key={kind} className="mb-1">
              <div className="flex items-center gap-1.5 bg-surface-2 px-3.5 py-1.5 text-[11px] font-semibold text-ink-2">
                {kindLabel(kind)}
                <span className="tnum text-ink-3">{items.length}</span>
              </div>
              {items.map((item) => (
                <div
                  key={item.notificationId}
                  className="flex flex-col gap-0.5 border-b border-line px-3.5 py-2 last:border-b-0"
                >
                  <span className="text-xs">{item.message}</span>
                  <span className="font-mono text-[10px] text-ink-3">
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
