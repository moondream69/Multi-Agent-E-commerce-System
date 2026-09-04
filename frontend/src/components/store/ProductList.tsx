import React from 'react';
import { Product } from '../../types/events';

interface Props {
  products: Product[];
  onBuy: (product: Product) => void;
}

export function ProductList({ products, onBuy }: Props) {
  return (
    <div>
      <h1 style={{ margin: '0 0 4px', fontSize: 20 }}>商品商店</h1>
      <p style={{ margin: '0 0 16px', fontSize: 13, color: '#666' }}>
        选品 Agent 生成的商品会实时出现在这里
      </p>
      {products.length === 0 ? (
        <p style={{ fontSize: 13, color: '#999' }}>暂无商品</p>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
            gap: 16,
          }}
        >
          {products.map((p) => (
            <div
              key={p.id}
              style={{
                background: '#fff',
                border: '1px solid #e5e7eb',
                borderRadius: 10,
                padding: 16,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <div
                style={{
                  height: 96,
                  borderRadius: 8,
                  background: '#f1f5f9',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 12,
                  color: '#94a3b8',
                }}
              >
                {p.category}
              </div>
              <div style={{ fontSize: 14, fontWeight: 600 }}>{p.title}</div>
              <div
                style={{
                  fontSize: 12,
                  color: '#6b7280',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {p.description || '暂无描述'}
              </div>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginTop: 'auto',
                }}
              >
                <span
                  style={{ fontSize: 16, fontWeight: 700, color: '#dc2626' }}
                >
                  {p.currency} {p.price.toFixed(2)}
                </span>
                <button
                  onClick={() => onBuy(p)}
                  style={{
                    padding: '6px 14px',
                    background: '#2563eb',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 6,
                    cursor: 'pointer',
                    fontSize: 13,
                  }}
                >
                  立即购买
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
