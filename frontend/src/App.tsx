import { useState } from 'react';
import { ApprovalCenter } from './components/ApprovalCenter';
import { Cockpit } from './components/Cockpit';
import { DraftingWorkbench } from './components/DraftingWorkbench';
import { NotificationBell } from './components/NotificationBell';
import {
  clearSession,
  getToken,
  getUsername,
  login,
  storeSession,
} from './services/auth';
import { clearCurrentSessionId } from './services/session';
import { theme } from './theme';

type View = 'cockpit' | 'drafting' | 'approvals';

const NAV_ITEMS: Array<{ key: View; label: string }> = [
  { key: 'cockpit', label: '驾驶舱' },
  { key: 'drafting', label: '起草工作台' },
  { key: 'approvals', label: '审批中心' },
];

function LoginView({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await login(username, password);
      storeSession(session.token, session.username);
      onLoggedIn();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: theme.color.bg,
      }}
    >
      <div
        style={{
          width: 320,
          padding: '28px 24px',
          background: theme.color.surface,
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.md,
          boxShadow: theme.shadow.card,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <h1
          style={{
            margin: 0,
            fontSize: 16,
            fontWeight: 600,
            color: theme.color.text,
          }}
        >
          电商运营台 · 登录
        </h1>
        <input
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit();
          }}
          placeholder="用户名"
          autoFocus
          style={{
            padding: '8px 12px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            fontSize: 14,
          }}
        />
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit();
          }}
          placeholder="密码"
          style={{
            padding: '8px 12px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            fontSize: 14,
          }}
        />
        {error && (
          <div style={{ fontSize: 12, color: theme.color.danger }}>{error}</div>
        )}
        <button
          onClick={() => void submit()}
          disabled={busy || !username || !password}
          style={{
            padding: '8px 0',
            border: 'none',
            borderRadius: theme.radius.sm,
            background: theme.color.brand,
            color: '#fff',
            cursor: busy ? 'not-allowed' : 'pointer',
            fontSize: 14,
            opacity: busy || !username || !password ? 0.6 : 1,
          }}
        >
          {busy ? '登录中…' : '登录'}
        </button>
        <div style={{ fontSize: 11, color: theme.color.textMuted }}>
          局域网部署 · 内部工具
        </div>
      </div>
    </div>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
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
          {getUsername()}
        </span>
        <NotificationBell />
        <button
          onClick={onLogout}
          style={{
            padding: '4px 10px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            background: 'transparent',
            cursor: 'pointer',
            fontSize: 12,
            color: theme.color.textSecondary,
          }}
        >
          退出
        </button>
      </header>

      {view === 'cockpit' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <Cockpit onOpenApprovals={() => setView('approvals')} />
        </div>
      )}
      {view === 'drafting' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <DraftingWorkbench />
        </div>
      )}
      {view === 'approvals' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <ApprovalCenter />
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState<string | null>(getToken());

  if (!token) {
    return <LoginView onLoggedIn={() => setToken(getToken())} />;
  }
  return (
    <Shell
      onLogout={() => {
        clearSession();
        clearCurrentSessionId(); // 登录新开:登出即放弃当前会话(A2)
        setToken(null);
      }}
    />
  );
}
