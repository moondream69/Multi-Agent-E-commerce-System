import { Children, ReactNode, useMemo, useState } from 'react';
import Markdown, { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import type { Citation, RecordCitation } from '../types/events';

// —— Agent 消息的 Markdown 渲染缝(A16 / ADR-0007 C0):全仓唯一 react-markdown 配置点 ——
// 不透传原始 HTML(HTML 串以字面文本出现,DOM 不生成对应元素,无 XSS 面),不引 rehype-raw;
// 开 remark-gfm(表格/删除线/任务列表/自动链接)——Agent 正文常出表格,CommonMark 不解析(2026-09-14 决定)。
// 样式覆盖全走令牌类名字面量(index.css @theme inline),双主题自动适配。
// 用户消息刻意保持纯文本(旧系统基线取舍)——由调用点分流,不进本模块;
// 引用小点(issue #51 / B28)落在这套 components 覆盖上:答案文本里的 [n] 渲染为可点上标,
// 编号由后端归一化(同一文档/同一记录合并),前端只按 [n] ↔ citations 查表;查不到编号即普通文本(不硬标)。
// 条目两类(#67):语料切块(切块原文 + 文献溯源)/ 系统记录(商品/订单:记录标识 + 查询结果正文),
// 弹层按 kind 分流——商品不是语料切块,不假装有章节与切块序号。
//
// whitespace-pre-wrap 只挂行内文本面(p / td / th),不挂容器:mdast 输出在块之间夹换行文本节点,
// 容器级 pre-wrap 会把它们放大成空行(清单每项多出一行);而 Agent 正文常以单换行分行
// (含按行拼的 ASCII 表),段落内须保留换行——这正是旧基线(消息气泡 whitespace-pre-wrap)的观感。

const NO_CITATIONS: Citation[] = [];
const CITATION_MARKER = /\[(\d+)\]/g;

/** 自由 JSONB(批次 run_output / 切片结果)里的引用条目:按契约形状取用,缺省即无引用。

    #52:答案载荷的调用面不止一处(执行报告 / 切片时间线),读法收在这里,不各写一份。
*/
export function citationsOf(value: unknown): Citation[] {
  const citations = (value as { citations?: unknown } | null | undefined)
    ?.citations;
  return Array.isArray(citations) ? (citations as Citation[]) : [];
}

/** 同上,取答案文本:空串/null 视为无答案(与 citationsOf 同一读法,同一组调用面)。 */
export function answerOf(value: unknown): string | null {
  const answer = (value as { answer?: unknown } | null | undefined)?.answer;
  return typeof answer === 'string' && answer.trim() !== '' ? answer : null;
}

/** 系统记录引用(#67:商品/订单查库结果)与语料切块引用的判别:kind 即判别键。 */
function isRecordCitation(citation: Citation): citation is RecordCitation {
  return citation.kind === 'product' || citation.kind === 'order';
}

/** 引用小点:上标编号;点开显示被引内容与完整溯源——语料给切块原文(文档标识/标题/来源/日期/
    章节/切块序号),系统记录(#67)给记录标识与查询结果正文。 */
function CitationMark({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-block align-super text-[10px] leading-none">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`引用 ${citation.number}`}
        onClick={() => setOpen((value) => !value)}
        className="cursor-pointer rounded-sm bg-brand-soft px-1 py-0.5 font-medium text-brand"
      >
        {citation.number}
      </button>
      {open && (
        <span
          role="note"
          className="absolute top-full left-0 z-10 mt-1 block max-h-[70vh] w-72 overflow-y-auto rounded-card border border-line bg-surface p-2.5 text-left text-[12px] leading-[1.6] whitespace-normal text-ink shadow-lg"
        >
          <span className="mb-0.5 block font-medium">{citation.title}</span>
          <span className="mb-1 block text-ink-3">
            {citation.source}
            {isRecordCitation(citation) || !citation.published_at
              ? ''
              : ` · ${citation.published_at}`}
          </span>
          {isRecordCitation(citation) ? (
            <span className="mt-1.5 block border-t border-line pt-1.5">
              <span className="mb-0.5 block font-mono text-[11px] text-ink-3">
                {citation.record.id}
              </span>
              <span className="block whitespace-pre-wrap">
                {citation.record.content}
              </span>
            </span>
          ) : (
            citation.chunks.map((chunk) => (
              <span
                key={chunk.id}
                className="mt-1.5 block border-t border-line pt-1.5"
              >
                <span className="mb-0.5 block font-mono text-[11px] text-ink-3">
                  {chunk.id}
                </span>
                <span className="mb-0.5 block text-ink-3">
                  {chunk.section}
                  {chunk.chunk_index === null
                    ? ''
                    : ` · 切块 ${chunk.chunk_index}`}
                </span>
                <span className="block whitespace-pre-wrap">
                  {chunk.content}
                </span>
              </span>
            ))
          )}
        </span>
      )}
    </span>
  );
}

/** 文本子节点里的 [n] → 引用上标;解析不到的编号原样保留(与后端「不硬标」同口径)。 */
function withCitations(
  children: ReactNode,
  byNumber: Map<number, Citation>,
): ReactNode {
  if (byNumber.size === 0) return children;
  return Children.map(children, (child, position) => {
    if (typeof child !== 'string') return child;
    const parts: ReactNode[] = [];
    let cursor = 0;
    for (const match of child.matchAll(CITATION_MARKER)) {
      const citation = byNumber.get(Number(match[1]));
      if (!citation) continue;
      const start = match.index ?? 0;
      if (start > cursor) parts.push(child.slice(cursor, start));
      parts.push(
        <CitationMark key={`${position}-${start}`} citation={citation} />,
      );
      cursor = start + match[0].length;
    }
    if (cursor === 0) return child;
    if (cursor < child.length) parts.push(child.slice(cursor));
    return parts;
  });
}

function buildComponents(citations: Citation[]): Components {
  const byNumber = new Map(
    citations.map((citation) => [citation.number, citation]),
  );
  const inline = (children: ReactNode) => withCitations(children, byNumber);
  return {
    p: ({ children }) => (
      <p className="my-1 leading-[1.7] whitespace-pre-wrap">
        {inline(children)}
      </p>
    ),
    h1: ({ children }) => (
      <h1 className="mt-3 mb-1 text-base font-semibold">{inline(children)}</h1>
    ),
    h2: ({ children }) => (
      <h2 className="mt-3 mb-1 text-[15px] font-semibold">
        {inline(children)}
      </h2>
    ),
    h3: ({ children }) => (
      <h3 className="mt-2.5 mb-1 text-sm font-semibold">{inline(children)}</h3>
    ),
    h4: ({ children }) => (
      <h4 className="mt-2.5 mb-1 text-[13px] font-semibold">
        {inline(children)}
      </h4>
    ),
    ul: ({ children }) => <ul className="my-1 list-disc pl-4">{children}</ul>,
    ol: ({ children }) => (
      <ol className="my-1 list-decimal pl-4">{children}</ol>
    ),
    li: ({ children }) => <li className="my-0.5">{inline(children)}</li>,
    strong: ({ children }) => (
      <strong className="font-semibold">{inline(children)}</strong>
    ),
    em: ({ children }) => <em className="italic">{inline(children)}</em>,
    // 行内代码=底色芯片;围栏块内的 code 由 pre 的 `[&>code]` 后代变体清回素面
    // (不用 className 判 language-:裸围栏 ``` 无语言标注,那种判法会漏)
    code: ({ children }) => (
      <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[12px]">
        {children}
      </code>
    ),
    pre: ({ children }) => (
      <pre className="my-1.5 overflow-x-auto rounded-lg border border-line bg-surface-2 p-2.5 font-mono text-xs [&>code]:rounded-none [&>code]:bg-transparent [&>code]:p-0">
        {children}
      </pre>
    ),
    table: ({ children }) => (
      <div className="my-1.5 overflow-x-auto">
        <table className="border-collapse text-xs">{children}</table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border border-line px-2 py-1 text-left font-semibold whitespace-pre-wrap">
        {inline(children)}
      </th>
    ),
    td: ({ children }) => (
      <td className="border border-line px-2 py-1 whitespace-pre-wrap">
        {inline(children)}
      </td>
    ),
    blockquote: ({ children }) => (
      <blockquote className="my-1.5 border-l-[3px] border-line pl-2.5 text-ink-2">
        {children}
      </blockquote>
    ),
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="break-all text-brand underline"
      >
        {children}
      </a>
    ),
  };
}

/** 渲染 Agent 产出的 Markdown 文本(消息流 / 草稿预览 / 执行报告三处共用)。 */
export function AgentMarkdown({
  children,
  citations = NO_CITATIONS,
}: {
  children: string;
  citations?: Citation[];
}) {
  const components = useMemo(() => buildComponents(citations), [citations]);
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </Markdown>
  );
}
