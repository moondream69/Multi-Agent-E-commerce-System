import { useState } from 'react';
import { ConversationMeta } from '../types/events';
import { theme } from '../theme';

// —— 会话切换条(A2):新建/切换/删除/重命名;当前会话高亮,空白会话显示为「新会话」——

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
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        borderLeft: `3px solid ${active ? theme.color.brand : 'transparent'}`,
        background: active ? theme.color.brandSoft : 'transparent',
      }}
    >
      <button
        onClick={onClick}
        style={{
          flex: 1,
          minWidth: 0,
          padding: '6px 9px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          textAlign: 'left',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
        }}
      >
        <span
          style={{
            fontSize: 12,
            color: active ? theme.color.brand : theme.color.text,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </span>
        <span style={{ fontSize: 10, color: theme.color.textMuted }}>
          {subtitle}
        </span>
      </button>
      {onRename && (
        <button
          onClick={onRename}
          title="重命名会话"
          style={{
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            fontSize: 12,
            color: theme.color.textMuted,
            padding: '2px 4px',
          }}
        >
          ✎
        </button>
      )}
      {onDelete && (
        <button
          onClick={onDelete}
          title="删除会话"
          style={{
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            fontSize: 12,
            color: theme.color.textMuted,
            padding: '2px 8px',
          }}
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
    <div
      style={{
        padding: '10px 0 6px',
        borderBottom: `1px solid ${theme.color.border}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '0 14px 6px',
        }}
      >
        <span
          style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
        >
          会话
        </span>
        <button
          onClick={onNew}
          style={{
            marginLeft: 'auto',
            padding: '3px 10px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            background: theme.color.surface,
            cursor: 'pointer',
            fontSize: 12,
            color: theme.color.textSecondary,
          }}
        >
          + 新建
        </button>
      </div>
      {renaming ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 8px 2px 3px',
            borderLeft: `3px solid ${theme.color.brand}`,
            background: theme.color.brandSoft,
          }}
        >
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
            style={{
              flex: 1,
              minWidth: 0,
              padding: '5px 8px',
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              fontSize: 12,
            }}
          />
          <button
            onClick={commitRename}
            disabled={!draft.trim()}
            title="提交重命名"
            style={{
              border: 'none',
              background: 'transparent',
              cursor: draft.trim() ? 'pointer' : 'not-allowed',
              fontSize: 12,
              color: draft.trim() ? theme.color.brand : theme.color.textMuted,
              padding: '2px 8px',
            }}
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
      {error && (
        <div
          style={{
            padding: '4px 14px 0',
            fontSize: 11,
            color: theme.color.danger,
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
