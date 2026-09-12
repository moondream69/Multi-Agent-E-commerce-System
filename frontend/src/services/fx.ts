import { FxCardPayload } from '../types/events';
import { apiFetch } from './auth';

const BASE = '/api';

/** 汇率卡片(ADR-0006 / spec #34):当期汇率(基准 CNY)+ 缓存时刻 + 近 7 日成交快照走势。

汇率不可用时后端如实回 rate=null(前端显「待核」),本函数不因此抛错——
卡片其余部分(走势/币种)仍然可读。
*/
export async function fetchFxCard(): Promise<FxCardPayload> {
  const res = await apiFetch(`${BASE}/fx`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as FxCardPayload;
}
