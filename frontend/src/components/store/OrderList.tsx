import React from 'react';
import { Order, OrderStatus } from '../../types/events';

interface Props {
  orders: Order[];
}

const STATUS_META: Record<
  OrderStatus,
  { label: string; color: string; bg: string }
> = {
  pending: { label: '待确认', color: '#6b7280', bg: '#f3f4f6' },
  confirmed: { label: '已确认', color: '#1d4ed8', bg: '#eff6ff' },
  processing: { label: '处理中', color: '#b45309', bg: '#fffbeb' },
  shipped: { label: '已发货', color: '#7e22ce', bg: '#faf5ff' },
  delivered: { label: '已送达', color: '#15803d', bg: '#f0fdf4' },
  cancelled: { label: '已取消', color: '#b91c1c', bg: '#fef2f2' },
  returned: { label: '已退货', color: '#c2410c', bg: '#fff7ed' },
};

export function OrderList({ orders }: Props) {
  return (
    <div>
      <h1 style={{ margin: '0 0 4px', fontSize: 20 }}>我的订单</h1>
      <p style={{ margin: '0 0 16px', fontSize: 13, color: '#666' }}>
        订单状态由订单处理 Agent 驱动,实时同步
      </p>
      {orders.length === 0 ? (
        <p style={{ fontSize: 13, color: '#999' }}>暂无订单</p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {orders.map((o) => {
            const meta = STATUS_META[o.status] ?? STATUS_META.pending;
            return (
              <div
                key={o.id}
                style={{
                  background: '#fff',
                  border: '1px solid #e5e7eb',
                  borderRadius: 10,
                  padding: '14px 16px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 16,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>
                    {o.product?.title ?? '未知商品'}
                  </div>
                  <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 2 }}>
                    订单号 {o.id.slice(0, 8)} ·{' '}
                    {new Date(o.createdAt).toLocaleString('zh-CN')}
                  </div>
                </div>
                <span style={{ fontSize: 15, fontWeight: 700 }}>
                  {o.currency} {Number(o.totalAmount).toFixed(2)}
                </span>
                <span
                  style={{
                    padding: '3px 10px',
                    borderRadius: 999,
                    fontSize: 12,
                    fontWeight: 600,
                    color: meta.color,
                    background: meta.bg,
                  }}
                >
                  {meta.label}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
