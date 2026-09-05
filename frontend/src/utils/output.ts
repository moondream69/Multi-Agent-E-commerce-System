/**
 * Agent 输出 → 展示文本的提取工具。
 *
 * outputToContent 与后端 core/output_text.py 的 extract_output_text 逐项镜像,
 * 字段优先级必须保持一致,否则前后端渲染结果会漂移。
 */

const KNOWN_KEYS = ['report', 'result', 'reply', 'alert', 'message'];

// 实时路径:Agent 输出对象 → 可读文本(与后端 extract_output_text 同优先级)
export function outputToContent(output: Record<string, unknown>): string {
  if (typeof output.report === 'string' && output.report) {
    return output.report;
  } else if (typeof output.result === 'string' && output.result) {
    return output.result;
  } else if (typeof output.reply === 'string' && output.reply) {
    return output.reply;
  } else if (output.alert !== undefined) {
    return typeof output.message === 'string'
      ? output.message
      : JSON.stringify(output, null, 2);
  } else if (typeof output.message === 'string' && output.message) {
    return output.message;
  }
  return JSON.stringify(output, null, 2);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// 历史路径:落库 content 字符串 → 展示文本(旧数据的 JSON 字面量兜底)
export function contentToDisplay(content: string): string {
  const trimmed = content.trim();
  if (!trimmed || !trimmed.startsWith('{')) {
    return content; // 普通文本/新数据/用户消息,原样返回
  }
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (!isPlainObject(parsed) || !KNOWN_KEYS.some((k) => k in parsed)) {
      return content; // 非普通对象、或对象无已知提取键(可能是用户发的合法 JSON),原样返回
    }
    return outputToContent(parsed);
  } catch {
    return content; // 非 JSON 字符串,原样返回
  }
}
