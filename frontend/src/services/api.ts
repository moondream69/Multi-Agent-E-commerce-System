import { AgentInfo } from '../types/events';

const BASE = '/api';

export async function fetchAgents(): Promise<AgentInfo[]> {
  const res = await fetch(`${BASE}/dashboard/agents`);
  return (await res.json()) as AgentInfo[];
}
