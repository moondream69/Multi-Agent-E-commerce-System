import React from 'react';
import { NotificationBells } from '../hooks/useNotificationBells';
import { AgentEvent, AgentInfo } from '../types/events';
import { NotificationBell } from './NotificationBell';
import { theme } from '../theme';

interface Props {
  agents: AgentInfo[];
  events: AgentEvent[];
  bells: NotificationBells;
}

const AGENT_STATUS_META: Record<
  string,
  { label: string; color: string; bg: string }
> = {
  idle: {
    label: 'idle',
    color: theme.color.success,
    bg: theme.color.successBg,
  },
  busy: {
    label: 'busy',
    color: theme.color.warning,
    bg: theme.color.warningBg,
  },
  error: {
    label: 'error',
    color: theme.color.danger,
    bg: theme.color.dangerBg,
  },
  offline: { label: 'offline', color: theme.color.textMuted, bg: '#f3f4f6' },
};

// 事件类型 → 语义状态灯:业务=绿、生命周期=蓝、告警/异常=红、其余=灰
const EVENT_DOT: Record<string, string> = {
  'report.generated': theme.color.success,
  'product.created': theme.color.success,
  'product.updated': theme.color.success,
  'order.status_changed': theme.color.success,
  'reply.generated': theme.color.success,
  'customer.notification': theme.color.brand,
  'inventory.alert': theme.color.danger,
  'escalation.triggered': theme.color.danger,
  'task.assigned': theme.color.brand,
  'task.completed': theme.color.brand,
  'task.failed': theme.color.danger,
  'agent.status_changed': theme.color.brand,
  'approval.requested': theme.color.warning,
  'approval.decided': theme.color.brand,
};

// 事件类型 → 中文名(给非技术用户看,隐藏原始英文事件码)
const EVENT_LABEL: Record<string, string> = {
  'report.generated': '选品报告生成',
  'product.created': '商品已创建',
  'product.updated': '商品已更新',
  'order.status_changed': '订单状态变更',
  'reply.generated': '客服回复生成',
  'customer.notification': '客户通知',
  'inventory.alert': '库存告警',
  'escalation.triggered': '工单升级',
  'task.assigned': '任务已分配',
  'task.completed': '任务完成',
  'task.failed': '任务失败',
  'agent.status_changed': 'Agent 状态变化',
  'approval.requested': '审批请求',
  'approval.decided': '审批已决定',
};

// Agent id → 中文名(静态映射,与后端注册一致)
const AGENT_NAME: Record<string, string> = {
  'product-research': '选品分析',
  'order-management': '订单管理',
  'customer-service': '智能客服',
};

const STATUS_ZH: Record<string, string> = {
  idle: '空闲',
  busy: '忙碌',
  error: '异常',
  offline: '离线',
  pending: '待处理',
  in_progress: '处理中',
  completed: '完成',
  failed: '失败',
  approved: '已通过',
  rejected: '已拒绝',
  expired: '已超时',
  confirmed: '已确认',
  processing: '备货中',
  shipped: '已发货',
  delivered: '已送达',
  cancelled: '已取消',
  returned: '已退货',
};

const TOOL_ZH: Record<string, string> = {
  product_crud: '商品管理',
  order_workflow: '订单工作流',
  inventory_alert: '库存告警',
  anomaly_detection: '异常检测',
  trend_query: '趋势查询',
  competitor_analysis: '竞品分析',
  scoring: '选品评分',
  report_generator: '报告生成',
  translator: '翻译',
  faq_search: 'FAQ 检索',
  sentiment_analysis: '情感分析',
  template_manager: '模板管理',
  order_lookup: '订单查询',
  escalate_ticket: '工单升级',
};

function shortId(id: unknown): string {
  if (typeof id !== 'string') return '';
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

/** unknown → 展示字符串(object 走 JSON,避免 [object Object])。 */
function str(v: unknown): string {
  if (v == null) return '';
  if (
    typeof v === 'string' ||
    typeof v === 'number' ||
    typeof v === 'boolean'
  ) {
    return String(v);
  }
  return JSON.stringify(v);
}

/** payload → 一行人类可读摘要(确定性转换,按事件类型加中文语境)。 */
function eventSummary(evt: AgentEvent): string {
  const p = evt.payload as Record<string, unknown> | undefined;
  if (!p || typeof p !== 'object') return '';
  switch (evt.type) {
    case 'agent.status_changed':
      return p.agentId
        ? `${AGENT_NAME[str(p.agentId)] ?? str(p.agentId)}${STATUS_ZH[str(p.status)] ? ` → ${STATUS_ZH[str(p.status)]}` : ''}`
        : '';
    case 'task.assigned':
    case 'task.completed':
    case 'task.failed':
      return p.agentId
        ? `${AGENT_NAME[str(p.agentId)] ?? str(p.agentId)}${p.taskId ? ` · 任务 ${shortId(p.taskId)}` : ''}`
        : '';
    case 'order.status_changed':
      return `订单 ${shortId(p.orderId)}${p.from ? ` 由${STATUS_ZH[str(p.from)] ?? str(p.from)}` : ''} ${STATUS_ZH[str(p.to)] ? `转为${STATUS_ZH[str(p.to)]}` : `变更为 ${str(p.to)}`}`;
    case 'product.created':
      return (p.product as Record<string, unknown> | undefined)?.title
        ? `「${str((p.product as Record<string, unknown>).title)}」已创建`
        : '';
    case 'product.updated':
      return p.productId
        ? `商品 ${shortId(p.productId)}${p.status ? ` → ${STATUS_ZH[str(p.status)] ?? str(p.status)}` : ''}`
        : '';
    case 'inventory.alert':
      return p.message ? str(p.message) : '库存低于安全线';
    case 'escalation.triggered':
      return p.reason ? `升级原因: ${str(p.reason)}` : '问题升级人工处理';
    case 'reply.generated':
      return p.templateId ? `已套用模板 ${str(p.templateId)}` : '';
    case 'customer.notification':
      return p.message ? str(p.message) : '';
    case 'report.generated':
      return p.title ? `「${str(p.title)}」已生成` : '报告已生成';
    case 'approval.requested':
      return `${TOOL_ZH[str(p.toolName)] ?? str(p.toolName)}待审批${p.mode === 'shadow' ? '(影子建议)' : ''}`;
    case 'approval.decided':
      return p.approve ? '操作已获批准' : '操作被拒绝';
    default:
      return '';
  }
}

export function Dashboard({ agents, events, bells }: Props) {
  return (
    <div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 16,
          marginBottom: 24,
        }}
      >
        {agents.map((agent) => {
          const meta =
            AGENT_STATUS_META[agent.status] ?? AGENT_STATUS_META.offline;
          return (
            <div
              key={agent.id}
              style={{
                background: theme.color.surface,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.md,
                padding: 16,
                boxShadow: theme.shadow.card,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 8,
                }}
              >
                <h3
                  style={{ margin: 0, fontSize: 15, color: theme.color.text }}
                >
                  {agent.name}
                </h3>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <NotificationBell
                    items={bells.lists[agent.id] ?? []}
                    unread={bells.unread[agent.id] ?? 0}
                    onOpen={() => bells.openPanel(agent.id)}
                    onClose={() => bells.closePanel(agent.id)}
                  />
                  <span
                    style={{
                      padding: '2px 10px',
                      borderRadius: theme.radius.full,
                      fontSize: 11,
                      fontWeight: 600,
                      fontFamily: theme.font.mono,
                      background: meta.bg,
                      color: meta.color,
                    }}
                  >
                    {meta.label}
                  </span>
                </div>
              </div>
              <p
                style={{
                  margin: 0,
                  fontSize: 12,
                  color: theme.color.textSecondary,
                }}
              >
                {agent.description}
              </p>
              <div
                style={{
                  marginTop: 8,
                  fontSize: 11,
                  color: theme.color.textMuted,
                  fontFamily: theme.font.mono,
                }}
              >
                {agent.tools?.length ?? 0} tools
              </div>
            </div>
          );
        })}
      </div>

      <h2 style={{ fontSize: 16, marginBottom: 8, color: theme.color.text }}>
        实时事件流
      </h2>
      <div
        style={{
          background: theme.color.surface,
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.md,
          padding: '8px 12px',
          maxHeight: 300,
          overflow: 'auto',
          boxShadow: theme.shadow.card,
        }}
      >
        {events.length === 0 && (
          <p style={{ color: theme.color.textMuted, fontSize: 13 }}>暂无事件</p>
        )}
        {events.map((evt, i) => (
          <div
            key={i}
            style={{
              padding: '5px 0',
              borderBottom: '1px solid #f1f5f9',
              fontSize: 12,
              display: 'flex',
              alignItems: 'baseline',
              gap: 8,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: EVENT_DOT[evt.type] ?? theme.color.textMuted,
                flexShrink: 0,
                alignSelf: 'center',
              }}
            />
            <span
              style={{
                color: theme.color.text,
                fontWeight: 500,
                fontSize: 12,
              }}
            >
              {EVENT_LABEL[evt.type] ?? evt.type}
            </span>
            <span
              style={{
                color: theme.color.textMuted,
                marginLeft: 'auto',
                fontFamily: theme.font.mono,
                fontSize: 11,
                flexShrink: 0,
              }}
            >
              {new Date(evt.timestamp).toLocaleTimeString()}
            </span>
            {(() => {
              const summary = eventSummary(evt);
              if (!summary) return null;
              return (
                <span
                  style={{
                    color: theme.color.textSecondary,
                    fontSize: 11,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    maxWidth: '45%',
                  }}
                >
                  {summary}
                </span>
              );
            })()}
          </div>
        ))}
      </div>
    </div>
  );
}
