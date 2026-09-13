import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionMessages } from './SessionMessages';
import { fetchConversationMessages } from '../services/conversations';
import {
  ConversationMessage,
  ConversationMessages,
  EventType,
} from '../types/events';

// WS 事件回调捕获:mock useSocket 记录订阅事件与 onEvent,测试内直接触发
const socket = vi.hoisted(() => ({
  events: [] as string[],
  handler: null as (() => void) | null,
}));

vi.mock('../hooks/useSocket', () => ({
  useSocket: (events: string[], onEvent: () => void) => {
    socket.events = events;
    socket.handler = onEvent;
    return true;
  },
}));

vi.mock('../services/conversations', () => ({
  fetchConversationMessages: vi.fn(),
}));

const fetchMock = vi.mocked(fetchConversationMessages);

function stream(
  sessionId: string,
  messages: ConversationMessages['messages'],
): ConversationMessages {
  return {
    conversation: {
      sessionId,
      title: `${sessionId} 的标题`,
      updatedAt: '2026-09-11T10:00:00+00:00',
      messageCount: messages.length,
    },
    messages,
  };
}

const STREAM_A = stream('s-1', [
  {
    role: 'user',
    content: '第一问',
    timestamp: '2026-09-11T09:00:00+00:00',
    taskId: 'thread-aaaa1111',
  },
  {
    role: 'assistant',
    content: '第二答',
    timestamp: '2026-09-11T09:00:05+00:00',
    taskId: 'thread-aaaa1111',
  },
  {
    role: 'user',
    content: '第三问',
    timestamp: '2026-09-11T09:10:00+00:00',
    taskId: null,
  },
]);

describe('SessionMessages(spec #20 会话消息流)', () => {
  beforeEach(() => {
    socket.events = [];
    socket.handler = null;
    fetchMock.mockReset();
  });

  it('渲染消息流:原序存储 → 展示最新在前,角色/任务短号/时间齐备', async () => {
    fetchMock.mockResolvedValue(STREAM_A);

    render(<SessionMessages sessionId="s-1" />);

    expect(await screen.findByText('第三问')).toBeTruthy();
    expect(screen.getByText(/会话消息 · 3 条/)).toBeTruthy();
    const contents = screen
      .getAllByText(/第一问|第二答|第三问/)
      .map((node) => node.textContent);
    expect(contents).toEqual(['第三问', '第二答', '第一问']); // 最新在前
    expect(screen.getAllByText('用户')).toHaveLength(2);
    expect(screen.getByText('助手')).toBeTruthy();
    expect(screen.getAllByText(/任务 thread-a · /)).toHaveLength(2); // 短号 8 位;无 taskId 的行不显示
    expect(screen.getAllByText(/\d{2}:\d{2}:\d{2}/)).toHaveLength(3);
  });

  it('空态:已落库会话暂无消息时给出提示', async () => {
    fetchMock.mockResolvedValue(stream('s-1', []));

    render(<SessionMessages sessionId="s-1" />);

    expect(await screen.findByText(/该会话暂无对话记录/)).toBeTruthy();
  });

  it('空态:会话未落库(后端 404 → null)不显示失败态', async () => {
    fetchMock.mockResolvedValue(null); // 空白会话惰性落库:未发言的会话没有行

    render(<SessionMessages sessionId="s-new" />);

    expect(await screen.findByText(/该会话暂无对话记录/)).toBeTruthy();
    expect(screen.queryByText(/消息流加载失败/)).toBeNull();
  });

  it('失败态:展示错误文案且不阻塞其余 UI', async () => {
    fetchMock.mockRejectedValue(new Error('会话 s-1 不存在'));

    render(<SessionMessages sessionId="s-1" />);

    expect(
      await screen.findByText('消息流加载失败:会话 s-1 不存在'),
    ).toBeTruthy();
    expect(screen.getByText(/会话消息/)).toBeTruthy();
  });

  it('切会话重拉:以新 sessionId 请求,旧会话内容不残留', async () => {
    fetchMock.mockResolvedValue(STREAM_A);
    const { rerender } = render(<SessionMessages sessionId="s-1" />);
    expect(await screen.findByText('第三问')).toBeTruthy();

    fetchMock.mockResolvedValue(
      stream('s-2', [
        {
          role: 'user',
          content: '乙会话的发言',
          timestamp: '2026-09-11T11:00:00+00:00',
          taskId: null,
        },
      ]),
    );
    rerender(<SessionMessages sessionId="s-2" />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('s-2'));
    expect(await screen.findByText('乙会话的发言')).toBeTruthy();
    expect(screen.queryByText('第三问')).toBeNull();
    expect(screen.getByText(/会话消息 · 1 条/)).toBeTruthy();
  });

  it('任务终态事件到达即重拉(沿用现有事件,不新增 WS 事件)', async () => {
    fetchMock.mockResolvedValue(STREAM_A);
    render(<SessionMessages sessionId="s-1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    act(() => socket.handler?.());

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(socket.events).toEqual([
      EventType.TASK_COMPLETED,
      EventType.TASK_FAILED,
    ]);
  });
});

function oneMessage(
  role: ConversationMessage['role'],
  content: string,
): ConversationMessages {
  return stream('s-1', [
    { role, content, timestamp: '2026-09-14T09:00:00+00:00', taskId: null },
  ]);
}

describe('SessionMessages(A16:Agent 消息 Markdown 渲染)', () => {
  beforeEach(() => {
    socket.events = [];
    socket.handler = null;
    fetchMock.mockReset();
  });

  it('Agent 消息按 Markdown 渲染:标题/粗体/列表各成元素', async () => {
    fetchMock.mockResolvedValue(
      oneMessage(
        'assistant',
        '## 选品结论\n\n**主推款**如下:\n\n- 便携咖啡机\n- 蓝牙音箱',
      ),
    );

    render(<SessionMessages sessionId="s-1" />);

    const heading = await screen.findByText('选品结论');
    expect(heading.tagName).toBe('H2');
    expect(screen.getByText('主推款').tagName).toBe('STRONG');
    expect(
      screen.getAllByRole('listitem').map((node) => node.textContent),
    ).toEqual(['便携咖啡机', '蓝牙音箱']);
  });

  it('用户消息保持纯文本:同样的 Markdown 记号是字面文本,不成元素', async () => {
    fetchMock.mockResolvedValue(oneMessage('user', '**不要加粗**'));

    const { container } = render(<SessionMessages sessionId="s-1" />);

    expect(await screen.findByText('**不要加粗**')).toBeTruthy();
    expect(container.querySelector('strong')).toBeNull();
    expect(container.querySelector('h2')).toBeNull();
  });

  it('原始 HTML 不透传:以文本出现,DOM 里不存在 img 元素', async () => {
    fetchMock.mockResolvedValue(
      oneMessage('assistant', '请查收:<img src=x onerror=alert(1)>'),
    );

    const { container } = render(<SessionMessages sessionId="s-1" />);

    await waitFor(() =>
      expect(container.textContent).toContain('<img src=x onerror=alert(1)>'),
    );
    expect(container.querySelector('img')).toBeNull();
  });
});
