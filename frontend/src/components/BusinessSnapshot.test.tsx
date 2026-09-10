import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BusinessSnapshot } from './BusinessSnapshot';
import { fetchReportSummary } from '../services/reports';
import { ReportSummary } from '../types/events';

vi.mock('../hooks/useSocket', () => ({ useSocket: () => true }));
vi.mock('../services/reports', () => ({ fetchReportSummary: vi.fn() }));

const fetchReportSummaryMock = vi.mocked(fetchReportSummary);

const SUMMARY: ReportSummary = {
  orders: { byStatus: { pending: 3, shipped: 2, cancelled: 1 }, total: 6 },
  revenue: {
    windowDays: 7,
    baseCurrency: 'CNY',
    amount: '1234.56',
    unconverted: 2,
  },
  lowStock: [
    {
      productId: 1,
      sku: 'SKU-A',
      title: '蓝牙音箱',
      stock: 2,
      alertThreshold: 10,
    },
  ],
  tickets: { open: 4 },
};

describe('BusinessSnapshot(spec #11 经营快照)', () => {
  beforeEach(() => {
    fetchReportSummaryMock.mockReset();
  });

  it('渲染四组聚合:状态分布(中文标签)/成交额与待核/低库存/未结工单', async () => {
    fetchReportSummaryMock.mockResolvedValue(SUMMARY);
    render(<BusinessSnapshot />);
    expect(await screen.findByText(/订单 6/)).toBeTruthy();
    expect(screen.getByText(/待处理 3/)).toBeTruthy();
    expect(screen.getByText(/已发货 2/)).toBeTruthy();
    expect(screen.getByText(/1234\.56/)).toBeTruthy();
    expect(screen.getByText(/待核 2/)).toBeTruthy();
    expect(screen.getByText(/低库存 1/)).toBeTruthy();
    expect(screen.getByText(/蓝牙音箱/)).toBeTruthy();
    expect(screen.getByText(/未结工单 4/)).toBeTruthy();
  });

  it('空态:零聚合照常渲染(订单 0 / 低库存 0 / 未结工单 0)', async () => {
    fetchReportSummaryMock.mockResolvedValue({
      orders: { byStatus: {}, total: 0 },
      revenue: {
        windowDays: 7,
        baseCurrency: 'CNY',
        amount: '0.00',
        unconverted: 0,
      },
      lowStock: [],
      tickets: { open: 0 },
    });
    render(<BusinessSnapshot />);
    expect(await screen.findByText(/订单 0/)).toBeTruthy();
    expect(screen.getByText(/低库存 0/)).toBeTruthy();
    expect(screen.getByText(/未结工单 0/)).toBeTruthy();
    expect(screen.queryByText(/待核/)).toBeNull(); // 无缺汇率订单不显示待核
  });

  it('加载失败:展示错误文案', async () => {
    fetchReportSummaryMock.mockRejectedValue(new Error('聚合服务不可用'));
    render(<BusinessSnapshot />);
    expect(await screen.findByText(/聚合服务不可用/)).toBeTruthy();
  });
});
