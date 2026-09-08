import { useEffect, useRef, useState } from 'react';
import { io, Socket } from 'socket.io-client';

/** 审批实时通道:审批/任务事件到达时触发刷新回调,返回连接状态。 */
export function useApprovalEvents(onChange: () => void): boolean {
  const [connected, setConnected] = useState(false);
  const callbackRef = useRef(onChange);
  callbackRef.current = onChange;

  useEffect(() => {
    const socket: Socket = io({ path: '/socket.io' });
    const refresh = () => callbackRef.current();
    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));
    socket.on('approval.requested', refresh);
    socket.on('approval.decided', refresh);
    socket.on('task.completed', refresh);
    socket.on('task.failed', refresh);
    return () => {
      socket.disconnect();
    };
  }, []);

  return connected;
}
