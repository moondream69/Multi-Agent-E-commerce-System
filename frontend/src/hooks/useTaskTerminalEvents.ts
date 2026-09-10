import { useCallback } from 'react';
import { EventType } from '../types/events';
import { useSocket } from './useSocket';

// 模块级常量数组:useSocket 依赖引用稳定,避免每次渲染重连
const TASK_TERMINAL_EVENTS = [EventType.TASK_COMPLETED, EventType.TASK_FAILED];

/** 任务终态实时通道:任务完成/失败事件到达时触发刷新回调,返回连接状态。 */
export function useTaskTerminalEvents(onChange: () => void): boolean {
  const onEvent = useCallback(() => onChange(), [onChange]);
  return useSocket(TASK_TERMINAL_EVENTS, onEvent);
}
