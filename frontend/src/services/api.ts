import { AgentInfo } from '../types/events';

const BASE = '/api';

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await fetch(`${BASE}/dashboard/agents`);
  return (await res.json()) as AgentInfo[];
}

export interface DashboardStatus {
  totalAgents: number;
  onlineAgents: number;
  timestamp: string;
}

export async function fetchStatus(): Promise<DashboardStatus> {
  const res = await fetch(`${BASE}/dashboard/status`);
  return (await res.json()) as DashboardStatus;
}

export async function createTask(
  type: string,
  input: Record<string, unknown>,
  targetAgentId?: string,
): Promise<unknown> {
  const res = await fetch(`${BASE}/agents/task`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, input, targetAgentId }),
  });
  return (await res.json()) as unknown;
}
