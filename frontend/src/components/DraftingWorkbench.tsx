import { useState } from 'react';
import { TicketList } from './TicketList';
import { draftReply } from '../services/drafting';
import { DraftingEvidence } from '../types/events';
import { theme } from '../theme';

// —— 起草工作台(B11/B19 + A11):双栏——左买家消息+查证证据+升级工单、右可编辑多语草稿+一键复制 ——

const LOCALES: Array<{ code: string; label: string }> = [
  { code: 'zh', label: '中文' },
  { code: 'en', label: '英语' },
  { code: 'ja', label: '日语' },
  { code: 'de', label: '德语' },
  { code: 'fr', label: '法语' },
];

function display(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number'
    ? String(value)
    : '';
}

function EvidenceBlock({ evidence }: { evidence: DraftingEvidence | null }) {
  if (!evidence) {
    return (
      <div style={{ fontSize: 12, color: theme.color.textMuted }}>
        生成草稿后,这里展示查证证据(FAQ 命中与订单信息)。
      </div>
    );
  }
  const faq = evidence.faq_hits ?? [];
  const order = evidence.order;
  const product = order?.product as Record<string, unknown> | undefined;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: theme.color.text }}>
        查证证据
      </div>
      {order ? (
        <div
          style={{
            fontSize: 12,
            padding: '8px 10px',
            background: theme.color.successBg,
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            color: theme.color.textSecondary,
          }}
        >
          订单 #{display(order.id)} · 状态 {display(order.status)} · 金额{' '}
          {display(order.total_amount)} {display(order.currency)}
          {product ? ` · 商品 ${display(product.title)}` : ''}
        </div>
      ) : (
        evidence.order_id != null && (
          <div
            style={{
              fontSize: 12,
              padding: '8px 10px',
              background: theme.color.warningBg,
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              color: theme.color.warning,
            }}
          >
            订单 #{display(evidence.order_id)} 未查到(未编造)
          </div>
        )
      )}
      {faq.length === 0 ? (
        <div
          style={{
            fontSize: 12,
            padding: '8px 10px',
            background: theme.color.warningBg,
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            color: theme.color.warning,
          }}
        >
          FAQ 未命中(未编造)
        </div>
      ) : (
        faq.map((hit) => {
          const payload = hit.payload ?? {};
          return (
            <div
              key={hit.id}
              style={{
                fontSize: 12,
                padding: '8px 10px',
                background: theme.color.bg,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.sm,
                color: theme.color.textSecondary,
              }}
            >
              <div style={{ color: theme.color.text }}>
                Q:{display(payload.question)}
              </div>
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
    <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
      {/* 左栏:买家消息 + 查证证据 */}
      <div
        style={{
          width: 420,
          flexShrink: 0,
          borderRight: `1px solid ${theme.color.border}`,
          background: theme.color.surface,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        }}
      >
        <div
          style={{
            padding: '12px 16px',
            borderBottom: `1px solid ${theme.color.border}`,
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div
            style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
          >
            买家消息
          </div>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="粘贴买家消息原文(任意语言)…"
            rows={5}
            style={{
              padding: '8px 10px',
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              fontSize: 13,
              resize: 'none',
              fontFamily: 'inherit',
            }}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <select
              value={locale}
              onChange={(event) => setLocale(event.target.value)}
              style={{
                padding: '6px 8px',
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.sm,
                fontSize: 13,
                background: theme.color.surface,
              }}
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
              style={{
                flex: 1,
                padding: '6px 10px',
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.sm,
                fontSize: 13,
              }}
            />
          </div>
          <button
            onClick={() => void generate()}
            disabled={busy || !message.trim()}
            style={{
              padding: '7px 0',
              border: 'none',
              borderRadius: theme.radius.sm,
              background: theme.color.brand,
              color: '#fff',
              cursor: busy ? 'not-allowed' : 'pointer',
              fontSize: 13,
              opacity: busy || !message.trim() ? 0.6 : 1,
            }}
          >
            {busy ? '查证与起草中…' : '生成草稿'}
          </button>
          {error && (
            <div style={{ fontSize: 12, color: theme.color.danger }}>
              {error}
            </div>
          )}
        </div>
        <TicketList />
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
          <EvidenceBlock evidence={evidence} />
        </div>
      </div>

      {/* 右栏:可编辑草稿 + 一键复制 */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          background: theme.color.bg,
        }}
      >
        <div
          style={{
            padding: '12px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <div
            style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
          >
            回复草稿(可编辑)
          </div>
          <button
            onClick={() => void copy()}
            disabled={!draft}
            style={{
              marginLeft: 'auto',
              padding: '6px 16px',
              border: 'none',
              borderRadius: theme.radius.sm,
              background: copied ? theme.color.success : theme.color.brand,
              color: '#fff',
              cursor: draft ? 'pointer' : 'not-allowed',
              fontSize: 13,
              opacity: draft ? 1 : 0.5,
            }}
          >
            {copied ? '已复制' : '一键复制'}
          </button>
        </div>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="草稿将显示在这里,可直接编辑后复制发出"
          style={{
            flex: 1,
            margin: '0 16px 16px',
            padding: '12px 14px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.md,
            background: theme.color.surface,
            fontSize: 14,
            lineHeight: 1.7,
            resize: 'none',
            fontFamily: 'inherit',
          }}
        />
      </div>
    </div>
  );
}
