import React, { useState } from 'react';
import { login } from '../services/auth';
import { theme } from '../theme';

export default function LoginPage({
  onLogin,
}: {
  onLogin: (username: string) => void;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const name = await login(username, password);
      onLogin(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        background: theme.color.bg,
        fontFamily: theme.font.body,
      }}
    >
      <form
        onSubmit={(e) => {
          void submit(e);
        }}
        style={{
          width: 320,
          padding: 32,
          background: theme.color.surface,
          border: `1px solid ${theme.color.border}`,
          borderRadius: theme.radius.md,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <h1
          style={{
            margin: 0,
            fontSize: 18,
            fontWeight: 600,
            color: theme.color.text,
          }}
        >
          Multi-Agent 卖家工作台
        </h1>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="用户名"
          autoFocus
          style={{
            padding: '10px 12px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            fontSize: 14,
            outline: 'none',
          }}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="密码"
          style={{
            padding: '10px 12px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            fontSize: 14,
            outline: 'none',
          }}
        />
        {error && (
          <div style={{ color: theme.color.danger, fontSize: 13 }}>{error}</div>
        )}
        <button
          type="submit"
          disabled={loading}
          style={{
            padding: '10px 12px',
            border: 'none',
            borderRadius: theme.radius.sm,
            background: theme.color.brand,
            color: '#fff',
            fontSize: 14,
            fontWeight: 600,
            cursor: loading ? 'wait' : 'pointer',
          }}
        >
          {loading ? '登录中...' : '登录'}
        </button>
      </form>
    </div>
  );
}
