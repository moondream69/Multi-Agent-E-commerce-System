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
  const { getByPlaceholderText, getByRole } = render(<DraftingWorkbench />);
  fireEvent.change(getByPlaceholderText('粘贴买家消息原文(任意语言)…'), {
    target: { value: '这个商品还有货吗' },
  });
  fireEvent.click(getByRole('button', { name: '生成草稿' }));
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
