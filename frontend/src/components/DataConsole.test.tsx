import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { DataConsole } from './DataConsole';
import { fetchOrders, fetchProducts } from '../services/console';
import { EventType, OrderListItem, ProductListItem } from '../types/events';
import { useSocket } from '../hooks/useSocket';

vi.mock('../hooks/useSocket', () => ({ useSocket: vi.fn(() => true) }));
vi.mock('../services/console', () => ({
  fetchProducts: vi.fn(),
  fetchOrders: vi.fn(),
}));

const fetchProductsMock = vi.mocked(fetchProducts);
const fetchOrdersMock = vi.mocked(fetchOrders);
const useSocketMock = vi.mocked(useSocket);

const PRODUCTS: ProductListItem[] = [
  {
    id: 1,
    sku: 'DEMO-KT-001',
    title: '便携咖啡机',
    price: '45.90',
    currency: 'USD',
    category: '厨房',
    status: 'active',
    stock: 4,
    alertThreshold: 10,
  },
  {
    id: 2,
    sku: 'DEMO-KT-002',
    title: '手冲咖啡壶套装',
    price: '28.50',
    currency: 'USD',
    category: '厨房',
    status: 'draft',
    stock: 24,
    alertThreshold: 10,
  },
];

const ORDERS: OrderListItem[] = [
  {
    id: 11,
    reference: 'ORD-11',
    productId: 1,
    customerId: 3,
    status: 'pending',
    totalAmount: '45.90',
    currency: 'USD',
    fxRate: '7.12340000',
    fxBaseCurrency: 'CNY',
    platform: 'amazon',
    createdAt: '2026-09-12T08:30:00+00:00',
  },
  {
    id: 12,
    reference: null,
    productId: 2,
    customerId: null,
    status: 'shipped',
    totalAmount: '28.50',
    currency: 'USD',
    fxRate: null,
    fxBaseCurrency: 'CNY',
    platform: 'amazon',
    createdAt: '2026-09-11T08:30:00+00:00',
  },
];

describe('DataConsole(ADR-0006 / spec #34 数据台)', () => {
  beforeEach(() => {
    fetchProductsMock.mockReset();
    fetchOrdersMock.mockReset();
    useSocketMock.mockClear();
    fetchProductsMock.mockResolvedValue(PRODUCTS);
    fetchOrdersMock.mockResolvedValue({ orders: ORDERS, total: 2 });
  });

  it('商品表:全量拉取 + 低库存高亮 + 状态中文标签', async () => {
    render(<DataConsole onAskAgent={() => {}} />);

    expect(await screen.findByText('便携咖啡机')).toBeTruthy();
    expect(screen.getByText('DEMO-KT-002')).toBeTruthy();
    expect(screen.getByText(/低于阈值 10/)).toBeTruthy();
    expect(screen.getAllByText('在售').length).toBeGreaterThan(0); // 状态列(筛选下拉同文案)
    expect(screen.getAllByText('草稿').length).toBeGreaterThan(0);
    expect(screen.getByText('共 2 件')).toBeTruthy();
  });

  it('商品筛选与排序在前端:关键词命中 SKU/标题,库存升序把缺货排前', async () => {
    render(<DataConsole onAskAgent={() => {}} />);
    await screen.findByText('便携咖啡机');

    fireEvent.change(screen.getByPlaceholderText('搜索 SKU / 标题'), {
      target: { value: 'KT-002' },
    });
    expect(screen.getByText('手冲咖啡壶套装')).toBeTruthy();
    expect(screen.queryByText('便携咖啡机')).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('搜索 SKU / 标题'), {
      target: { value: '' },
    });
    fireEvent.change(screen.getAllByRole('combobox')[1], {
      target: { value: 'stock' },
    });
    const rows = screen.getAllByRole('row').slice(1); // 去表头
    expect(rows[0].textContent).toContain('便携咖啡机'); // 库存 4 < 24
    expect(fetchProductsMock).toHaveBeenCalledTimes(1); // 筛选不触发重拉
  });

  it('问 Agent:商品行预填话术并交给外层跳转(不代发)', async () => {
    const onAskAgent = vi.fn();
    render(<DataConsole onAskAgent={onAskAgent} />);
    await screen.findByText('便携咖啡机');

    fireEvent.click(screen.getAllByText('问 Agent')[0]);

    expect(onAskAgent).toHaveBeenCalledWith(
      '查看商品 DEMO-KT-001 便携咖啡机 的库存与定价,给运营建议',
    );
  });

  it('订单表:状态筛选与分页走服务端,商品名客户端拼装,缺汇率显「待核」', async () => {
    render(<DataConsole onAskAgent={() => {}} />);
    await screen.findByText('便携咖啡机');

    fireEvent.click(screen.getByText('订单'));
    expect(await screen.findByText('ORD-11')).toBeTruthy();
    expect(fetchOrdersMock).toHaveBeenLastCalledWith({
      status: undefined,
      limit: 50,
      offset: 0,
    });
    expect(screen.getByText('便携咖啡机 · DEMO-KT-001')).toBeTruthy(); // 客户端拼装
    expect(screen.getByText('待核')).toBeTruthy(); // fxRate null
    expect(screen.getAllByText('已发货').length).toBeGreaterThan(0); // 状态列(筛选下拉同文案)
    expect(screen.getByText('第 1–2 / 共 2 单')).toBeTruthy();

    fireEvent.change(screen.getAllByRole('combobox')[0], {
      target: { value: 'shipped' },
    });
    await waitFor(() =>
      expect(fetchOrdersMock).toHaveBeenLastCalledWith({
        status: 'shipped',
        limit: 50,
        offset: 0,
      }),
    );
  });

  it('翻页:下一页按 50 步进并向服务端要新页(总数不变)', async () => {
    fetchOrdersMock.mockResolvedValue({ orders: ORDERS, total: 120 });
    render(<DataConsole onAskAgent={() => {}} />);
    await screen.findByText('便携咖啡机');
    fireEvent.click(screen.getByText('订单'));
    await screen.findByText('第 1–2 / 共 120 单');

    fireEvent.click(screen.getByText('下一页'));

    await waitFor(() =>
      expect(fetchOrdersMock).toHaveBeenLastCalledWith({
        status: undefined,
        limit: 50,
        offset: 50,
      }),
    );
  });

  it('订单行「问 Agent」:无 reference 回落 #id', async () => {
    const onAskAgent = vi.fn();
    render(<DataConsole onAskAgent={onAskAgent} />);
    await screen.findByText('便携咖啡机');
    fireEvent.click(screen.getByText('订单'));
    await screen.findByText('ORD-11');

    fireEvent.click(screen.getAllByText('问 Agent')[1]);

    expect(onAskAgent).toHaveBeenCalledWith('查一下订单 #12 的状态与履约情况');
  });

  it('notification.created 广播 → 两张表重新拉取(审批 apply/下单后无需手动刷新)', async () => {
    render(<DataConsole onAskAgent={() => {}} />);
    await screen.findByText('便携咖啡机');
    expect(fetchProductsMock).toHaveBeenCalledTimes(1);

    const subscription = useSocketMock.mock.calls.find(([events]) =>
      events.includes(EventType.NOTIFICATION_CREATED),
    );
    expect(subscription).toBeTruthy();
    act(() => {
      subscription![1](EventType.NOTIFICATION_CREATED, {});
    });

    await waitFor(() => expect(fetchProductsMock).toHaveBeenCalledTimes(2));
    expect(fetchOrdersMock).toHaveBeenCalledTimes(2);
  });
});
