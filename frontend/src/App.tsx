import { useState } from 'react';
import { ApprovalCenter } from './components/ApprovalCenter';
import { theme } from './theme';

type View = 'cockpit' | 'drafting' | 'approvals';

const NAV_ITEMS: Array<{ key: View; label: string }> = [
  { key: 'cockpit', label: '驾驶舱' },
  { key: 'drafting', label: '起草工作台' },
  { key: 'approvals', label: '审批中心' },
];

function PlaceholderView({ title, note }: { title: string; note: string }) {
  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        background: theme.color.bg,
      }}
    >
      <div style={{ fontSize: 17, fontWeight: 600, color: theme.color.text }}>
        {title}
      </div>
      <div style={{ fontSize: 13, color: theme.color.textMuted }}>{note}</div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>('approvals');

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        fontFamily: theme.font.body,
        margin: 0,
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '0 16px',
          height: 52,
          borderBottom: `1px solid ${theme.color.border}`,
          background: theme.color.surface,
          flexShrink: 0,
        }}
      >
        <h1
          style={{
            margin: '0 16px 0 0',
            fontSize: 15,
            fontWeight: 600,
            letterSpacing: '0.01em',
            color: theme.color.text,
          }}
        >
          电商运营台
        </h1>
        <nav style={{ display: 'flex', gap: 4 }}>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              onClick={() => setView(item.key)}
              style={{
                padding: '6px 14px',
                border: 'none',
                borderRadius: theme.radius.sm,
                cursor: 'pointer',
                fontSize: 13,
                background:
                  view === item.key ? theme.color.brandSoft : 'transparent',
                color:
                  view === item.key
                    ? theme.color.brand
                    : theme.color.textSecondary,
              }}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 12,
            fontFamily: theme.font.mono,
            color: theme.color.textMuted,
          }}
        >
          局域网部署 · 内部工具
        </span>
      </header>

      {view === 'cockpit' && (
        <PlaceholderView
          title="驾驶舱"
          note="切片时间线与事件流实况墙随增量 5 落地"
        />
      )}
      {view === 'drafting' && (
        <PlaceholderView
          title="起草工作台"
          note="买家消息双栏起草随增量 5 落地"
        />
      )}
      {view === 'approvals' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <ApprovalCenter />
        </div>
      )}
    </div>
  );
}
