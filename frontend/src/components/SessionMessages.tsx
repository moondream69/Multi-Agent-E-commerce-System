import { useCallback, useEffect, useState } from 'react';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { fetchConversationMessages } from '../services/conversations';
import { ConversationMessage, ConversationMessages } from '../types/events';

// —— 会话消息流(spec #20 A2 延伸 / 2026-09 重绘):切会话即见当时聊了什么 ——
// 数据源 GET /api/conversations/{sessionId}/messages(原序=时间序);展示取最新在前,
// 与任务列表/通知面板同惯例——进场无需滚动即见最近一句。实时性沿用现有任务终态事件重拉,
// 本轮不新增 WS 事件。

const ROLES: Record<string, { label: string; border: string; text: string }> = {
  user: { label: '用户', border: 'border-l-brand', text: 'text-brand' },
  assistant: {
    label: '助手',
    border: 'border-l-st-done',
    text: 'text-st-done',
  },
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
    border: 'border-l-line-strong',
    text: 'text-ink-3',
  };
  const at = formatTime(message.timestamp);
  return (
    <div
      className={`flex flex-col gap-1 rounded-lg border border-line border-l-[3px] bg-surface px-3 py-2.5 ${role.border}`}
    >
      <span className={`text-[11px] font-semibold ${role.text}`}>
        {role.label}
      </span>
      <span className="text-[13px] break-words whitespace-pre-wrap">
        {message.content}
      </span>
      {(message.taskId || at) && (
        <span className="font-mono text-[10px] text-ink-3">
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
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mb-1 font-mono text-xs text-ink-3">
        会话消息
        {stream && ` · ${stream.conversation.messageCount} 条`}
      </div>
      <div className="mb-3 text-xs text-ink-3">
        点左侧任务卡查看该任务的切片时间线;点会话名回到这里。
      </div>
      {error && (
        <div className="mb-3 rounded-lg border border-st-failed/40 bg-st-failed-bg px-3 py-2 text-[13px] text-st-failed">
          消息流加载失败:{error}
        </div>
      )}
      {stream === undefined && !error && (
        <div className="text-[13px] text-ink-3">加载中…</div>
      )}
      {stream !== undefined && messages.length === 0 && (
        <div className="text-[13px] text-ink-3">
          该会话暂无对话记录。在左侧输入需求即可开始。
        </div>
      )}
      <div className="flex max-w-[620px] flex-col gap-2">
        {messages.map((message, index) => (
          <MessageRow key={index} message={message} />
        ))}
      </div>
    </div>
  );
}
