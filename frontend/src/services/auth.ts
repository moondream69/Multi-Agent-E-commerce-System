const TOKEN_KEY = 'mae_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export async function login(
  username: string,
  password: string,
): Promise<string> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error('用户名或密码错误');
  const data = (await res.json()) as { token: string; username: string };
  setToken(data.token);
  return data.username;
}

export async function fetchMe(): Promise<string> {
  const res = await fetch('/api/auth/me', {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new Error('未认证');
  const data = (await res.json()) as { username: string };
  return data.username;
}
