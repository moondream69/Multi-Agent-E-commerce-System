import { useEffect, useRef, useState } from 'react';
import { io, Socket } from 'socket.io-client';

/**
 * socket.io 订阅生命周期(连接/断开/清理),返回连接状态。
 *
 * events 须为模块级常量数组(稳定引用):内联数组会触发每次渲染重连。
 */
export function useSocket(
  events: string[],
  onEvent: (event: string, payload: Record<string, unknown>) => void,
): boolean {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const socket: Socket = io({ path: '/socket.io' });
    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));
    for (const event of events) {
      socket.on(event, (payload: Record<string, unknown>) => {
        handlerRef.current(event, payload);
      });
    }
    return () => {
      socket.disconnect();
    };
  }, [events]);

  return connected;
}
