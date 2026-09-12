import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApprovalCenter } from './ApprovalCenter';
import { fetchOpenApprovals } from '../services/approvals';
import { ApprovalListResponse } from '../types/events';

vi.mock('../hooks/useSocket', () => ({ useSocket: vi.fn(() => true) }));
vi.mock('../services/approvals', () => ({
  fetchOpenApprovals: vi.fn(),
  fetchActionMetadata: vi.fn(() => Promise.resolve([])),
  decideBatches: vi.fn(() => Promise.resolve()),
  executeShadowBatch: vi.fn(() => Promise.resolve()),
}));

const fetchOpenApprovalsMock = vi.mocked(fetchOpenApprovals);

const ENVELOPE: ApprovalListResponse = {
  approvals: [
    {
      batchId: 'batch-aa11',
      threadId: 'thread-aaaa1111',
      sliceNo: 2,
      actionType: 'product.publish',
      actions: [
        {
          action: 'product.publish',
          params: { product_id: 7 },
          snapshot: { status: 'draft', title: '便携咖啡机' },
        },
      ],
      status: 'pending',
      mode: 'approval',
      comment: null,
      result: null,
      runOutput: { answer: '已选出 3 个候选款,建议先上架主推款' },
    },
  ],
  plans: {
    'thread-aaaa1111': {
      request: '分析蓝牙音箱市场并上架主推款',
      plan: {
        slices: [
          {
            no: 1,
            agent: 'product_research',
            description: '市场分析',
            depends_on: [],
            approval_points: [],
          },
          {
            no: 2,
            agent: 'order_management',
            description: '上架主推款',
            depends_on: [1],
            approval_points: ['上架审批'],
          },
          {
            no: 3,
            agent: 'product_research',
            description: '生成复盘报告',
            depends_on: [2],
            approval_points: [],
          },
        ],
      },
    },
  },
};

describe('ApprovalCenter(ADR-0005 任务上下文 + 后续计划预览;spec #34)', () => {
  beforeEach(() => {
    fetchOpenApprovalsMock.mockReset();
    fetchOpenApprovalsMock.mockResolvedValue(ENVELOPE);
  });

  it('审批单携带原始需求 + 切片计划:当前切片标注「本批」,其余段同列', async () => {
    render(<ApprovalCenter />);

    expect(
      await screen.findByText('分析蓝牙音箱市场并上架主推款'),
    ).toBeTruthy();
    expect(screen.getByText('原始需求')).toBeTruthy();
    expect(screen.getByText('切片计划')).toBeTruthy();
    // 段 2 是当前批(依赖段 1 → title 取依赖,chip 文案带「本批」);段 1/3 的 Agent 中文名同列
    const current = screen.getByTitle('依赖段 1');
    expect(current.textContent).toContain('订单');
    expect(current.textContent).toContain('本批');
    expect(screen.getByTitle('市场分析').textContent).toContain('选品');
    expect(screen.getByTitle('依赖段 2').textContent).toContain('选品');
  });

  it('动作台账与任务上下文同帧:动作标题/参数/现状→目标 + 运行摘要', async () => {
    render(<ApprovalCenter />);
    await screen.findByText('分析蓝牙音箱市场并上架主推款');

    expect(screen.getByText('便携咖啡机')).toBeTruthy();
    expect(screen.getByText(/商品 7/)).toBeTruthy();
    expect(screen.getByText(/现状 草稿 → 在售/)).toBeTruthy();
    expect(screen.getByText(/已选出 3 个候选款/)).toBeTruthy();
    expect(screen.getByText('待批 1')).toBeTruthy();
  });

  it('逐批决定后提交按钮才可用(全部决定前禁用)', async () => {
    render(<ApprovalCenter />);
    await screen.findByText('分析蓝牙音箱市场并上架主推款');

    const submit = screen.getByRole('button', { name: /逐批决定后提交/ });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: '批准' }));

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: '提交本任务决定' }),
      ).toBeTruthy(),
    );
  });

  it('无任务行(plans 缺该线程):不渲染计划区,批次本体照常', async () => {
    fetchOpenApprovalsMock.mockResolvedValue({
      approvals: ENVELOPE.approvals,
      plans: {},
    });
    render(<ApprovalCenter />);

    expect(await screen.findByText('便携咖啡机')).toBeTruthy();
    expect(screen.queryByText('原始需求')).toBeNull();
    expect(screen.queryByText('切片计划')).toBeNull();
  });
});
