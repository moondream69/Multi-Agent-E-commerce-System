import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ChatPanel } from './components/ChatPanel';
import { Dashboard } from './components/Dashboard';
import { OrderList } from './components/store/OrderList';
import { ProductList } from './components/store/ProductList';
import { useWebSocket } from './hooks/useWebSocket';
import {
  createOrder,
  fetchAgents,
  fetchOrders,
  fetchProducts,
} from './services/api';
import { AgentEventType, AgentInfo, Order, Product } from './types/events';
import { theme } from './theme';

type View = 'cockpit' | 'store' | 'orders' | 'support';

const NAV_ITEMS: Array<{ key: View; label: string }> = [
  { key: 'cockpit', label: '驾驶舱' },
  { key: 'store', label: '商品商店' },
  { key: 'orders', label: '我的订单' },
  { key: 'support', label: '客服中心' },
];

export default function App() {
  const {
    events,
    sendMessage,
    connected,
    lastResponse,
    statuses,
    notifications,
  } = useWebSocket();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [view, setView] = useState<View>('cockpit');

  const refreshProducts = useCallback(() => {
    fetchProducts().then(setProducts).catch(console.error);
  }, []);
  const refreshOrders = useCallback(() => {
    fetchOrders().then(setOrders).catch(console.error);
  }, []);

  // socket 首连/重连时拉取一次状态快照,补齐断线期间丢失的状态变化(断线时 statuses 已清空)
  const wasConnected = useRef(false);
  useEffect(() => {
    if (connected && !wasConnected.current) {
      fetchAgents().then(setAgents).catch(console.error);
    }
    wasConnected.current = connected;
  }, [connected]);

  useEffect(() => {
    refreshProducts();
    refreshOrders();
  }, [refreshProducts, refreshOrders]);

  // 业务事件驱动前台刷新:订单状态变化刷新订单,商品事件刷新商店
  useEffect(() => {
    const last = events[0];
    if (!last) return;
    if (last.type === AgentEventType.ORDER_STATUS_CHANGED) refreshOrders();
    if (
      last.type === AgentEventType.PRODUCT_CREATED ||
      last.type === AgentEventType.PRODUCT_UPDATED
    ) {
      refreshProducts();
    }
  }, [events, refreshOrders, refreshProducts]);

  const handleBuy = (product: Product) => {
    createOrder(product.id)
      .then(() => {
        setView('orders');
        refreshOrders();
      })
      .catch((err) => console.error('下单失败', err));
  };

  const liveAgents = agents.map((a) => ({
    ...a,
    status: statuses[a.id] ?? a.status,
  }));

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        fontFamily: theme.font.body,
        margin: 0,
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '0 16px',
          height: 52,
          borderBottom: `1px solid ${theme.color.border}`,
          background: theme.color.surface,
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            margin: '0 16px 0 0',
            fontSize: 16,
            fontWeight: 600,
            color: theme.color.text,
          }}
        >
          Multi-Agent 跨境电商系统
        </h1>
        <nav style={{ display: 'flex', gap: 4 }}>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              onClick={() => setView(item.key)}
              style={{
                padding: '6px 14px',
                border: 'none',
                borderRadius: theme.radius.sm,
                cursor: 'pointer',
                fontSize: 13,
                background:
                  view === item.key ? theme.color.brand : 'transparent',
                color: view === item.key ? '#fff' : theme.color.textSecondary,
              }}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div
          style={{
            marginLeft: 'auto',
            color: connected ? theme.color.success : theme.color.danger,
            fontSize: 13,
            fontFamily: theme.font.mono,
          }}
        >
          {connected ? '已连接' : '未连接'}
        </div>
      </header>

      {view === 'cockpit' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <div
            style={{
              flex: 1,
              padding: 24,
              overflow: 'auto',
              background: theme.color.bg,
            }}
          >
            <Dashboard agents={liveAgents} events={events} />
          </div>
          <div
            style={{
              width: 420,
              borderLeft: '1px solid #e0e0e0',
              background: '#fff',
            }}
          >
            <ChatPanel
              onSend={sendMessage}
              lastResponse={lastResponse}
              notifications={notifications}
            />
          </div>
        </div>
      )}
      {view === 'store' && (
        <div
          style={{
            flex: 1,
            padding: 24,
            overflow: 'auto',
            background: theme.color.bg,
          }}
        >
          <ProductList products={products} onBuy={handleBuy} />
        </div>
      )}
      {view === 'orders' && (
        <div
          style={{
            flex: 1,
            padding: 24,
            overflow: 'auto',
            background: theme.color.bg,
          }}
        >
          <OrderList orders={orders} />
        </div>
      )}
      {view === 'support' && (
        <div style={{ flex: 1, minHeight: 0 }}>
          <ChatPanel
            onSend={sendMessage}
            lastResponse={lastResponse}
            notifications={notifications}
            title="客服中心"
            placeholder="输入问题，如：我的订单到哪里了？"
          />
        </div>
      )}
    </div>
  );
}
