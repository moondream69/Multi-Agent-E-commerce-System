import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AgentMarkdown } from './AgentMarkdown';
import { Citation } from '../types/events';

// —— 引用小点(issue #51 / B28):答案里的 [n] 渲染为可点上标,点开显示被引切块原文与完整溯源 ——

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    number: 1,
    doc_id: 'faq-returns',
    title: '退款多久到账?退到哪里?',
    source: '自造 FAQ 语料库',
    published_at: '2026-09-14',
    chunks: [
      {
        id: 'faq-returns#6',
        score: 0.83,
        section: '退货退款',
        chunk_index: 6,
        content: 'Q: 退款多久到账?\nA: 仓库验收后 1-3 个工作日发起退款。',
      },
    ],
    ...overrides,
  };
}

describe('AgentMarkdown 引用小点', () => {
  it('把 [n] 渲染为可点上标,点开显示被引切块原文与完整溯源', () => {
    render(
      <AgentMarkdown citations={[citation()]}>
        仓库验收后 1-3 个工作日发起退款[1]。
      </AgentMarkdown>,
    );

    expect(screen.queryByText(/\[1\]/)).toBeNull(); // 标记已被上标替换
    const mark = screen.getByRole('button', { name: '引用 1' });
    expect(mark.textContent).toBe('1');

    fireEvent.click(mark);
    const panel = within(screen.getByRole('note'));
    expect(panel.getByText('退款多久到账?退到哪里?')).toBeTruthy(); // 标题
    expect(panel.getByText(/自造 FAQ 语料库 · 2026-09-14/)).toBeTruthy(); // 来源渠道 + 发布日期
    expect(panel.getByText('faq-returns#6')).toBeTruthy(); // 文档标识(切块标识)
    expect(panel.getByText(/退货退款 · 切块 6/)).toBeTruthy(); // 章节 + 切块序号
    expect(panel.getByText(/A: 仓库验收后 1-3 个工作日发起退款/)).toBeTruthy(); // 被引切块原文

    fireEvent.click(mark); // 再点收起
    expect(screen.queryByText('退款多久到账?退到哪里?')).toBeNull();
  });

  it('同一文档的多个被引切块归到同一编号,展开即两条原文', () => {
    const merged = citation({
      chunks: [
        citation().chunks[0],
        {
          id: 'faq-returns#9',
          score: 0.71,
          section: '退货退款',
          chunk_index: 9,
          content: 'Q: 旺季退款会变慢吗?\nA: 大促期间可能顺延 2-3 个工作日。',
        },
      ],
    });
    render(
      <AgentMarkdown citations={[merged]}>
        退款 1-3 个工作日[1];旺季可能顺延[1]。
      </AgentMarkdown>,
    );

    const marks = screen.getAllByRole('button', { name: '引用 1' });
    expect(marks).toHaveLength(2); // 两处标记共用同一编号
    fireEvent.click(marks[0]);
    const panel = within(screen.getByRole('note'));
    expect(panel.getByText(/A: 仓库验收后 1-3 个工作日发起退款/)).toBeTruthy();
    expect(panel.getByText(/A: 大促期间可能顺延 2-3 个工作日/)).toBeTruthy();
  });

  it('无引用数据时标记原样保留(无检索依据的产出不硬标)', () => {
    const { container } = render(
      <AgentMarkdown>评分 88 分(A 级),证据不足[1]。</AgentMarkdown>,
    );

    expect(screen.getByText(/证据不足\[1\]。/)).toBeTruthy();
    expect(container.querySelector('button')).toBeNull();
  });

  it('查不到的编号仍是普通文本(只有真命中才成上标)', () => {
    render(
      <AgentMarkdown citations={[citation()]}>
        退款 1-3 个工作日[1],但这条查不到[9]。
      </AgentMarkdown>,
    );

    expect(screen.getAllByRole('button', { name: /^引用/ })).toHaveLength(1);
    expect(screen.getByText(/但这条查不到\[9\]。/)).toBeTruthy();
  });

  it('加粗/列表等行内与块级结构里的标记同样成上标', () => {
    render(
      <AgentMarkdown citations={[citation()]}>
        {'- **到账时间**[1]\n- 其他说明'}
      </AgentMarkdown>,
    );

    expect(screen.getByRole('button', { name: '引用 1' })).toBeTruthy();
  });

  it('多切块引用(选品报告实拍:同文档 20 个切块)面板限高可滚,不溢出视口', () => {
    const many = citation({
      doc_id: 'usitc-global-digital-trade-1',
      chunks: Array.from({ length: 20 }, (_, index) => ({
        id: `usitc-global-digital-trade-1#${index}`,
        score: 0.5,
        section: 'pp.1-2',
        chunk_index: index,
        content: `被引片段 ${index}`,
      })),
    });
    render(
      <AgentMarkdown citations={[many]}>跨境数据流动受限[1]。</AgentMarkdown>,
    );

    fireEvent.click(screen.getByRole('button', { name: '引用 1' }));

    const panel = screen.getByRole('note');
    expect(panel.className).toContain('max-h-');
    expect(panel.className).toContain('overflow-y-auto');
    expect(within(panel).getAllByText(/被引片段 \d+/)).toHaveLength(20);
  });
});
