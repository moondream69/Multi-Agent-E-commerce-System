import { useCallback, useEffect, useRef, useState } from 'react';
import { NotificationMessage } from '../types/events';

// 通知 kind → 归属 Agent 卡片(后端契约字段;未知 kind 默认归客服卡)
const KIND_TARGET: Record<string, string> = {
  order_status: 'order-management',
  inventory_alert: 'order-management',
};
const DEFAULT_TARGET = 'customer-service';
const MAX_PER_AGENT = 50;

const STORAGE_KEY = 'notification-bells-v1';

interface BellStore {
  unread: Record<string, number>;
  lists: Record<string, NotificationMessage[]>;
}

export interface NotificationBells {
  unread: Record<string, number>;
  lists: Record<string, NotificationMessage[]>;
  openPanel: (agentId: string) => void;
  closePanel: (agentId: string) => void;
}

/** 从 localStorage 恢复(刷新后铃铛仍亮);损坏/超限时兜底为空并截断。 */
function loadStore(): BellStore {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { unread: {}, lists: {} };
    const parsed = JSON.parse(raw) as Partial<BellStore>;
    const lists: Record<string, NotificationMessage[]> = {};
    for (const [key, value] of Object.entries(parsed.lists ?? {})) {
      if (Array.isArray(value)) lists[key] = value.slice(0, MAX_PER_AGENT);
    }
    return { unread: parsed.unread ?? {}, lists };
  } catch {
    return { unread: {}, lists: {} };
  }
}

/**
 * Agent 卡片铃铛状态:新通知按 kind 归卡、每卡累计未读、每卡最近 50 条、
 * localStorage 持久化(刷新不丢);打开面板即清该卡未读,面板打开期间新通知不计未读。
 */
export function useNotificationBells(
  notifications: NotificationMessage[],
): NotificationBells {
  const [store, setStore] = useState<BellStore>(loadStore);
  const seenRef = useRef(0);
  const openRef = useRef<Record<string, boolean>>({});

  // 增量入账(ref 游标防 StrictMode double-effect 重复消费)
  useEffect(() => {
    const fresh = notifications.slice(seenRef.current);
    if (fresh.length === 0) return;
    seenRef.current = notifications.length;
    setStore((prev) => {
      const unread = { ...prev.unread };
      const lists = { ...prev.lists };
      for (const n of fresh) {
        const target = KIND_TARGET[n.kind] ?? DEFAULT_TARGET;
        lists[target] = [n, ...(lists[target] ?? [])].slice(0, MAX_PER_AGENT);
        if (!openRef.current[target]) {
          unread[target] = (unread[target] ?? 0) + 1;
        }
      }
      return { unread, lists };
    });
  }, [notifications]);

  // 持久化(写满/隐私模式静默降级为纯内存)
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
    } catch {
      // ignore
    }
  }, [store]);

  const openPanel = useCallback((agentId: string) => {
    openRef.current[agentId] = true;
    setStore((prev) => ({
      ...prev,
      unread: { ...prev.unread, [agentId]: 0 },
    }));
  }, []);

  const closePanel = useCallback((agentId: string) => {
    openRef.current[agentId] = false;
  }, []);

  return {
    unread: store.unread,
    lists: store.lists,
    openPanel,
    closePanel,
  };
}
