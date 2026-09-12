import { useCallback, useState } from 'react';
import { useSocket } from '../hooks/useSocket';
import { EventType } from '../types/events';

// —— 事件流实况墙(A13 / 2026-09 重绘):任务/切片/审批事件,语义配色 + 中文标签 ——

interface WallEvent {
  id: string;
  event: string;
  payload: Record<string, unknown>;
  at: string;
}

const EVENT_LABELS: Record<string, string> = {
  [EventType.TASK_CREATED]: '任务已创建',
  [EventType.TASK_PLANNED]: '规划完成',
  [EventType.TASK_INTERRUPTED]: '任务挂起',
  [EventType.TASK_COMPLETED]: '任务完成',
  [EventType.TASK_FAILED]: '任务失败',
  [EventType.SLICE_STARTED]: '切片分派',
  [EventType.SLICE_COMPLETED]: '切片完成',
  [EventType.APPROVAL_REQUESTED]: '审批请求',
  [EventType.APPROVAL_DECIDED]: '审批已决定',
};

type EventTone =
  'done' | 'planning' | 'running' | 'approval' | 'failed' | 'neutral';

// 显式静态映射:Tailwind 只收录源码中字面出现的类名(动态拼接会丢样式)
const TONE_CLASS: Record<EventTone, { text: string; dot: string }> = {
  done: { text: 'text-st-done', dot: 'bg-st-done' },
  planning: { text: 'text-st-planning', dot: 'bg-st-planning' },
  running: { text: 'text-st-running', dot: 'bg-st-running' },
  approval: { text: 'text-st-approval', dot: 'bg-st-approval' },
  failed: { text: 'text-st-failed', dot: 'bg-st-failed' },
  neutral: { text: 'text-ink-2', dot: 'bg-st-idle' },
};

function eventTone(event: string, payload: Record<string, unknown>): EventTone {
  switch (event) {
    case EventType.TASK_COMPLETED:
    case EventType.APPROVAL_DECIDED:
      return 'done';
    case EventType.TASK_PLANNED:
      return 'planning';
    case EventType.SLICE_STARTED:
      return 'running';
    case EventType.SLICE_COMPLETED:
      return payload.status === 'failed' || payload.status === 'rejected'
        ? 'failed'
        : 'done';
    case EventType.TASK_INTERRUPTED:
    case EventType.APPROVAL_REQUESTED:
      return 'approval';
    case EventType.TASK_FAILED:
      return 'failed';
    default:
      return 'neutral';
  }
}

const EVENT_NAMES = Object.keys(EVENT_LABELS);
const MAX_EVENTS = 50;

function shortId(id: unknown): string {
  return typeof id === 'string' || typeof id === 'number'
    ? String(id).slice(0, 8)
    : '';
}

export function EventWall() {
  const [events, setEvents] = useState<WallEvent[]>([]);

  const onEvent = useCallback(
    (event: string, payload: Record<string, unknown>) => {
      setEvents((prev) =>
        [
          ...prev,
          {
            id: `${Date.now()}-${Math.random()}`,
            event,
            payload,
            at: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
          },
        ].slice(-MAX_EVENTS),
      );
    },
    [],
  );
  const connected = useSocket(EVENT_NAMES, onEvent);

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden rounded-card border border-line bg-surface shadow-card">
      <div className="flex items-baseline gap-2 border-b border-line px-3.5 py-3">
        <span className="text-[13px] font-semibold">事件流实况墙</span>
        <span
          className={`flex items-center gap-1.5 font-mono text-[11px] ${
            connected ? 'text-st-done' : 'text-ink-3'
          }`}
        >
          <span
            className={`size-1.5 rounded-full ${connected ? 'bg-st-done' : 'bg-st-idle'}`}
          />
          {connected ? '已连接' : '未连接'}
        </span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-3">
        {events.length === 0 && (
          <div className="mt-10 text-center text-xs text-ink-3">
            暂无事件。任务与审批活动会实时出现在这里。
          </div>
        )}
        {events.map((item) => (
          <div
            key={item.id}
            className="flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5"
          >
            <span
              className={`size-1.5 shrink-0 rounded-full ${TONE_CLASS[eventTone(item.event, item.payload)].dot}`}
            />
            <span
              className={`text-xs font-medium whitespace-nowrap ${TONE_CLASS[eventTone(item.event, item.payload)].text}`}
            >
              {EVENT_LABELS[item.event] ?? item.event}
            </span>
            <span className="truncate font-mono text-[11px] text-ink-3">
              {shortId(item.payload.threadId)}
            </span>
            <span className="ml-auto shrink-0 font-mono text-[11px] text-ink-3">
              {item.at}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
