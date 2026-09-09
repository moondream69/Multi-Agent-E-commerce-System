import { useCallback, useEffect, useRef, useState } from 'react';
import { NotificationMessage } from '../types/events';

// 通知 kind → 中文分组标签(spec #9:面板内按 kind 分组,未知归兜底组)
const KIND_LABELS: Record<string, string> = {
  order_status: '订单状态',
  inventory_alert: '库存告警',
  fx_missing: '汇率缺失',
};
const FALLBACK_KIND = 'other';
const FALLBACK_LABEL = '其他通知';
const MAX_PER_KIND = 50;
const STORAGE_KEY = 'notification-bells-v1';

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? FALLBACK_LABEL;
}

interface BellStore {
  unread: number;
  lists: Record<string, NotificationMessage[]>;
}

export interface NotificationBells {
  unread: number;
  lists: Record<string, NotificationMessage[]>;
  /** 入账一条通知(WS 事件回调直接调用;重复 notificationId 幂等忽略)。 */
  ingest: (notification: NotificationMessage) => void;
  openPanel: () => void;
  closePanel: () => void;
}

/** 从 localStorage 恢复(刷新后铃铛仍亮);损坏/超限时兜底为空并截断。 */
function loadStore(): BellStore {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { unread: 0, lists: {} };
    const parsed = JSON.parse(raw) as Partial<BellStore>;
    const lists: Record<string, NotificationMessage[]> = {};
    for (const [key, value] of Object.entries(parsed.lists ?? {})) {
      if (Array.isArray(value)) lists[key] = value.slice(0, MAX_PER_KIND);
    }
    return {
      unread: typeof parsed.unread === 'number' ? parsed.unread : 0,
      lists,
    };
  } catch {
    return { unread: 0, lists: {} };
  }
}

/**
 * 通知铃铛状态:通知按 kind 归组、未读累计(0 隐藏/99+ 封顶由渲染层处理)、
 * 每组最近 50 条、localStorage 持久化(刷新不丢);打开面板即清零未读,
 * 面板打开期间新通知不计未读。
 *
 * 入账按条(ingest)而非消费累积数组:游标式消费在数组饱和后会静默漏计。
 */
export function useNotificationBells(): NotificationBells {
  const [store, setStore] = useState<BellStore>(loadStore);
  const openRef = useRef(false);

  const ingest = useCallback(
    (notification: NotificationMessage) => {
      setStore((prev) => {
        const kind = KIND_LABELS[notification.kind]
          ? notification.kind
          : FALLBACK_KIND;
        const existing = prev.lists[kind] ?? [];
        if (
          existing.some(
            (item) => item.notificationId === notification.notificationId,
          )
        ) {
          return prev; // 重复投递(StrictMode 双连接/重放)幂等忽略
        }
        return {
          unread: openRef.current ? prev.unread : prev.unread + 1,
          lists: {
            ...prev.lists,
            [kind]: [notification, ...existing].slice(0, MAX_PER_KIND),
          },
        };
      });
    },
    [openRef],
  );

  // 持久化(写满/隐私模式静默降级为纯内存)
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
    } catch {
      // ignore
    }
  }, [store]);

  const openPanel = useCallback(() => {
    openRef.current = true;
    setStore((prev) => ({ ...prev, unread: 0 }));
  }, [openRef]);

  const closePanel = useCallback(() => {
    openRef.current = false;
  }, [openRef]);

  return {
    unread: store.unread,
    lists: store.lists,
    ingest,
    openPanel,
    closePanel,
  };
}
