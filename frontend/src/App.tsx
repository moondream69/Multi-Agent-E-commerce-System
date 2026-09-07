import React, { useCallback, useEffect, useRef, useState } from 'react';
import ApprovalPanel from './components/ApprovalPanel';
import { ChatMessage, ChatPanel } from './components/ChatPanel';
import { Dashboard } from './components/Dashboard';
import LoginPage from './components/LoginPage';
import { useNotificationBells } from './hooks/useNotificationBells';
import { useWebSocket } from './hooks/useWebSocket';
import { clearToken, fetchMe, getToken } from './services/auth';
import {
  deleteSession as apiDeleteSession,
  fetchAgents,
  fetchApprovals,
  fetchConversations,
  fetchSessionMessages,
  setUnauthorizedHandler,
} from './services/api';
import { AgentEventType, AgentInfo, ConversationMeta } from './types/events';
import { theme } from './theme';
import { contentToDisplay, outputToContent } from './utils/output';

type View = 'cockpit' | 'support' | 'approvals';

// 当前会话的 localStorage 键(登录新开、刷新恢复,见 ADR 0004)
const SESSION_KEY = 'mae_session';

const NAV_ITEMS: Array<{ key: View; label: string }> = [
  { key: 'cockpit', label: '驾驶舱' },
  { key: 'support', label: '客服工作台' },
  { key: 'approvals', label: '审批中心' },
];

export default function App() {
  const [user, setUser] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  // 刷新页面后凭 token 恢复登录态
  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    fetchMe()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setChecking(false));
  }, []);

  if (checking) return null;
  if (!user) return <LoginPage onLogin={setUser} />;
  return (
    <MainApp
      username={user}
      onLogout={() => {
        clearToken();
        localStorage.removeItem(SESSION_KEY); // 登录新开:登出清当前会话,下次登录进新会话
        setUser(null);
      }}
    />
  );
}

function MainApp({
  username,
  onLogout,
}: {
  username: string;
  onLogout: () => void;
}) {
  const {
    events,
    sendMessage,
    connected,
    lastResponse,
    statuses,
    notifications,
  } = useWebSocket();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [pendingApprovals, setPendingApprovals] = useState(0);
  const [view, setView] = useState<View>('cockpit');
  const [sessions, setSessions] = useState<ConversationMeta[]>([]);
  // 当前会话:null = 未落库的空白新会话(惰性落库,首条消息才建行)
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(() =>
    localStorage.getItem(SESSION_KEY),
  );
  // 同步 ref:handleSend 等异步回调读取最新值,避免切换后立即发送写入旧会话
  const currentSessionIdRef = useRef(currentSessionId);
  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  // —— 聊天消息流(App 级持有:视图切换/重挂载不丢;刷新后按会话恢复)——
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const historyLoadedRef = useRef(false);
  const consumedResponseRef = useRef<string | null>(null);
  const pendingReplyRef = useRef(false);
  const bells = useNotificationBells(notifications);

  const refreshSessions = useCallback(
    () => fetchConversations().then(setSessions).catch(console.error),
    [],
  );

  const newSession = useCallback(() => {
    setCurrentSessionId(null);
    localStorage.removeItem(SESSION_KEY);
    setChatMessages([]);
  }, []);

  // 会话历史加载:竞态序号(快速切换只让最后一次生效);404 可回落空白新会话
  const sessionLoadSeqRef = useRef(0);
  const loadSessionMessages = useCallback(
    (sessionId: string, fallbackToNew = false) => {
      const seq = ++sessionLoadSeqRef.current;
      fetchSessionMessages(sessionId)
        .then((data) => {
          if (seq !== sessionLoadSeqRef.current) return;
          setChatMessages(
            data.messages.map((m) => ({
              role: m.role as ChatMessage['role'],
              content:
                m.role === 'assistant'
                  ? contentToDisplay(m.content)
                  : m.content,
              ts: m.timestamp,
            })),
          );
        })
        .catch((error) => {
          if (seq !== sessionLoadSeqRef.current) return;
          console.error(error);
          if (fallbackToNew) newSession();
        });
    },
    [newSession],
  );

  // 首次挂载:拉会话列表;当前会话存在则恢复其历史,不存在(陈旧 id)回落新会话
  useEffect(() => {
    if (historyLoadedRef.current) return;
    historyLoadedRef.current = true;
    void refreshSessions();
    if (currentSessionId) {
      loadSessionMessages(currentSessionId, true);
    }
  }, [currentSessionId, refreshSessions, loadSessionMessages]);

  const switchSession = useCallback(
    (sessionId: string) => {
      setCurrentSessionId(sessionId);
      localStorage.setItem(SESSION_KEY, sessionId);
      loadSessionMessages(sessionId);
    },
    [loadSessionMessages],
  );

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      const meta = sessions.find((s) => s.sessionId === sessionId);
      if (
        !window.confirm(
          `删除会话「${meta?.title || '未命名会话'}」?此操作不可恢复。`,
        )
      ) {
        return;
      }
      try {
        await apiDeleteSession(sessionId);
        await refreshSessions();
      } catch (error) {
        console.error(error);
        return;
      }
      if (sessionId === currentSessionId) newSession();
    },
    [sessions, currentSessionId, refreshSessions, newSession],
  );

  // WS 任务响应:按 taskId 去重消费(StrictMode double-effect / 视图切换重挂载均不重复)
  useEffect(() => {
    if (!lastResponse || lastResponse.type === 'task_created') return;
    const key = `${lastResponse.taskId}:${lastResponse.type}`;
    if (consumedResponseRef.current === key) return;
    consumedResponseRef.current = key;

    if (lastResponse.type === 'task_result') {
      const entry: ChatMessage = {
        role: 'assistant',
        content: outputToContent(lastResponse.output),
        ts: new Date().toISOString(),
        steps: Array.isArray(lastResponse.steps)
          ? (lastResponse.steps as ChatMessage['steps'])
          : undefined,
      };
      setChatMessages((prev) => {
        if (pendingReplyRef.current && prev[prev.length - 1]?.placeholder) {
          return [...prev.slice(0, -1), entry]; // 替换"正在思考..."占位
        }
        return [...prev, entry];
      });
      pendingReplyRef.current = false;
      void refreshSessions(); // 首条消息落库后,新会话进入列表
    } else if (lastResponse.type === 'task_error') {
      setChatMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `错误: ${lastResponse.error}`,
          ts: new Date().toISOString(),
        },
      ]);
      pendingReplyRef.current = false;
      void refreshSessions(); // 首条消息已落库,新会话也应进入列表
    }
  }, [lastResponse, refreshSessions]);

  const handleSend = useCallback(
    (text: string) => {
      // 空白新会话:首条消息前生成会话 id(后端按 (用户, sessionId) 惰性建行)
      let sessionId = currentSessionIdRef.current;
      if (!sessionId) {
        sessionId = crypto.randomUUID();
        setCurrentSessionId(sessionId);
        localStorage.setItem(SESSION_KEY, sessionId);
      }
      sendMessage(text, sessionId);
      setChatMessages((prev) => [
        ...prev,
        { role: 'user', content: text, ts: new Date().toISOString() },
      ]);
      pendingReplyRef.current = true;
      setTimeout(() => {
        setChatMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: 'Agent 正在思考...',
            ts: new Date().toISOString(),
            placeholder: true,
          },
        ]);
      }, 300);
    },
    [sendMessage],
  );

  // 待审批数:进入审批中心/审批事件到达时刷新
  const refreshPendingApprovals = useCallback(() => {
    fetchApprovals('pending')
      .then((rows) => setPendingApprovals(rows.length))
      .catch(console.error);
  }, []);

  // socket 首连/重连时拉取一次状态快照,补齐断线期间丢失的状态变化(断线时 statuses 已清空)
  const wasConnected = useRef(false);
  useEffect(() => {
    if (connected && !wasConnected.current) {
      fetchAgents().then(setAgents).catch(console.error);
    }
    wasConnected.current = connected;
  }, [connected]);

  // 审批事件驱动前台刷新徽标
  useEffect(() => {
    const last = events[0];
    if (!last) return;
    if (
      last.type === AgentEventType.APPROVAL_REQUESTED ||
      last.type === AgentEventType.APPROVAL_DECIDED
    ) {
      refreshPendingApprovals();
    }
  }, [events, refreshPendingApprovals]);

  const liveAgents = agents.map((a) => ({
    ...a,
    status: statuses[a.id] ?? a.status,
  }));

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
            fontSize: 16,
            fontWeight: 600,
            color: theme.color.text,
          }}
        >
          Multi-Agent 跨境电商系统
        </h1>
        <nav style={{ display: 'flex', gap: 4 }}>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              onClick={() => {
                setView(item.key);
                if (item.key === 'approvals') refreshPendingApprovals();
              }}
              style={{
                padding: '6px 14px',
                border: 'none',
                borderRadius: theme.radius.sm,
                cursor: 'pointer',
                fontSize: 13,
                background:
                  view === item.key ? theme.color.brand : 'transparent',
                color: view === item.key ? '#fff' : theme.color.textSecondary,
                position: 'relative',
              }}
            >
              {item.label}
              {item.key === 'approvals' && pendingApprovals > 0 && (
                <span
                  style={{
                    position: 'absolute',
                    top: -6,
                    right: -6,
                    minWidth: 16,
                    height: 16,
                    borderRadius: theme.radius.full,
                    background: theme.color.danger,
                    color: '#fff',
                    fontSize: 10,
                    lineHeight: '16px',
                    textAlign: 'center',
                    padding: '0 4px',
                  }}
                >
                  {pendingApprovals}
                </span>
              )}
            </button>
          ))}
        </nav>
        <div
          style={{
            marginLeft: 'auto',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <span
            style={{
              color: connected ? theme.color.success : theme.color.danger,
              fontSize: 13,
              fontFamily: theme.font.mono,
            }}
          >
            {connected ? '已连接' : '未连接'}
          </span>
          <span style={{ fontSize: 13, color: theme.color.textSecondary }}>
            {username}
          </span>
          <button
            onClick={onLogout}
            style={{
              padding: '4px 10px',
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              background: 'transparent',
              color: theme.color.textSecondary,
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            退出
          </button>
        </div>
      </header>

      {view === 'cockpit' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <div
            style={{
              flex: 1,
              padding: 24,
              overflow: 'auto',
              background: theme.color.bg,
            }}
          >
            <Dashboard agents={liveAgents} events={events} bells={bells} />
          </div>
          <div
            style={{
              width: 420,
              borderLeft: '1px solid #e0e0e0',
              background: '#fff',
            }}
          >
            <ChatPanel
              messages={chatMessages}
              onSend={handleSend}
              sessions={sessions}
              currentSessionId={currentSessionId}
              onNewSession={newSession}
              onSwitchSession={switchSession}
              onDeleteSession={(sessionId) =>
                void handleDeleteSession(sessionId)
              }
            />
          </div>
        </div>
      )}
      {view === 'support' && (
        <div style={{ flex: 1, minHeight: 0 }}>
          <ChatPanel
            messages={chatMessages}
            onSend={handleSend}
            title="客服工作台"
            placeholder="输入问题，如：查一下退货政策"
            sessions={sessions}
            currentSessionId={currentSessionId}
            onNewSession={newSession}
            onSwitchSession={switchSession}
            onDeleteSession={(sessionId) => void handleDeleteSession(sessionId)}
          />
        </div>
      )}
      {view === 'approvals' && (
        <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
          <ApprovalPanel />
        </div>
      )}
    </div>
  );
}
