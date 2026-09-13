import { useState } from 'react';
import { TicketList } from './TicketList';
import { AgentMarkdown } from './AgentMarkdown';
import { draftReply } from '../services/drafting';
import { DraftingEvidence } from '../types/events';

// —— 起草工作台(B11/B19 + A11):双栏——左买家消息+查证证据+升级工单、右可编辑多语草稿+一键复制 ——

const LOCALES: Array<{ code: string; label: string }> = [
  { code: 'zh', label: '中文' },
  { code: 'en', label: '英语' },
  { code: 'ja', label: '日语' },
  { code: 'de', label: '德语' },
  { code: 'fr', label: '法语' },
];

const inputClass =
  'rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[13px] outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25';

const DRAFT_EMPTY_HINT = '草稿将显示在这里,可直接编辑后复制发出';

function display(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number'
    ? String(value)
    : '';
}

function EvidenceBlock({ evidence }: { evidence: DraftingEvidence | null }) {
  if (!evidence) {
    return (
      <div className="text-xs text-ink-3">
        生成草稿后,这里展示查证证据(FAQ 命中与订单信息)。
      </div>
    );
  }
  const faq = evidence.faq_hits ?? [];
  const order = evidence.order;
  const products = evidence.products ?? [];
  const product = order?.product as Record<string, unknown> | undefined;
  return (
    <div className="flex flex-col gap-2.5">
      <div className="text-xs font-semibold">查证证据</div>
      {order ? (
        <div className="rounded-lg border border-st-done/30 bg-st-done-bg px-2.5 py-2 text-xs text-ink-2">
          订单 #{display(order.id)} · 状态 {display(order.status)} · 金额{' '}
          {display(order.total_amount)} {display(order.currency)}
          {product ? ` · 商品 ${display(product.title)}` : ''}
        </div>
      ) : (
        evidence.order_id != null && (
          <div className="rounded-lg border border-st-approval/40 bg-st-approval-bg px-2.5 py-2 text-xs text-st-approval">
            订单 #{display(evidence.order_id)} 未查到(未编造)
          </div>
        )
      )}
      {products.length === 0 ? (
        <div className="rounded-lg border border-st-approval/40 bg-st-approval-bg px-2.5 py-2 text-xs text-st-approval">
          商品未匹配(未编造)
        </div>
      ) : (
        <>
          {products.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-xs text-ink-2"
            >
              商品 #{item.id} · {item.title} · {item.sku} · {item.price}{' '}
              {item.currency} · 库存 {item.stock} · {item.status}
            </div>
          ))}
          {evidence.products_truncated && (
            <div className="px-1 text-xs text-ink-3">
              命中过多,仅列前 5 条——可让买家提供 SKU 或更具体的名称。
            </div>
          )}
        </>
      )}
      {faq.length === 0 ? (
        <div className="rounded-lg border border-st-approval/40 bg-st-approval-bg px-2.5 py-2 text-xs text-st-approval">
          FAQ 未命中(未编造)
        </div>
      ) : (
        faq.map((hit) => {
          const payload = hit.payload ?? {};
          return (
            <div
              key={hit.id}
              className="rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-xs text-ink-2"
            >
              <div className="text-ink">Q:{display(payload.question)}</div>
              <div>A:{display(payload.answer)}</div>
            </div>
          );
        })
      )}
    </div>
  );
}

export function DraftingWorkbench() {
  const [message, setMessage] = useState('');
  const [locale, setLocale] = useState('zh');
  const [orderId, setOrderId] = useState('');
  const [evidence, setEvidence] = useState<DraftingEvidence | null>(null);
  const [draft, setDraft] = useState('');
  // A16:草稿默认以 Markdown 预览呈现(生成后即回预览),需要改字再切「编辑」落到 textarea
  const [mode, setMode] = useState<'preview' | 'edit'>('preview');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const generate = async () => {
    if (!message.trim() || busy) return;
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const parsedOrderId = orderId.trim() ? Number(orderId.trim()) : undefined;
      if (orderId.trim() && Number.isNaN(parsedOrderId)) {
        throw new Error('订单号须为数字');
      }
      const response = await draftReply(message.trim(), locale, parsedOrderId);
      setDraft(response.draft);
      setEvidence(response.evidence);
      setMode('preview');
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError('复制失败:浏览器未授予剪贴板权限');
    }
  };

  return (
    <div className="flex min-h-0 flex-1">
      {/* 左栏:买家消息 + 查证证据 */}
      <div className="flex w-[420px] shrink-0 flex-col border-r border-line bg-surface">
        <div className="flex flex-col gap-2 border-b border-line px-4 py-3">
          <div className="text-[13px] font-semibold">买家消息</div>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="粘贴买家消息原文(任意语言)…"
            rows={5}
            className={`resize-none ${inputClass}`}
          />
          <div className="flex gap-2">
            <select
              value={locale}
              onChange={(event) => setLocale(event.target.value)}
              className={inputClass}
            >
              {LOCALES.map((item) => (
                <option key={item.code} value={item.code}>
                  草稿语言:{item.label}
                </option>
              ))}
            </select>
            <input
              value={orderId}
              onChange={(event) => setOrderId(event.target.value)}
              placeholder="订单号(可选,用于查证)"
              className={`min-w-0 flex-1 ${inputClass}`}
            />
          </div>
          <button
            onClick={() => void generate()}
            disabled={busy || !message.trim()}
            className="cursor-pointer rounded-lg bg-brand py-2 text-[13px] font-medium text-brand-contrast transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? '查证与起草中…' : '生成草稿'}
          </button>
          {error && <div className="text-xs text-st-failed">{error}</div>}
        </div>
        <TicketList />
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <EvidenceBlock evidence={evidence} />
        </div>
      </div>

      {/* 右栏:草稿预览(默认)/ 编辑 + 一键复制 */}
      <div className="flex min-w-0 flex-1 flex-col bg-bg">
        <div className="flex items-center gap-2 px-4 py-3">
          <div className="text-[13px] font-semibold">回复草稿</div>
          <div className="flex rounded-lg border border-line p-0.5 text-xs">
            {(['preview', 'edit'] as const).map((item) => (
              <button
                key={item}
                onClick={() => setMode(item)}
                aria-pressed={mode === item}
                className={`cursor-pointer rounded-md px-2.5 py-1 transition-colors ${
                  mode === item
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-ink-3 hover:text-ink-2'
                }`}
              >
                {item === 'preview' ? '预览' : '编辑'}
              </button>
            ))}
          </div>
          <button
            onClick={() => void copy()}
            disabled={!draft}
            className={`ml-auto cursor-pointer rounded-lg px-4 py-1.5 text-[13px] font-medium text-brand-contrast transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              copied ? 'bg-st-done' : 'bg-brand hover:brightness-110'
            }`}
          >
            {copied ? '已复制' : '一键复制'}
          </button>
        </div>
        {mode === 'edit' ? (
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={DRAFT_EMPTY_HINT}
            className="mx-4 mb-4 min-h-0 flex-1 resize-none rounded-card border border-line bg-surface px-3.5 py-3 text-sm leading-[1.7] outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
          />
        ) : (
          <div className="mx-4 mb-4 min-h-0 flex-1 overflow-y-auto rounded-card border border-line bg-surface px-3.5 py-3 text-sm leading-[1.7] text-ink">
            {draft ? (
              <AgentMarkdown>{draft}</AgentMarkdown>
            ) : (
              <span className="text-ink-3">{DRAFT_EMPTY_HINT}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
