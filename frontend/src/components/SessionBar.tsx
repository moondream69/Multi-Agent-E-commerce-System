import { useState } from 'react';
import { ConversationMeta } from '../types/events';

// —— 会话切换条(A2 / 2026-09 重绘):新建/切换/删除/重命名;当前会话高亮 ——

interface Props {
  current: string;
  conversations: ConversationMeta[];
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
  onRename: (sessionId: string, title: string) => void;
  error: string | null;
}

function SessionRow({
  title,
  subtitle,
  active,
  onClick,
  onDelete,
  onRename,
}: {
  title: string;
  subtitle: string;
  active: boolean;
  onClick: () => void;
  onDelete?: () => void;
  onRename?: () => void;
}) {
  return (
    <div
      className={`group flex items-center gap-1 border-l-[3px] pr-1 transition-colors ${
        active
          ? 'border-brand bg-brand-soft'
          : 'border-transparent hover:bg-surface-2'
      }`}
    >
      <button
        onClick={onClick}
        className="flex min-w-0 flex-1 cursor-pointer flex-col gap-0.5 px-2.5 py-1.5 text-left"
      >
        <span
          className={`truncate text-xs ${active ? 'font-medium text-brand' : 'text-ink'}`}
        >
          {title}
        </span>
        <span className="text-[10px] text-ink-3">{subtitle}</span>
      </button>
      {onRename && (
        <button
          onClick={onRename}
          title="重命名会话"
          className="cursor-pointer rounded px-1 py-0.5 text-xs text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 hover:text-ink"
        >
          ✎
        </button>
      )}
      {onDelete && (
        <button
          onClick={onDelete}
          title="删除会话"
          className="cursor-pointer rounded px-1.5 py-0.5 text-xs text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 hover:text-st-failed"
        >
          ×
        </button>
      )}
    </div>
  );
}

export function SessionBar({
  current,
  conversations,
  onSelect,
  onNew,
  onDelete,
  onRename,
  error,
}: Props) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState('');
  const currentMeta = conversations.find((item) => item.sessionId === current);
  const others = conversations.filter((item) => item.sessionId !== current);

  const commitRename = () => {
    const title = draft.trim();
    if (!title) return;
    onRename(current, title);
    setRenaming(false);
  };

  return (
    <div className="flex flex-col gap-0.5 border-b border-line pb-1.5">
      <div className="flex items-center px-3.5 pt-3 pb-1.5">
        <span className="text-[13px] font-semibold">会话</span>
        <button
          onClick={onNew}
          className="ml-auto cursor-pointer rounded-lg border border-line bg-surface px-2.5 py-1 text-xs text-ink-2 transition-colors hover:border-brand/40 hover:bg-brand-soft hover:text-brand"
        >
          + 新建
        </button>
      </div>
      {/* 会话列表限高内滚:会话多时不挤占下方 Manager/导入/任务区(首屏走查:layout 溢出) */}
      <div className="flex max-h-[240px] flex-col gap-0.5 overflow-y-auto">
        {renaming ? (
          <div className="flex items-center gap-1 border-l-[3px] border-brand bg-brand-soft px-2 py-0.5">
            <input
              autoFocus
              value={draft}
              maxLength={50}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  commitRename();
                }
                if (event.key === 'Escape') setRenaming(false);
              }}
              className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-2 py-1 text-xs outline-none focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
            />
            <button
              onClick={commitRename}
              disabled={!draft.trim()}
              title="提交重命名"
              className={`px-2 py-0.5 text-xs ${
                draft.trim()
                  ? 'cursor-pointer text-brand'
                  : 'cursor-not-allowed text-ink-3'
              }`}
            >
              ✓
            </button>
          </div>
        ) : (
          <SessionRow
            title={currentMeta?.title || '新会话'}
            subtitle={
              currentMeta
                ? `${currentMeta.messageCount} 条消息`
                : '尚未发言(惰性落库)'
            }
            active
            onClick={() => onSelect(current)}
            onDelete={() => onDelete(current)}
            onRename={
              currentMeta
                ? () => {
                    setDraft(currentMeta.title || '');
                    setRenaming(true);
                  }
                : undefined
            }
          />
        )}
        {others.map((item) => (
          <SessionRow
            key={item.sessionId}
            title={item.title || '(无标题)'}
            subtitle={`${item.messageCount} 条消息`}
            active={false}
            onClick={() => onSelect(item.sessionId)}
            onDelete={() => onDelete(item.sessionId)}
          />
        ))}
      </div>
      {error && (
        <div className="px-3.5 pt-1 text-[11px] text-st-failed">{error}</div>
      )}
    </div>
  );
}
