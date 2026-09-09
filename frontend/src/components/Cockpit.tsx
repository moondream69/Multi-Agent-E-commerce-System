import { useCallback, useEffect, useState } from 'react';
import { EventWall } from './EventWall';
import { createTask, fetchTaskDetail, fetchTasks } from '../services/tasks';
import { importCsv } from '../services/imports';
import {
  ImportReport,
  SlicePlanSlice,
  TaskDetail,
  TaskListItem,
} from '../types/events';
import { theme } from '../theme';

// —— 驾驶舱(spec #8):Manager 第 4 角色(输入+规划轨迹)+ 切片时间线 + 事件流实况墙 ——

const AGENT_LABELS: Record<string, string> = {
  product_research: '选品',
  order_management: '订单',
  customer_service: '客服',
};

function shortId(id: string): string {
  return id.slice(0, 8);
}

type SliceState =
  | { kind: 'done' }
  | { kind: 'rejected' }
  | { kind: 'waiting_approval' }
  | { kind: 'pending' };

function sliceState(
  slice: SlicePlanSlice,
  detail: TaskDetail | null,
): SliceState {
  const result = detail?.results?.[String(slice.no)];
  if (result && typeof result === 'object') {
    const value = result as Record<string, unknown>;
    if (value.rejected) return { kind: 'rejected' };
    return { kind: 'done' };
  }
  const hasPendingBatch = detail?.batches.some(
    (batch) =>
      batch.sliceNo === slice.no &&
      batch.mode === 'approval' &&
      batch.status === 'pending',
  );
  if (hasPendingBatch) return { kind: 'waiting_approval' };
  return { kind: 'pending' };
}

function sliceStateLabel(state: SliceState): { text: string; color: string } {
  switch (state.kind) {
    case 'done':
      return { text: '完成', color: theme.color.success };
    case 'rejected':
      return { text: '被拒 · 重规划', color: theme.color.danger };
    case 'waiting_approval':
      return { text: '等待审批', color: theme.color.warning };
    default:
      return { text: '待执行', color: theme.color.textMuted };
  }
}

function TaskRow({
  task,
  selected,
  onSelect,
}: {
  task: TaskListItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      style={{
        display: 'flex',
        alignItems: 'baseline',
        gap: 8,
        width: '100%',
        padding: '8px 12px',
        border: 'none',
        borderLeft: `3px solid ${selected ? theme.color.brand : 'transparent'}`,
        background: selected ? theme.color.brandSoft : 'transparent',
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <span
        style={{
          fontSize: 11,
          fontFamily: theme.font.mono,
          color: theme.color.textMuted,
        }}
      >
        {shortId(task.threadId)}
      </span>
      <span
        style={{
          fontSize: 13,
          color: theme.color.text,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {task.title || '(空白任务)'}
      </span>
      <span
        style={{
          marginLeft: 'auto',
          fontSize: 11,
          color:
            task.status === 'failed'
              ? theme.color.danger
              : task.status === 'completed'
                ? theme.color.success
                : theme.color.textMuted,
          whiteSpace: 'nowrap',
        }}
      >
        {task.status === 'completed'
          ? '完成'
          : task.status === 'failed'
            ? '失败'
            : task.status === 'interrupted'
              ? '挂起'
              : '进行中'}
      </span>
    </button>
  );
}

function SliceTimeline({
  detail,
  onOpenApprovals,
}: {
  detail: TaskDetail | null;
  onOpenApprovals: () => void;
}) {
  if (!detail) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: theme.color.textMuted,
          fontSize: 13,
        }}
      >
        选择左侧任务查看切片时间线,或输入新需求让 Manager 规划。
      </div>
    );
  }

  const slices = detail.plan?.slices ?? [];
  return (
    <div
      style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px' }}
    >
      <div
        style={{
          fontSize: 13,
          fontFamily: theme.font.mono,
          color: theme.color.textMuted,
          marginBottom: 4,
        }}
      >
        任务 {shortId(detail.threadId)} · 切片计划 {slices.length} 段
      </div>
      <div
        style={{
          fontSize: 14,
          color: theme.color.text,
          marginBottom: 12,
          whiteSpace: 'pre-wrap',
        }}
      >
        {detail.request}
      </div>
      {detail.result?.error && (
        <div
          style={{
            marginBottom: 12,
            padding: '8px 12px',
            background: theme.color.dangerBg,
            border: `1px solid ${theme.color.danger}`,
            borderRadius: theme.radius.sm,
            color: theme.color.danger,
            fontSize: 13,
          }}
        >
          未完成:{detail.result.error}
        </div>
      )}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxWidth: 560,
        }}
      >
        {slices.map((slice) => {
          const state = sliceState(slice, detail);
          const label = sliceStateLabel(state);
          return (
            <div
              key={slice.no}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                background: theme.color.surface,
                border: `1px solid ${theme.color.border}`,
                borderRadius: theme.radius.sm,
                padding: '10px 14px',
              }}
            >
              <span
                style={{
                  width: 22,
                  height: 22,
                  borderRadius: theme.radius.full,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  fontWeight: 600,
                  background: theme.color.bg,
                  border: `1px solid ${theme.color.border}`,
                  color: theme.color.text,
                  flexShrink: 0,
                }}
              >
                {slice.no}
              </span>
              <span
                style={{
                  fontSize: 12,
                  color: theme.color.textSecondary,
                  whiteSpace: 'nowrap',
                }}
              >
                {AGENT_LABELS[slice.agent] ?? slice.agent}
                {slice.depends_on.length > 0 &&
                  ` · 依赖段 ${slice.depends_on.join(', ')}`}
              </span>
              <span
                style={{
                  fontSize: 13,
                  color: theme.color.text,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {slice.description}
              </span>
              <span
                style={{
                  marginLeft: 'auto',
                  fontSize: 12,
                  fontWeight: 500,
                  color: label.color,
                  whiteSpace: 'nowrap',
                }}
              >
                {label.text}
              </span>
              {state.kind === 'waiting_approval' && (
                <button
                  onClick={onOpenApprovals}
                  style={{
                    padding: '4px 10px',
                    border: `1px solid ${theme.color.warning}`,
                    borderRadius: theme.radius.sm,
                    background: theme.color.warningBg,
                    color: theme.color.warning,
                    cursor: 'pointer',
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                  }}
                >
                  去审批
                </button>
              )}
            </div>
          );
        })}
      </div>
      {detail.result?.summary && (
        <div
          style={{
            marginTop: 14,
            maxWidth: 560,
            padding: '10px 14px',
            background: theme.color.surface,
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            fontSize: 13,
            color: theme.color.textSecondary,
            whiteSpace: 'pre-wrap',
          }}
        >
          {detail.result.summary}
        </div>
      )}
    </div>
  );
}

function CsvImportCard() {
  // CSV 批量导入(spec #8 B8):文件读取后以文本体上传,行级报告 {created, skipped, errors}
  const [kind, setKind] = useState<'products' | 'orders'>('products');
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const text = await file.text();
      setReport(await importCsv(kind, text));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        padding: '12px 14px',
        borderBottom: `1px solid ${theme.color.border}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}>
        CSV 数据导入
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <select
          value={kind}
          onChange={(event) =>
            setKind(event.target.value as 'products' | 'orders')
          }
          style={{
            padding: '5px 8px',
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            fontSize: 12,
            background: theme.color.surface,
          }}
        >
          <option value="products">商品(sku 幂等,落草稿)</option>
          <option value="orders">订单(reference 去重)</option>
        </select>
        <input
          type="file"
          accept=".csv,text/csv"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}
          style={{ fontSize: 12, flex: 1, minWidth: 0 }}
        />
      </div>
      {busy && (
        <div style={{ fontSize: 12, color: theme.color.textMuted }}>
          导入中…
        </div>
      )}
      {report && (
        <div
          style={{
            fontSize: 12,
            padding: '8px 10px',
            background: theme.color.bg,
            border: `1px solid ${theme.color.border}`,
            borderRadius: theme.radius.sm,
            color: theme.color.textSecondary,
          }}
        >
          新建 {report.created} · 跳过 {report.skipped} · 错误{' '}
          {report.errors.length}
          {report.errors.slice(0, 3).map((item) => (
            <div key={item.row} style={{ color: theme.color.danger }}>
              第 {item.row} 行:{item.reason}
            </div>
          ))}
        </div>
      )}
      {error && (
        <div style={{ fontSize: 12, color: theme.color.danger }}>{error}</div>
      )}
    </div>
  );
}

export function Cockpit({ onOpenApprovals }: { onOpenApprovals: () => void }) {
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshTasks = useCallback(() => {
    fetchTasks()
      .then((rows) => {
        setTasks(rows);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks]);

  useEffect(() => {
    if (!selected) return;
    fetchTaskDetail(selected)
      .then((row) => {
        setDetail(row);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, [selected, tasks]);

  const submit = async () => {
    if (!input.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createTask(input.trim());
      setInput('');
      refreshTasks();
      setSelected(created.threadId);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
      {/* 左栏:Manager 输入 + 任务列表 */}
      <div
        style={{
          width: 280,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          borderRight: `1px solid ${theme.color.border}`,
          background: theme.color.surface,
        }}
      >
        <div
          style={{
            padding: '12px 14px',
            borderBottom: `1px solid ${theme.color.border}`,
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div
            style={{ fontSize: 13, fontWeight: 600, color: theme.color.text }}
          >
            Manager 第 4 角色
          </div>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void submit();
              }
            }}
            placeholder="给 Manager 下需求,例如:分析蓝牙音箱市场并生成选品报告"
            rows={4}
            style={{
              padding: '8px 10px',
              border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.sm,
              fontSize: 13,
              resize: 'none',
              fontFamily: 'inherit',
            }}
          />
          <button
            onClick={() => void submit()}
            disabled={busy || !input.trim()}
            style={{
              padding: '7px 0',
              border: 'none',
              borderRadius: theme.radius.sm,
              background: theme.color.brand,
              color: '#fff',
              cursor: busy ? 'not-allowed' : 'pointer',
              fontSize: 13,
              opacity: busy || !input.trim() ? 0.6 : 1,
            }}
          >
            {busy ? '规划中…' : '发起任务'}
          </button>
        </div>
        {error && (
          <div
            style={{
              margin: '8px 14px 0',
              fontSize: 12,
              color: theme.color.danger,
            }}
          >
            {error}
          </div>
        )}
        <CsvImportCard />
        <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
          {tasks.map((task) => (
            <TaskRow
              key={task.threadId}
              task={task}
              selected={selected === task.threadId}
              onSelect={() => {
                setSelected(task.threadId);
              }}
            />
          ))}
        </div>
      </div>

      {/* 中栏:切片时间线 */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <SliceTimeline detail={detail} onOpenApprovals={onOpenApprovals} />
      </div>

      {/* 右栏:事件流实况墙 */}
      <div style={{ width: 300, flexShrink: 0, display: 'flex', minHeight: 0 }}>
        <EventWall />
      </div>
    </div>
  );
}
