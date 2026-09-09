import { useCallback } from 'react';
import { EventType } from '../types/events';
import { useSocket } from './useSocket';

// 模块级常量数组:useSocket 依赖引用稳定,避免每次渲染重连
const APPROVAL_EVENTS = [
  EventType.APPROVAL_REQUESTED,
  EventType.APPROVAL_DECIDED,
  EventType.TASK_COMPLETED,
  EventType.TASK_FAILED,
];

/** 审批实时通道:审批/任务事件到达时触发刷新回调,返回连接状态。 */
export function useApprovalEvents(onChange: () => void): boolean {
  const onEvent = useCallback(() => onChange(), [onChange]);
  return useSocket(APPROVAL_EVENTS, onEvent);
}
