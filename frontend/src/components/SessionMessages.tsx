import { useCallback, useEffect, useState } from 'react';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { fetchConversationMessages } from '../services/conversations';
import { ConversationMessage, ConversationMessages } from '../types/events';
import { theme } from '../theme';

// —— 会话消息流(spec #20 A2 延伸):切会话即见当时聊了什么(用户原文 + 助手回复)——
// 数据源 GET /api/conversations/{sessionId}/messages(原序=时间序);展示取最新在前,
// 与任务列表/通知面板同惯例——进场无需滚动即见最近一句。实时性沿用现有任务终态事件重拉,
// 本轮不新增 WS 事件。

const ROLES: Record<string, { label: string; color: string }> = {
  user: { label: '用户', color: theme.color.brand },
  assistant: { label: '助手', color: theme.color.success },
};

function shortId(id: string): string {
  return id.slice(0, 8);
}

function formatTime(timestamp: string | null): string {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

function MessageRow({ message }: { message: ConversationMessage }) {
  const role = ROLES[message.role] ?? {
    label: message.role,
    color: theme.color.textMuted,
  };
  const at = formatTime(message.timestamp);
  return (
    <div
      style={{
        background: theme.color.surface,
        border: `1px solid ${theme.color.border}`,
        borderLeft: `3px solid ${role.color}`,
        borderRadius: theme.radius.sm,
        padding: '9px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <span style={{ fontSize: 11, fontWeight: 600, color: role.color }}>
        {role.label}
      </span>
      <span
        style={{
          fontSize: 13,
          color: theme.color.text,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {message.content}
      </span>
      {(message.taskId || at) && (
        <span
          style={{
            fontSize: 10,
            fontFamily: theme.font.mono,
            color: theme.color.textMuted,
          }}
        >
          {message.taskId && `任务 ${shortId(message.taskId)} · `}
          {at}
        </span>
      )}
    </div>
  );
}

/** 当前会话的对话轨迹:切会话即重拉(旧会话内容先清,不残留);加载中/空态/失败态齐备。 */
export function SessionMessages({ sessionId }: { sessionId: string }) {
  // undefined = 拉取中;null = 会话未落库/不存在(404)——空白会话的常态,按空态展示而非故障
  const [stream, setStream] = useState<ConversationMessages | null | undefined>(
    undefined,
  );
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchConversationMessages(sessionId)
      .then((row) => {
        setStream(row);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, [sessionId]);

  useEffect(() => {
    setStream(undefined);
    setError(null);
    refresh();
  }, [refresh]);
  // 助手回复在任务终态落库:沿用现有事件重拉(与经营快照/工单列表同钩子)
  useTaskTerminalEvents(refresh);

  const messages = stream ? [...stream.messages].reverse() : [];

  return (
    <div
      style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px' }}
    >
      <div
        style={{
          fontSize: 13,
          fontFamily: theme.font.mono,
          color: theme.color.textMuted,
          marginBottom: 4,
        }}
      >
        会话消息
        {stream && ` · ${stream.conversation.messageCount} 条`}
      </div>
      <div
        style={{
          fontSize: 12,
          color: theme.color.textMuted,
          marginBottom: 12,
        }}
      >
        点左侧任务卡查看该任务的切片时间线;点会话名回到这里。
      </div>
      {error && (
        <div
          style={{
            marginBottom: 12,
            padding: '8px 12px',
            background: theme.color.dangerBg,
            border: `1px solid ${theme.color.danger}`,
            borderRadius: theme.radius.sm,
            color: theme.color.danger,
            fontSize: 13,
          }}
        >
          消息流加载失败:{error}
        </div>
      )}
      {stream === undefined && !error && (
        <div style={{ fontSize: 13, color: theme.color.textMuted }}>
          加载中…
        </div>
      )}
      {stream !== undefined && messages.length === 0 && (
        <div style={{ fontSize: 13, color: theme.color.textMuted }}>
          该会话暂无对话记录。在左侧输入需求即可开始。
        </div>
      )}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxWidth: 560,
        }}
      >
        {messages.map((message, index) => (
          <MessageRow key={index} message={message} />
        ))}
      </div>
    </div>
  );
}
