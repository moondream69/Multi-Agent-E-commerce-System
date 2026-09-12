import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FxCard } from './FxCard';
import { fetchFxCard } from '../services/fx';
import { FxCardPayload } from '../types/events';
import { useSocket } from '../hooks/useSocket';

vi.mock('../hooks/useSocket', () => ({ useSocket: vi.fn(() => true) }));
vi.mock('../services/fx', () => ({ fetchFxCard: vi.fn() }));

const fetchFxCardMock = vi.mocked(fetchFxCard);
const useSocketMock = vi.mocked(useSocket);

const DAY_MS = 86_400_000;

/** 以真实今天为锚生成日期(组件的横轴按「距今几天」定位)。 */
function daysAgo(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
}

function payload(overrides: Partial<FxCardPayload> = {}): FxCardPayload {
  return {
    base: 'CNY',
    currency: 'USD',
    rate: '7.12340000',
    cachedAt: '2026-09-12T08:30:00+00:00',
    source: 'cache',
    trend: { windowDays: 7, points: [{ date: daysAgo(1), rate: '7.1000' }] },
    ...overrides,
  };
}

describe('FxCard(ADR-0006 / spec #34 汇率卡片)', () => {
  beforeEach(() => {
    fetchFxCardMock.mockReset();
    useSocketMock.mockClear();
  });

  it('渲染当期汇率 + 来源与缓存时刻 + 走势口径说明', async () => {
    fetchFxCardMock.mockResolvedValue(payload());
    render(<FxCard />);

    expect(await screen.findByText('7.12340000')).toBeTruthy();
    expect(screen.getByText(/1 USD = 7.12340000 CNY/)).toBeTruthy();
    expect(screen.getByText(/^缓存 /)).toBeTruthy();
    expect(screen.getByText(/近 7 日 · 1 个成交日/)).toBeTruthy();
    expect(screen.getByText('USD→CNY')).toBeTruthy();
  });

  it('汇率不可用:显「待核」并说明后果,走势照常可读', async () => {
    fetchFxCardMock.mockResolvedValue(
      payload({
        rate: null,
        cachedAt: null,
        source: null,
        trend: {
          windowDays: 7,
          points: [
            { date: daysAgo(2), rate: '7.1000' },
            { date: daysAgo(1), rate: '7.2000' },
          ],
        },
      }),
    );
    render(<FxCard />);

    expect(await screen.findByText('待核')).toBeTruthy();
    expect(screen.getByText(/下单将留空快照待人工核对/)).toBeTruthy();
    expect(screen.getByText(/近 7 日 · 2 个成交日/)).toBeTruthy();
  });

  it('走势按日定位:相邻日连线,空档不连线(不伪造中间值)', async () => {
    fetchFxCardMock.mockResolvedValue(
      payload({
        trend: {
          windowDays: 7,
          // 前 3 天连续(2 段线),第 4 个点与前一个隔 3 天(断点)
          points: [
            { date: daysAgo(6), rate: '7.0000' },
            { date: daysAgo(3), rate: '7.1000' },
            { date: daysAgo(2), rate: '7.1500' },
            { date: daysAgo(1), rate: '7.2000' },
          ],
        },
      }),
    );
    const { container } = render(<FxCard />);
    await screen.findByText(/个成交日/);

    expect(container.querySelectorAll('line')).toHaveLength(2); // 3→2 与 2→1 两段
    expect(container.querySelectorAll('circle')).toHaveLength(4); // 每个成交日一个点
  });

  it('无成交快照:走势区显「暂无成交快照」,不画空图', async () => {
    fetchFxCardMock.mockResolvedValue(
      payload({ trend: { windowDays: 7, points: [] } }),
    );
    const { container } = render(<FxCard />);

    expect(await screen.findByText(/近 7 日暂无成交快照/)).toBeTruthy();
    expect(container.querySelector('svg')).toBeNull();
  });

  it('加载失败:展示错误文案', async () => {
    fetchFxCardMock.mockRejectedValue(new Error('汇率服务不可用'));
    render(<FxCard />);

    expect(await screen.findByText(/汇率服务不可用/)).toBeTruthy();
  });
});
