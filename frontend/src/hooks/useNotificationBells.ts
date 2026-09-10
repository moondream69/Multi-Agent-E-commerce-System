import { useCallback, useEffect, useReducer, useRef } from 'react';
import {
  fetchNotifications,
  markNotificationsRead,
} from '../services/notifications';
import { NotificationFeed, NotificationMessage } from '../types/events';

// 通知 kind → 中文分组标签(spec #9:面板内按 kind 分组,未知归兜底组)
const KIND_LABELS: Record<string, string> = {
  order_status: '订单状态',
  inventory_alert: '库存告警',
  fx_missing: '汇率缺失',
};
const FALLBACK_KIND = 'other';
const FALLBACK_LABEL = '其他通知';
const MAX_PER_KIND = 50; // 与服务端截断同值(增量 8-T2):WS 入账在本地同口径封顶

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? FALLBACK_LABEL;
}

function groupOf(kind: string): string {
  return KIND_LABELS[kind] ? kind : FALLBACK_KIND;
}

/** 信封列表 → kind 归组字典(服务端序即最新在前,不再重排)。 */
function groupByKind(
  notifications: NotificationMessage[],
): Record<string, NotificationMessage[]> {
  const lists: Record<string, NotificationMessage[]> = {};
  for (const notification of notifications) {
    const kind = groupOf(notification.kind);
    lists[kind] = [...(lists[kind] ?? []), notification];
  }
  return lists;
}

interface BellState {
  unread: number;
  lists: Record<string, NotificationMessage[]>;
}

/** 单次原子转移:去重、归组、未读累计同源,避免双 state 各自更新导致的计数漂移。 */
function reduce(
  state: BellState,
  action:
    | { type: 'feed'; feed: NotificationFeed }
    | { type: 'ingest'; notification: NotificationMessage; panelOpen: boolean }
    | { type: 'read' },
): BellState {
  if (action.type === 'feed') {
    return {
      unread: action.feed.unread,
      lists: groupByKind(action.feed.notifications),
    };
  }
  if (action.type === 'read') {
    return { ...state, unread: 0 }; // 打开面板:本地清零(服务端同步幂等清零)
  }
  const kind = groupOf(action.notification.kind);
  const existing = state.lists[kind] ?? [];
  if (
    existing.some(
      (item) => item.notificationId === action.notification.notificationId,
    )
  ) {
    return state; // 重复投递(StrictMode 双连接/重放/与拉取重叠)幂等忽略
  }
  return {
    unread: action.panelOpen ? state.unread : state.unread + 1,
    lists: {
      ...state.lists,
      [kind]: [action.notification, ...existing].slice(0, MAX_PER_KIND),
    },
  };
}

export interface NotificationBells {
  unread: number;
  lists: Record<string, NotificationMessage[]>;
  /** 入账一条通知(WS 事件回调直接调用;重复 notificationId 幂等忽略)。 */
  ingest: (notification: NotificationMessage) => void;
  /** 重拉服务端真源(挂载时自动调用;notification.read poke 后由订阅方调用)。 */
  refresh: () => Promise<void>;
  openPanel: () => void;
  closePanel: () => void;
}

/**
 * 通知铃铛状态(增量 8-T2:服务端真源,按用户已读):
 * 挂载即拉取(每组最近 50 条由服务端截断;未读计数来自服务端);WS 入账保留
 * notificationId 去重与 kind 归组;打开面板即标记已读(服务端幂等清零);
 * 面板打开期间新到通知立即标记已读;拉取失败保持空态(不阻塞其余 UI)。
 *
 * 入账按条(ingest)而非消费累积数组:游标式消费在数组饱和后会静默漏计。
 */
export function useNotificationBells(): NotificationBells {
  const [state, dispatch] = useReducer(reduce, { unread: 0, lists: {} });
  const openRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const feed = await fetchNotifications();
      dispatch({ type: 'feed', feed });
    } catch {
      // 拉取失败保持空态(不阻塞其余 UI;下次挂载/事件重拉自愈)
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const ingest = useCallback((notification: NotificationMessage) => {
    dispatch({ type: 'ingest', notification, panelOpen: openRef.current });
    if (openRef.current) {
      // 面板打开期间到达即读(我正看着它):服务端不残留未读,本地不累计
      void markNotificationsRead().catch(() => {});
    }
  }, []);

  const openPanel = useCallback(() => {
    openRef.current = true;
    dispatch({ type: 'read' });
    void markNotificationsRead().catch(() => {});
  }, []);

  const closePanel = useCallback(() => {
    openRef.current = false;
  }, []);

  return {
    unread: state.unread,
    lists: state.lists,
    ingest,
    refresh,
    openPanel,
    closePanel,
  };
}
