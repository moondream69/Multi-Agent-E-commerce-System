import Markdown, { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

// —— Agent 消息的 Markdown 渲染缝(A16 / ADR-0007 C0):全仓唯一 react-markdown 配置点 ——
// 不透传原始 HTML(HTML 串以字面文本出现,DOM 不生成对应元素,无 XSS 面),不引 rehype-raw;
// 开 remark-gfm(表格/删除线/任务列表/自动链接)——Agent 正文常出表格,CommonMark 不解析(2026-09-14 决定)。
// 样式覆盖全走令牌类名字面量(index.css @theme inline),双主题自动适配。
// 用户消息刻意保持纯文本(旧系统基线取舍)——由调用点分流,不进本模块;
// 引用小点(T4/#51)也落在这套 components 覆盖上。
//
// whitespace-pre-wrap 只挂行内文本面(p / td / th),不挂容器:mdast 输出在块之间夹换行文本节点,
// 容器级 pre-wrap 会把它们放大成空行(清单每项多出一行);而 Agent 正文常以单换行分行
// (含按行拼的 ASCII 表),段落内须保留换行——这正是旧基线(消息气泡 whitespace-pre-wrap)的观感。

const components: Components = {
  p: ({ children }) => (
    <p className="my-1 leading-[1.7] whitespace-pre-wrap">{children}</p>
  ),
  h1: ({ children }) => (
    <h1 className="mt-3 mb-1 text-base font-semibold">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-3 mb-1 text-[15px] font-semibold">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-2.5 mb-1 text-sm font-semibold">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-2.5 mb-1 text-[13px] font-semibold">{children}</h4>
  ),
  ul: ({ children }) => <ul className="my-1 list-disc pl-4">{children}</ul>,
  ol: ({ children }) => <ol className="my-1 list-decimal pl-4">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  strong: ({ children }) => (
    <strong className="font-semibold">{children}</strong>
  ),
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
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-line px-2 py-1 whitespace-pre-wrap">
      {children}
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

/** 渲染 Agent 产出的 Markdown 文本(消息流 / 草稿预览 / 执行报告三处共用)。 */
export function AgentMarkdown({ children }: { children: string }) {
  return (
    <Markdown remarkPlugins={[remarkGfm]} components={components}>
      {children}
    </Markdown>
  );
}
