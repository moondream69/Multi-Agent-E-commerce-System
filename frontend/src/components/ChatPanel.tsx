import React, { useState, useRef, useEffect } from 'react';
import Markdown from 'react-markdown';
import { ConversationMeta } from '../types/events';
import { StepsTimeline, StepEntry } from './StepsTimeline';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  ts: string;
  steps?: StepEntry[];
  placeholder?: boolean;
}

interface Props {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  title?: string;
  placeholder?: string;
  sessions?: ConversationMeta[];
  currentSessionId?: string | null;
  onNewSession?: () => void;
  onSwitchSession?: (sessionId: string) => void;
  onDeleteSession?: (sessionId: string) => void;
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

export function ChatPanel({
  messages,
  onSend,
  title = '与 Agent 团队对话',
  placeholder = '输入任务，如：分析蓝牙耳机市场趋势...',
  sessions,
  currentSessionId,
  onNewSession,
  onSwitchSession,
  onDeleteSession,
}: Props) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    if (!input.trim()) return;
    onSend(input);
    setInput('');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          padding: '8px 16px',
          borderBottom: '1px solid #e0e0e0',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <span
          style={{
            fontWeight: 600,
            fontSize: 14,
            marginRight: 'auto',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {title}
        </span>
        {sessions && (
          <>
            <select
              value={currentSessionId ?? ''}
              onChange={(e) => {
                const value = e.target.value;
                if (value) onSwitchSession?.(value);
                else onNewSession?.();
              }}
              style={{
                fontSize: 12,
                padding: '3px 6px',
                border: '1px solid #d0d0d0',
                borderRadius: 6,
                maxWidth: 180,
                outline: 'none',
                background: '#fff',
              }}
            >
              <option value="">＋ 新会话</option>
              {sessions.map((s) => (
                <option key={s.sessionId} value={s.sessionId}>
                  {s.title || '未命名会话'}
                </option>
              ))}
            </select>
            <button
              onClick={onNewSession}
              style={{
                padding: '4px 10px',
                border: '1px solid #d0d0d0',
                borderRadius: 6,
                background: 'transparent',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              新建
            </button>
            {currentSessionId && (
              <button
                onClick={() => {
                  void onDeleteSession?.(currentSessionId);
                }}
                style={{
                  padding: '4px 10px',
                  border: '1px solid #d0d0d0',
                  borderRadius: 6,
                  background: 'transparent',
                  color: '#ef4444',
                  fontSize: 12,
                  cursor: 'pointer',
                }}
              >
                删除
              </button>
            )}
          </>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 12 }}>
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
              <>
                <Markdown components={markdownComponents}>
                  {msg.content}
                </Markdown>
                {msg.steps && msg.steps.length > 0 && (
                  <StepsTimeline steps={msg.steps} />
                )}
              </>
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
          placeholder={placeholder}
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
