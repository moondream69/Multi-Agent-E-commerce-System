import { useState, useEffect, useCallback, useRef } from 'react';
import { io, Socket } from 'socket.io-client';
import { getToken } from '../services/auth';
import { handleAuthError } from '../services/api';
import {
  AgentEvent,
  AgentEventType,
  AgentStatus,
  AgentStatusChangedPayload,
  ChatResponse,
  NotificationMessage,
} from '../types/events';

export function useWebSocket() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastResponse, setLastResponse] = useState<ChatResponse | null>(null);
  const [statuses, setStatuses] = useState<Record<string, AgentStatus>>({});
  const [notifications, setNotifications] = useState<NotificationMessage[]>([]);
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    const socket = io({ auth: { token: getToken() } });
    socketRef.current = socket;

    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => {
      setConnected(false);
      // 断线后 statuses 可能过期,清空后由重连快照恢复权威值
      setStatuses({});
    });
    // 服务端拒绝连接(token 缺失/无效/过期)或网络失败
    socket.on('connect_error', () => {
      setConnected(false);
      handleAuthError();
    });
    socket.on('agent:event', (event: AgentEvent) => {
      setEvents((prev) => [event, ...prev].slice(0, 50));
      if (event.type === AgentEventType.AGENT_STATUS_CHANGED) {
        const p = event.payload as AgentStatusChangedPayload | undefined;
        if (p?.agentId && p.status) {
          setStatuses((prev) => ({ ...prev, [p.agentId]: p.status }));
        }
      }
    });
    socket.on('chat:response', (response: ChatResponse) => {
      setLastResponse(response);
    });
    socket.on('chat:notification', (message: NotificationMessage) => {
      setNotifications((prev) => [...prev, message]);
    });

    return () => {
      socket.disconnect();
    };
  }, []);

  const sendMessage = useCallback((text: string, sessionId?: string | null) => {
    setLastResponse(null);
    socketRef.current?.emit('chat:message', { text, sessionId });
  }, []);

  return {
    events,
    sendMessage,
    connected,
    lastResponse,
    statuses,
    notifications,
  };
}
