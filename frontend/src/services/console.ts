import { OrderListResponse, ProductListItem } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

export interface OrderQuery {
  /** 订单状态(服务端筛选);空 = 全部 */
  status?: string;
  limit?: number;
  offset?: number;
}

/** 商品列表(spec #9 / #34):数据台盘货数据源,全量返回(商品量级小,筛选排序在前端)。 */
export async function fetchProducts(): Promise<ProductListItem[]> {
  const res = await apiFetch(`${BASE}/products`);
  if (!res.ok) throw new Error(await res.text());
  const body = (await res.json()) as { products: ProductListItem[] };
  return body.products;
}

/** 订单列表(spec #34):数据台对账数据源,服务端状态筛选 + 分页,回传同筛选总数。 */
export async function fetchOrders(
  query: OrderQuery = {},
): Promise<OrderListResponse> {
  const params = new URLSearchParams();
  if (query.status) params.set('status', query.status);
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  if (query.offset !== undefined) params.set('offset', String(query.offset));
  const suffix = params.size > 0 ? `?${params.toString()}` : '';
  const res = await apiFetch(`${BASE}/orders${suffix}`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as OrderListResponse;
}
