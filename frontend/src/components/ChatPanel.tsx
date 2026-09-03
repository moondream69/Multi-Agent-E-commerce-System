import React, { useState, useRef, useEffect } from 'react';
import Markdown from 'react-markdown';
import { ChatResponse } from '../types/events';

interface Props {
  onSend: (text: string) => void;
  lastResponse: ChatResponse | null;
}

// Agent 消息的 markdown 渲染样式(内联风格,与气泡一致;不含原始 HTML 透传,XSS 安全)
const markdownComponents: React.ComponentProps<typeof Markdown>['components'] =
  {
    p: ({ children }) => <p style={{ margin: '0 0 6px 0' }}>{children}</p>,
    h1: ({ children }) => (
      <h1 style={{ margin: '8px 0 4px', fontSize: 16, fontWeight: 600 }}>
        {children}
      </h1>
    ),
    h2: ({ children }) => (
      <h2 style={{ margin: '8px 0 4px', fontSize: 15, fontWeight: 600 }}>
        {children}
      </h2>
    ),
    h3: ({ children }) => (
      <h3 style={{ margin: '8px 0 4px', fontSize: 14, fontWeight: 600 }}>
        {children}
      </h3>
    ),
    h4: ({ children }) => (
      <h4 style={{ margin: '8px 0 4px', fontSize: 13, fontWeight: 600 }}>
        {children}
      </h4>
    ),
    ul: ({ children }) => (
      <ul style={{ paddingLeft: 18, margin: '0 0 6px' }}>{children}</ul>
    ),
    ol: ({ children }) => (
      <ol style={{ paddingLeft: 18, margin: '0 0 6px' }}>{children}</ol>
    ),
    li: ({ children }) => <li style={{ margin: 0 }}>{children}</li>,
    code: ({ children }) => (
      <code
        style={{
          background: '#f1f5f9',
          padding: '1px 4px',
          borderRadius: 4,
          fontFamily: 'monospace',
          fontSize: 12,
        }}
      >
        {children}
      </code>
    ),
    pre: ({ children }) => (
      <pre
        style={{
          background: '#f8fafc',
          border: '1px solid #e2e8f0',
          borderRadius: 6,
          padding: 8,
          overflowX: 'auto',
          fontSize: 12,
          margin: '0 0 6px',
        }}
      >
        {children}
      </pre>
    ),
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        style={{ color: '#2563eb', wordBreak: 'break-all' }}
      >
        {children}
      </a>
    ),
    blockquote: ({ children }) => (
      <blockquote
        style={{
          borderLeft: '3px solid #e2e8f0',
          paddingLeft: 8,
          color: '#666',
          margin: '0 0 6px',
        }}
      >
        {children}
      </blockquote>
    ),
  };

export function ChatPanel({ onSend, lastResponse }: Props) {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<
    Array<{ role: string; content: string; ts: string }>
  >([]);
  const processingRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!lastResponse) return;

    if (lastResponse.type === 'task_result') {
      const output = lastResponse.output;
      let content = '';
      if (typeof output.report === 'string' && output.report) {
        content = output.report;
      } else if (typeof output.result === 'string' && output.result) {
        content = output.result;
      } else if (typeof output.reply === 'string' && output.reply) {
        content = output.reply;
      } else if (output.alert !== undefined) {
        content =
          typeof output.message === 'string'
            ? output.message
            : JSON.stringify(output, null, 2);
      } else if (typeof output.message === 'string' && output.message) {
        content = output.message;
      } else {
        content = JSON.stringify(output, null, 2);
      }

      setMessages((prev) => {
        const updated = [...prev];
        if (
          processingRef.current &&
          updated.length > 0 &&
          updated[updated.length - 1].role === 'assistant'
        ) {
          updated[updated.length - 1] = {
            role: 'assistant',
            content,
            ts: new Date().toISOString(),
          };
        } else {
          updated.push({
            role: 'assistant',
            content,
            ts: new Date().toISOString(),
          });
        }
        return updated;
      });
      processingRef.current = false;
    } else if (lastResponse.type === 'task_error') {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `错误: ${lastResponse.error}`,
          ts: new Date().toISOString(),
        },
      ]);
      processingRef.current = false;
    }
  }, [lastResponse]);

  const handleSend = () => {
    if (!input.trim()) return;
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: input, ts: new Date().toISOString() },
    ]);
    onSend(input);
    setInput('');
    processingRef.current = true;
    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Agent 正在思考...',
          ts: new Date().toISOString(),
        },
      ]);
    }, 300);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #e0e0e0',
          fontWeight: 600,
          fontSize: 14,
        }}
      >
        与 Agent 团队对话
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        {messages.map((msg, i) => (
          <div
            key={i}
            style={{
              marginBottom: 8,
              padding: 8,
              borderRadius: 8,
              maxWidth: '85%',
              background:
                msg.role === 'user'
                  ? '#eff6ff'
                  : msg.content.startsWith('错误')
                    ? '#fef2f2'
                    : '#f3f4f6',
              alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
              fontSize: 13,
              lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
            }}
          >
            <div
              style={{
                fontWeight: 600,
                fontSize: 10,
                color: '#666',
                marginBottom: 2,
              }}
            >
              {msg.role === 'user' ? '你' : 'Agent'}
            </div>
            {msg.role === 'user' ? (
              msg.content
            ) : (
              <Markdown components={markdownComponents}>{msg.content}</Markdown>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div
        style={{
          padding: 12,
          borderTop: '1px solid #e0e0e0',
          display: 'flex',
          gap: 8,
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSend();
          }}
          placeholder="输入任务，如：分析蓝牙耳机市场趋势..."
          style={{
            flex: 1,
            padding: '8px 12px',
            border: '1px solid #d0d0d0',
            borderRadius: 6,
            fontSize: 13,
            outline: 'none',
          }}
        />
        <button
          onClick={handleSend}
          style={{
            padding: '8px 16px',
            background: '#2563eb',
            color: '#fff',
            border: 'none',
            borderRadius: 6,
            cursor: 'pointer',
            fontSize: 13,
          }}
        >
          发送
        </button>
      </div>
    </div>
  );
}
