import { ImportReport } from '../types/events';
import { apiFetch } from './auth';

/** CSV 批量导入(spec #8 B8):文本体上传(text/csv),行级报告。 */
export async function importCsv(
  kind: 'products' | 'orders',
  text: string,
): Promise<ImportReport> {
  const res = await apiFetch(`/api/import/${kind}`, {
    method: 'POST',
    headers: { 'Content-Type': 'text/csv' },
    body: text,
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
  const body = (await res.json()) as { report: ImportReport };
  return body.report;
}
