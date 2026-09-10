import type { ComponentProps } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { SessionBar } from './SessionBar';
import { ConversationMeta } from '../types/events';

const CONVERSATIONS: ConversationMeta[] = [
  {
    sessionId: 's-1',
    title: '第一个会话',
    updatedAt: '2026-09-10T10:00:00Z',
    messageCount: 3,
  },
  {
    sessionId: 's-2',
    title: '第二个会话',
    updatedAt: '2026-09-09T10:00:00Z',
    messageCount: 1,
  },
];

function renderBar(
  overrides: Partial<ComponentProps<typeof SessionBar>> = {},
): ComponentProps<typeof SessionBar> {
  const props: ComponentProps<typeof SessionBar> = {
    current: 's-1',
    conversations: CONVERSATIONS,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    onDelete: vi.fn(),
    onRename: vi.fn(),
    error: null,
    ...overrides,
  };
  render(<SessionBar {...props} />);
  return props;
}

describe('SessionBar 重命名(spec #11 A2 扩展)', () => {
  it('当前会话内联编辑:Enter 提交调用 onRename(trim)', () => {
    const props = renderBar();
    fireEvent.click(screen.getByTitle('重命名会话'));
    const input = screen.getByDisplayValue('第一个会话');
    fireEvent.change(input, { target: { value: '  新名字  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(props.onRename).toHaveBeenCalledWith('s-1', '新名字');
  });

  it('Esc 取消:不调用 onRename,恢复展示原标题', () => {
    const props = renderBar();
    fireEvent.click(screen.getByTitle('重命名会话'));
    fireEvent.change(screen.getByDisplayValue('第一个会话'), {
      target: { value: '改动中' },
    });
    fireEvent.keyDown(screen.getByDisplayValue('改动中'), { key: 'Escape' });
    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.getByText('第一个会话')).toBeTruthy();
  });

  it('空值(trim 后)禁用提交', () => {
    renderBar();
    fireEvent.click(screen.getByTitle('重命名会话'));
    fireEvent.change(screen.getByDisplayValue('第一个会话'), {
      target: { value: '   ' },
    });
    const submit = screen.getByTitle('提交重命名');
    expect(submit.hasAttribute('disabled')).toBe(true);
  });

  it('未落库的空白会话没有重命名入口', () => {
    renderBar({ current: 's-new' });
    expect(screen.getByText('新会话')).toBeTruthy();
    expect(screen.queryByTitle('重命名会话')).toBeNull();
  });
});
