import { useCallback, useState } from 'react';
import { EventType } from '../types/events';
import { useSocket } from './useSocket';

/** 协作管线(签名视图):把事件流归约成 Manager + 三业务 Agent 的实时状态。 */

export type NodeState =
  'idle' | 'planning' | 'running' | 'waiting' | 'done' | 'failed';

export type SliceChipStatus =
  'queued' | 'running' | 'waiting' | 'done' | 'failed' | 'rejected';

export interface SliceChip {
  no: number;
  agent: string;
  description: string;
  status: SliceChipStatus;
}

export interface PipelineState {
  /** 追踪的任务;null = 尚无任务(待命) */
  threadId: string | null;
  manager: NodeState;
  agents: Record<string, NodeState>;
  slices: SliceChip[];
  /** 最近一次导致状态变化的事件(供动画编排判定「刚发生了什么」) */
  pulse: { event: string; sliceNo: number | null; at: number } | null;
}

export const AGENT_ORDER = [
  'product_research',
  'order_management',
  'customer_service',
] as const;

export function initialPipeline(): PipelineState {
  return {
    threadId: null,
    manager: 'idle',
    agents: Object.fromEntries(AGENT_ORDER.map((a) => [a, 'idle'])),
    slices: [],
    pulse: null,
  };
}

/** 由切片集合重算 Agent 节点态:执行中 > 失败 > 等待 > 完成 > 待命(同 Agent 多切片取最「活跃」态)。 */
function agentStateOf(slices: SliceChip[], agent: string): NodeState {
  const own = slices.filter((s) => s.agent === agent);
  if (own.some((s) => s.status === 'running')) return 'running';
  if (own.some((s) => s.status === 'failed')) return 'failed';
  if (own.some((s) => s.status === 'waiting')) return 'waiting';
  if (own.some((s) => s.status === 'done' || s.status === 'rejected'))
    return 'done';
  if (own.length > 0) return 'running'; // 已分派未开始(同层并行尚未全部 started)
  return 'idle';
}

function withPulse(
  state: PipelineState,
  event: string,
  sliceNo: number | null = null,
): PipelineState {
  return { ...state, pulse: { event, sliceNo, at: Date.now() } };
}

export function reducePipeline(
  state: PipelineState,
  event: string,
  payload: Record<string, unknown>,
): PipelineState {
  const threadId = (payload.threadId as string | undefined) ?? null;
  switch (event) {
    case EventType.TASK_CREATED:
      // 新任务接管面板(同时并发的多任务不在本视图范围)
      return withPulse(
        { ...initialPipeline(), threadId, manager: 'planning' },
        event,
      );
    case EventType.TASK_PLANNED: {
      if (threadId !== state.threadId) return state;
      const raw =
        (payload.slices as Array<Record<string, unknown>> | undefined) ?? [];
      const slices: SliceChip[] = raw.map((s) => ({
        no: s.no as number,
        agent: s.agent as string,
        description: (s.description as string) ?? '',
        status: 'queued',
      }));
      const agents = { ...state.agents };
      for (const chip of slices)
        agents[chip.agent] = agentStateOf(slices, chip.agent);
      return withPulse({ ...state, manager: 'running', slices, agents }, event);
    }
    case EventType.SLICE_STARTED: {
      if (threadId !== state.threadId) return state;
      const sliceNo = payload.sliceNo as number;
      const agent = payload.agent as string;
      const slices = state.slices.map((s) =>
        s.no === sliceNo ? { ...s, status: 'running' as const } : s,
      );
      // 计划外切片(重规划回流新增)允许补入
      if (!slices.some((s) => s.no === sliceNo)) {
        slices.push({ no: sliceNo, agent, description: '', status: 'running' });
      }
      const agents = { ...state.agents, [agent]: agentStateOf(slices, agent) };
      return withPulse({ ...state, slices, agents }, event, sliceNo);
    }
    case EventType.SLICE_COMPLETED: {
      if (threadId !== state.threadId) return state;
      const sliceNo = payload.sliceNo as number;
      const agent = payload.agent as string;
      const status = payload.status as string;
      const chipStatus: SliceChipStatus =
        status === 'failed'
          ? 'failed'
          : status === 'rejected'
            ? 'rejected'
            : 'done';
      const slices = state.slices.map((s) =>
        s.no === sliceNo ? { ...s, status: chipStatus } : s,
      );
      const agents = { ...state.agents, [agent]: agentStateOf(slices, agent) };
      return withPulse({ ...state, slices, agents }, event, sliceNo);
    }
    case EventType.APPROVAL_REQUESTED: {
      if (threadId !== state.threadId) return state;
      const sliceNo = payload.sliceNo as number;
      const agent = payload.agent as string;
      const slices = state.slices.map((s) =>
        s.no === sliceNo ? { ...s, status: 'waiting' as const } : s,
      );
      const agents = { ...state.agents, [agent]: agentStateOf(slices, agent) };
      // Manager 视角:任务挂起等待人工 → 等待态
      return withPulse(
        { ...state, manager: 'waiting', slices, agents },
        event,
        sliceNo,
      );
    }
    case EventType.TASK_INTERRUPTED:
      if (threadId !== state.threadId) return state;
      return withPulse({ ...state, manager: 'waiting' }, event);
    case EventType.TASK_COMPLETED: {
      if (threadId !== state.threadId) return state;
      const slices = state.slices.map((s) =>
        s.status === 'running' ||
        s.status === 'waiting' ||
        s.status === 'queued'
          ? { ...s, status: 'done' as const }
          : s,
      );
      const agents = { ...state.agents };
      for (const agent of AGENT_ORDER)
        agents[agent] = agentStateOf(slices, agent);
      return withPulse({ ...state, manager: 'done', slices, agents }, event);
    }
    case EventType.TASK_FAILED: {
      if (threadId !== state.threadId) return state;
      const slices = state.slices.map((s) =>
        s.status === 'running' ||
        s.status === 'waiting' ||
        s.status === 'queued'
          ? { ...s, status: 'failed' as const }
          : s,
      );
      const agents = { ...state.agents };
      for (const agent of AGENT_ORDER)
        agents[agent] = agentStateOf(slices, agent);
      return withPulse({ ...state, manager: 'failed', slices, agents }, event);
    }
    default:
      return state;
  }
}

// 模块级常量:useSocket 依赖引用稳定,避免每次渲染重连
const PIPELINE_EVENTS = [
  EventType.TASK_CREATED,
  EventType.TASK_PLANNED,
  EventType.TASK_INTERRUPTED,
  EventType.TASK_COMPLETED,
  EventType.TASK_FAILED,
  EventType.SLICE_STARTED,
  EventType.SLICE_COMPLETED,
  EventType.APPROVAL_REQUESTED,
];

/** 协作管线实时状态:订阅任务/切片事件,恒为最新任务的归约结果。 */
export function useCollabPipeline(): PipelineState {
  const [state, setState] = useState<PipelineState>(initialPipeline);
  const onEvent = useCallback(
    (event: string, payload: Record<string, unknown>) => {
      setState((current) => reducePipeline(current, event, payload));
    },
    [],
  );
  useSocket(PIPELINE_EVENTS, onEvent);
  return state;
}
