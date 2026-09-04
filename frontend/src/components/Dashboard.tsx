import React from 'react';
import { AgentEvent, AgentInfo } from '../types/events';
import { theme } from '../theme';

interface Props {
  agents: AgentInfo[];
  events: AgentEvent[];
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
};

export function Dashboard({ agents, events }: Props) {
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
                fontFamily: theme.font.mono,
                fontSize: 11,
              }}
            >
              {evt.type}
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
              if (!evt.payload || typeof evt.payload !== 'object') return null;
              const p = evt.payload as Record<string, unknown>;
              const summary = ['taskId', 'agentId', 'status']
                .filter((k) => p[k] != null)
                .map((k) => `${k}:${String(p[k])}`)
                .join(' ');
              if (!summary) return null;
              return (
                <span
                  style={{
                    color: theme.color.textSecondary,
                    fontFamily: theme.font.mono,
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
