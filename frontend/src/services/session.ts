// 当前会话 id 的本地状态(A2):登录新开、刷新恢复、新建即切。
// 独立成模块以避免 auth ↔ conversations 的循环依赖(登出需清除会话)。

const SESSION_KEY = 'mae_session_id';

export function newSessionId(): string {
  return crypto.randomUUID();
}

/** 当前会话 id(首次访问生成并落 localStorage:空白会话惰性落库,后端此时无行)。 */
export function getCurrentSessionId(): string {
  const stored = localStorage.getItem(SESSION_KEY);
  if (stored) return stored;
  const fresh = newSessionId();
  localStorage.setItem(SESSION_KEY, fresh);
  return fresh;
}

export function setCurrentSessionId(sessionId: string): void {
  localStorage.setItem(SESSION_KEY, sessionId);
}

/** 登录新开:登出清除当前会话,下次登录进入新会话。 */
export function clearCurrentSessionId(): void {
  localStorage.removeItem(SESSION_KEY);
}
