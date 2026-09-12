import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import { BusinessSnapshot } from './BusinessSnapshot';
import { fetchReportSummary } from '../services/reports';
import { EventType, ReportSummary } from '../types/events';
import { useSocket } from '../hooks/useSocket';

vi.mock('../hooks/useSocket', () => ({ useSocket: vi.fn(() => true) }));
vi.mock('../services/reports', () => ({ fetchReportSummary: vi.fn() }));

const fetchReportSummaryMock = vi.mocked(fetchReportSummary);
const useSocketMock = vi.mocked(useSocket);

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
    useSocketMock.mockClear();
  });

  /** 2026-09 重绘:标签与数值分列——按标签定位统计块,取块内首个数值元素(.tnum)。 */
  function statValue(label: string): string {
    const block = screen.getByText(label, { exact: true })
      .parentElement as HTMLElement;
    return block.querySelector('.tnum')?.textContent ?? '';
  }

  it('渲染四组聚合:状态分布(中文标签)/成交额与待核/低库存/未结工单', async () => {
    fetchReportSummaryMock.mockResolvedValue(SUMMARY);
    render(<BusinessSnapshot />);
    expect(await screen.findByText('订单', { exact: true })).toBeTruthy();
    expect(statValue('订单')).toBe('6');
    expect(screen.getByText(/待处理 3/)).toBeTruthy();
    expect(screen.getByText(/已发货 2/)).toBeTruthy();
    expect(statValue('近 7 日成交额')).toBe('1234.56');
    expect(screen.getByText(/待核 2/)).toBeTruthy();
    expect(statValue('低库存')).toBe('1');
    expect(screen.getByText(/蓝牙音箱/)).toBeTruthy();
    expect(statValue('未结工单')).toBe('4');
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
    expect(await screen.findByText('订单', { exact: true })).toBeTruthy();
    expect(statValue('订单')).toBe('0');
    expect(statValue('低库存')).toBe('0');
    expect(statValue('未结工单')).toBe('0');
    expect(screen.queryByText(/待核/)).toBeNull(); // 无缺汇率订单不显示待核
  });

  it('加载失败:展示错误文案', async () => {
    fetchReportSummaryMock.mockRejectedValue(new Error('聚合服务不可用'));
    render(<BusinessSnapshot />);
    expect(await screen.findByText(/聚合服务不可用/)).toBeTruthy();
  });

  it('notification.created 广播 → 重新拉取快照(走查缺陷:纯 REST 下单无任务终态事件)', async () => {
    fetchReportSummaryMock.mockResolvedValue(SUMMARY);
    render(<BusinessSnapshot />);
    await screen.findByText('订单', { exact: true });
    expect(fetchReportSummaryMock).toHaveBeenCalledTimes(1);

    // 下单提交后的 notification.created 广播是快照刷新的唯一信号(REST 直连不产生任务事件)
    const subscription = useSocketMock.mock.calls.find(([events]) =>
      events.includes(EventType.NOTIFICATION_CREATED),
    );
    expect(subscription).toBeTruthy();
    act(() => {
      subscription![1](EventType.NOTIFICATION_CREATED, {});
    });
    await waitFor(() =>
      expect(fetchReportSummaryMock).toHaveBeenCalledTimes(2),
    );
  });
});
