import { useState } from 'react';
import { ApprovalCenter } from './components/ApprovalCenter';
import { Cockpit } from './components/Cockpit';
import { DraftingWorkbench } from './components/DraftingWorkbench';
import { NotificationBell } from './components/NotificationBell';
import { useTheme } from './hooks/useTheme';
import {
  clearSession,
  getToken,
  getUsername,
  login,
  storeSession,
} from './services/auth';
import { clearCurrentSessionId } from './services/session';

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
    <div className="flex h-screen items-center justify-center bg-bg">
      <div className="flex w-[340px] flex-col gap-3 rounded-card border border-line bg-surface p-7 shadow-pop">
        <h1 className="m-0 text-base font-semibold">电商运营台 · 登录</h1>
        <input
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit();
          }}
          placeholder="用户名"
          autoFocus
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
        />
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit();
          }}
          placeholder="密码"
          className="rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
        />
        {error && <div className="text-xs text-st-failed">{error}</div>}
        <button
          onClick={() => void submit()}
          disabled={busy || !username || !password}
          className="cursor-pointer rounded-lg bg-brand py-2 text-sm font-medium text-brand-contrast transition-[filter] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? '登录中…' : '登录'}
        </button>
        <div className="text-[11px] text-ink-3">局域网部署 · 内部工具</div>
      </div>
    </div>
  );
}

function ThemeToggle() {
  const { mode, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      title={mode === 'dark' ? '切换到浅色' : '切换到深色'}
      aria-label="切换主题"
      className="cursor-pointer rounded-lg border border-line px-2 py-1 text-xs text-ink-2 transition-colors hover:bg-surface-2"
    >
      {mode === 'dark' ? '☾' : '☀'}
    </button>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const [view, setView] = useState<View>('approvals');

  return (
    <div className="flex h-screen flex-col bg-bg">
      <header className="flex h-13 shrink-0 items-center gap-1 border-b border-line bg-surface px-4">
        <h1 className="mr-4 text-[15px] font-semibold tracking-wide">
          电商运营台
        </h1>
        <nav className="flex gap-1">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              onClick={() => setView(item.key)}
              className={`cursor-pointer rounded-lg px-3.5 py-1.5 text-[13px] transition-colors ${
                view === item.key
                  ? 'bg-brand-soft font-medium text-brand'
                  : 'text-ink-2 hover:bg-surface-2'
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <span className="ml-auto font-mono text-xs text-ink-3">
          {getUsername()}
        </span>
        <ThemeToggle />
        <NotificationBell />
        <button
          onClick={onLogout}
          className="cursor-pointer rounded-lg border border-line px-2.5 py-1 text-xs text-ink-2 transition-colors hover:bg-surface-2"
        >
          退出
        </button>
      </header>

      {view === 'cockpit' && (
        <div className="flex min-h-0 flex-1">
          <Cockpit onOpenApprovals={() => setView('approvals')} />
        </div>
      )}
      {view === 'drafting' && (
        <div className="flex min-h-0 flex-1">
          <DraftingWorkbench />
        </div>
      )}
      {view === 'approvals' && (
        <div className="flex min-h-0 flex-1">
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
