import React, { useState, useEffect, useRef } from 'react';
import { Dashboard } from './components/Dashboard';
import { ChatPanel } from './components/ChatPanel';
import { useWebSocket } from './hooks/useWebSocket';
import { fetchAgents } from './services/api';
import { AgentInfo } from './types/events';

export default function App() {
  const { events, sendMessage, connected, lastResponse, statuses } =
    useWebSocket();
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  // socket 首连/重连时拉取一次状态快照,补齐断线期间丢失的状态变化(断线时 statuses 已清空)
  const wasConnected = useRef(false);
  useEffect(() => {
    if (connected && !wasConnected.current) {
      fetchAgents().then(setAgents).catch(console.error);
    }
    wasConnected.current = connected;
  }, [connected]);

  const liveAgents = agents.map((a) => ({
    ...a,
    status: statuses[a.id] ?? a.status,
  }));

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        fontFamily: 'system-ui, sans-serif',
        margin: 0,
      }}
    >
      <div
        style={{
          flex: 1,
          padding: 24,
          overflow: 'auto',
          background: '#f5f5f5',
        }}
      >
        <h1 style={{ margin: '0 0 16px 0', fontSize: 22 }}>
          Multi-Agent 跨境电商系统
        </h1>
        <div
          style={{
            marginBottom: 12,
            color: connected ? '#22c55e' : '#ef4444',
            fontSize: 13,
          }}
        >
          {connected ? '已连接' : '未连接'}
        </div>
        <Dashboard agents={liveAgents} events={events} />
      </div>
      <div
        style={{
          width: 420,
          borderLeft: '1px solid #e0e0e0',
          background: '#fff',
        }}
      >
        <ChatPanel onSend={sendMessage} lastResponse={lastResponse} />
      </div>
    </div>
  );
}
