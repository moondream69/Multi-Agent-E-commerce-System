import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DraftingWorkbench } from './DraftingWorkbench';
import { draftReply } from '../services/drafting';
import { DraftingEvidence, DraftingResponse } from '../types/events';

vi.mock('../services/drafting', () => ({ draftReply: vi.fn() }));
vi.mock('./TicketList', () => ({
  TicketList: () => <div data-testid="ticket-list" />,
}));

const draftReplyMock = vi.mocked(draftReply);

function evidence(overrides: Partial<DraftingEvidence> = {}): DraftingEvidence {
  return {
    faq_hits: [],
    order: null,
    order_id: null,
    products: [],
    products_truncated: false,
    ...overrides,
  };
}

function response(payload: Partial<DraftingEvidence> = {}): DraftingResponse {
  return { draft: '亲,这款还有货哦。', evidence: evidence(payload) };
}

function generate() {
  const result = render(<DraftingWorkbench />);
  const { getByPlaceholderText, getByRole } = result;
  fireEvent.change(getByPlaceholderText('粘贴买家消息原文(任意语言)…'), {
    target: { value: '这个商品还有货吗' },
  });
  fireEvent.click(getByRole('button', { name: '生成草稿' }));
  return result;
}

describe('DraftingWorkbench(issue #39 起草台商品查证)', () => {
  beforeEach(() => {
    draftReplyMock.mockReset();
  });

  it('商品命中:证据区展示 SKU/价格/库存', async () => {
    draftReplyMock.mockResolvedValue(
      response({
        products: [
          {
            id: 42,
            sku: 'SYN-PT-042',
            title: '宠物饮水机 雾灰款',
            price: '29.90',
            currency: 'USD',
            category: '宠物用品',
            status: 'active',
            stock: 7,
          },
        ],
      }),
    );
    generate();
    expect(await screen.findByText(/宠物饮水机 雾灰款/)).toBeTruthy();
    expect(screen.getByText(/SYN-PT-042/)).toBeTruthy();
    expect(screen.getByText(/29.90 USD/)).toBeTruthy();
    expect(screen.getByText(/库存 7/)).toBeTruthy();
  });

  it('无命中:如实呈现未匹配,不编造', async () => {
    draftReplyMock.mockResolvedValue(response());
    generate();
    expect(await screen.findByText('商品未匹配(未编造)')).toBeTruthy();
  });

  it('命中截断:提示仅列前 5 条', async () => {
    draftReplyMock.mockResolvedValue(
      response({
        products_truncated: true,
        products: [
          {
            id: 1,
            sku: 'SYN-HM-001',
            title: '保温杯',
            price: '12.90',
            currency: 'USD',
            category: '家居厨房',
            status: 'active',
            stock: 50,
          },
        ],
      }),
    );
    generate();
    expect(await screen.findByText(/仅列前 5 条/)).toBeTruthy();
  });
});

const MARKDOWN_DRAFT =
  '## 回复要点\n\n亲,**现货充足**:\n\n- 已锁定库存\n- 48 小时内发出';

describe('DraftingWorkbench(A16:草稿 Markdown 渲染)', () => {
  beforeEach(() => {
    draftReplyMock.mockReset();
  });

  it('生成后默认预览:草稿按 Markdown 渲染成元素', async () => {
    draftReplyMock.mockResolvedValue({
      draft: MARKDOWN_DRAFT,
      evidence: evidence(),
    });

    generate();

    const heading = await screen.findByText('回复要点');
    expect(heading.tagName).toBe('H2');
    expect(screen.getByText('现货充足').tagName).toBe('STRONG');
    expect(
      screen.getAllByRole('listitem').map((node) => node.textContent),
    ).toEqual(['已锁定库存', '48 小时内发出']);
  });

  it('切「编辑」回到原文:textarea 内是 Markdown 原文,可继续编辑', async () => {
    draftReplyMock.mockResolvedValue({
      draft: MARKDOWN_DRAFT,
      evidence: evidence(),
    });
    generate();
    await screen.findByText('回复要点');

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));

    const box = screen.getByPlaceholderText<HTMLTextAreaElement>(
      '草稿将显示在这里,可直接编辑后复制发出',
    );
    expect(box.value).toBe(MARKDOWN_DRAFT);
    expect(screen.queryByText('回复要点')).toBeNull(); // 渲染面已让位给编辑面

    fireEvent.change(box, { target: { value: '改过的草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '预览' }));

    expect(screen.getByText('改过的草稿')).toBeTruthy();
  });
});
