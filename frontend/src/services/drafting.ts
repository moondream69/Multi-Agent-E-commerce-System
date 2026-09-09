import { apiFetch } from './auth';
import { DraftingResponse } from '../types/events';

/** 起草工作台(spec #8 B11/B19):查证优先 + 多语草稿,不落库。 */
export async function draftReply(
  message: string,
  locale: string,
  orderId?: number,
): Promise<DraftingResponse> {
  const res = await apiFetch('/api/drafting', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, locale, order_id: orderId }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // 非 JSON 响应:保留 statusText
    }
    throw new Error(detail);
  }
  return (await res.json()) as DraftingResponse;
}
