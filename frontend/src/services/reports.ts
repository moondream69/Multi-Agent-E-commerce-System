import { ReportSummary } from '../types/events';
import { apiFetch } from './auth';

/** 经营快照(spec #11):订单分布/近 7 日成交额/低库存/未结工单,零 LLM。 */
export async function fetchReportSummary(): Promise<ReportSummary> {
  const res = await apiFetch('/api/reports/summary');
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as ReportSummary;
}
