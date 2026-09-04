import { AgentInfo, Order, Product } from '../types/events';

const BASE = '/api';

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await fetch(`${BASE}/dashboard/agents`);
  return (await res.json()) as AgentInfo[];
}

export async function fetchProducts(): Promise<Product[]> {
  const res = await fetch(`${BASE}/products`);
  return (await res.json()) as Product[];
}

export async function fetchOrders(): Promise<Order[]> {
  const res = await fetch(`${BASE}/orders`);
  return (await res.json()) as Order[];
}

export async function createOrder(productId: string): Promise<Order> {
  const res = await fetch(`${BASE}/orders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ productId }),
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as Order;
}
