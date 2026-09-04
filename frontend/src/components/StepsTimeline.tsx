import React, { useState } from 'react';

export interface StepEntry {
  name: string;
  status: string;
  detail: string;
  startedAt?: string;
  completedAt?: string | null;
}

interface Props {
  steps: StepEntry[];
}

// 状态色点;未匹配状态用灰(ws 下发值为小写:completed/in_progress/failed)
const STEP_COLORS: Record<string, string> = {
  completed: '#22c55e',
  in_progress: '#3b82f6',
  failed: '#ef4444',
};

// 非工具类步骤(推理轮/最终答案/生命周期),不计入工具调用数
const NON_TOOL_RE =
  /^(reasoning_|final_answer|done|start|error|workflow_prompt|workflow_enforce)/;

export function StepsTimeline({ steps }: Props) {
  const [expanded, setExpanded] = useState(false);
  const toolCount = steps.filter(
    (s) => !NON_TOOL_RE.test(s.name) && s.status === 'completed',
  ).length;

  return (
    <div
      style={{
        marginTop: 6,
        border: '1px solid #e5e7eb',
        borderRadius: 6,
        background: '#fafafa',
        fontSize: 11,
      }}
    >
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          padding: '4px 8px',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          fontSize: 11,
          color: '#374151',
          width: '100%',
          textAlign: 'left',
        }}
      >
        {expanded
          ? '收起步骤'
          : `共 ${steps.length} 步 · ${toolCount} 个工具调用 ▸`}
      </button>
      {expanded && (
        <div
          style={{
            padding: '0 8px 8px',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          {steps.map((s, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                gap: 6,
                alignItems: 'baseline',
                lineHeight: 1.4,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 3,
                  background: STEP_COLORS[s.status] ?? '#9ca3af',
                  flexShrink: 0,
                  alignSelf: 'center',
                }}
              />
              <span style={{ fontWeight: 600, color: '#374151' }}>
                {s.name}
              </span>
              <span
                style={{
                  color: '#9ca3af',
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {s.detail}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
