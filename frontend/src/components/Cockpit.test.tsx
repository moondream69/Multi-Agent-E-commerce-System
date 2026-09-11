import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SliceTimeline } from './Cockpit';
import { TaskDetail } from '../types/events';

function detailWith(result: TaskDetail['result']): TaskDetail {
  return {
    threadId: 'thread-aaaa1111',
    sessionId: 's-1',
    type: 'default',
    status: 'completed',
    request: '查库存',
    plan: {
      slices: [
        {
          no: 1,
          agent: 'order_management',
          description: '查询库存',
          depends_on: [],
          approval_points: [],
        },
      ],
    },
    results: { '1': { executed: true } },
    result,
    batches: [],
    createdAt: '2026-09-11T10:00:00+00:00',
  };
}

describe('SliceTimeline(spec #25:summary 字符串口径与渲染防御)', () => {
  it('summary 为字符串:渲染总结块,时间线主体同帧可见', () => {
    render(
      <SliceTimeline
        detail={detailWith({ summary: '完成 2/2 个切片' })}
        onOpenApprovals={() => {}}
      />,
    );

    expect(screen.getByText('完成 2/2 个切片')).toBeTruthy();
    expect(screen.getByText('查库存')).toBeTruthy();
    expect(screen.getByText('查询库存')).toBeTruthy();
  });

  it('summary 为对象(历史脏行):不渲染且不抛错', () => {
    const legacy = {
      summary: { slices: [], results: {} },
      error: null,
    } as unknown as TaskDetail['result'];

    render(
      <SliceTimeline detail={detailWith(legacy)} onOpenApprovals={() => {}} />,
    );

    expect(screen.getByText('查库存')).toBeTruthy(); // 渲染完成、时间线可见
    expect(screen.queryByText(/slices/)).toBeNull(); // 对象内容不作为文本出现
  });
});
