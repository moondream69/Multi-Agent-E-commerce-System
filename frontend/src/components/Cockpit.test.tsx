import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CsvImportCard, SliceTimeline } from './Cockpit';
import { TaskDetail } from '../types/events';
import { importCsv } from '../services/imports';

vi.mock('../services/imports', () => ({ importCsv: vi.fn() }));

const importCsvMock = vi.mocked(importCsv);

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

describe('SliceTimeline(issue #52:切片答案 + 引用小点)', () => {
  function detailWithAnswer(): TaskDetail {
    const detail = detailWith({ summary: '完成 1/1 个切片' });
    detail.results = {
      '1': {
        executed: true,
        answer: '跨境数据流动仍受限[1];壁垒因市场而异[1]。',
        citations: [
          {
            number: 1,
            doc_id: 'usitc-global-digital-trade-1',
            title: 'Global Digital Trade 1',
            source: 'U.S. International Trade Commission',
            published_at: '2017-08-01',
            chunks: [
              {
                id: 'usitc-global-digital-trade-1#583',
                score: 0.56,
                section: 'pp.150-151',
                chunk_index: 583,
                content: 'Digital trade barriers vary by market.',
              },
            ],
          },
        ],
      },
    };
    return detail;
  }

  it('切片产出答案:渲染在切片行下,[1] 可点开显示被引切块原文与溯源', () => {
    render(
      <SliceTimeline detail={detailWithAnswer()} onOpenApprovals={() => {}} />,
    );

    expect(screen.getByText(/跨境数据流动仍受限/)).toBeTruthy();
    const marks = screen.getAllByRole('button', { name: '引用 1' });
    expect(marks).toHaveLength(2); // 同编号多处共用同一引用

    fireEvent.click(marks[0]);
    const panel = within(screen.getByRole('note'));
    expect(panel.getByText('Global Digital Trade 1')).toBeTruthy();
    expect(
      panel.getByText(/U.S. International Trade Commission · 2017-08-01/),
    ).toBeTruthy();
    expect(panel.getByText('usitc-global-digital-trade-1#583')).toBeTruthy();
    expect(
      panel.getByText(/Digital trade barriers vary by market/),
    ).toBeTruthy();
  });

  it('切片无答案(待执行 / 未完成):不渲染答案块', () => {
    render(
      <SliceTimeline
        detail={detailWith({ summary: '完成 1/1 个切片' })}
        onOpenApprovals={() => {}}
      />,
    );

    expect(screen.getByText('查询库存')).toBeTruthy(); // 时间线主体照常
    expect(screen.queryByText(/跨境数据流动仍受限/)).toBeNull();
    expect(screen.queryByRole('button', { name: /引用/ })).toBeNull();
  });
});

describe('CsvImportCard(issue #30:窄栏下导入控件布局)', () => {
  it('控件纵向堆叠:选择框的固有宽度不再挤压文件输入', () => {
    render(<CsvImportCard />);

    // 左栏宽 280px,选择框被最长选项文案撑到 202px;横排会把文件输入压成 42px 窄条。
    // 2026-09 重绘:Tailwind 类替代内联样式,断言改查类名。
    const controls = screen.getByRole('combobox').parentElement as HTMLElement;
    expect(controls.className).toContain('flex-col');
  });

  it('导入路径照常:选类型 → 选文件 → 渲染行级报告', async () => {
    importCsvMock.mockResolvedValue({ created: 12, skipped: 0, errors: [] });
    const { container } = render(<CsvImportCard />);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const csv = 'sku,title,price,category\nDEMO-A,甲商品,1.00,家居';
    const file = new File([csv], 'products.csv', { type: 'text/csv' });

    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'products' },
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByText(/新建 12 · 跳过 0 · 错误 0/)).toBeTruthy();
    expect(importCsvMock).toHaveBeenCalledWith('products', csv);
  });

  it('导入后重置文件输入 value:同名文件重选仍触发 change(走查缺陷:浏览器对同路径不派发 change)', async () => {
    importCsvMock.mockResolvedValue({ created: 1, skipped: 0, errors: [] });
    const { container } = render(<CsvImportCard />);
    const fileInput = container.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    const file = new File(
      ['sku,title,price,category\nDEMO-B,乙商品,2.00,家居'],
      'products.csv',
      { type: 'text/csv' },
    );
    const setterSpy = vi.spyOn(fileInput, 'value', 'set');

    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByText(/新建 1 · 跳过 0 · 错误 0/)).toBeTruthy();
    expect(setterSpy.mock.calls.some(([value]) => value === '')).toBe(true);
  });
});
