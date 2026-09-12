import { useCallback, useEffect, useState } from 'react';
import { BusinessSnapshot } from './BusinessSnapshot';
import { CollabPipeline } from './CollabPipeline';
import { EventWall } from './EventWall';
import { SessionBar } from './SessionBar';
import { SessionMessages } from './SessionMessages';
import { useCollabPipeline } from '../hooks/useCollabPipeline';
import { useSessions } from '../hooks/useSessions';
import { createTask, fetchTaskDetail, fetchTasks } from '../services/tasks';
import { importCsv } from '../services/imports';
import {
  ImportReport,
  SlicePlanSlice,
  TaskDetail,
  TaskListItem,
} from '../types/events';

// —— 驾驶舱(spec #8 / 2026-09 重绘):协作管线(签名)+ Manager 第 4 角色 + 切片时间线 + 事件墙 ——

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
      return { text: '完成', color: 'text-st-done' };
    case 'rejected':
      return { text: '被拒 · 重规划', color: 'text-st-failed' };
    case 'waiting_approval':
      return { text: '等待审批', color: 'text-st-approval' };
    default:
      return { text: '待执行', color: 'text-ink-3' };
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
  const statusText =
    task.status === 'completed'
      ? '完成'
      : task.status === 'failed'
        ? '失败'
        : task.status === 'interrupted'
          ? '挂起'
          : '进行中';
  const statusColor =
    task.status === 'failed'
      ? 'text-st-failed'
      : task.status === 'completed'
        ? 'text-st-done'
        : 'text-ink-3';
  return (
    <button
      onClick={onSelect}
      className={`flex w-full cursor-pointer items-baseline gap-2 border-l-[3px] px-3 py-2 text-left transition-colors ${
        selected
          ? 'border-brand bg-brand-soft'
          : 'border-transparent hover:bg-surface-2'
      }`}
    >
      <span className="font-mono text-[11px] text-ink-3">
        {shortId(task.threadId)}
      </span>
      <span className="truncate text-[13px]">{task.title || '(空白任务)'}</span>
      <span className={`ml-auto shrink-0 text-[11px] ${statusColor}`}>
        {statusText}
      </span>
    </button>
  );
}

export function SliceTimeline({
  detail,
  onOpenApprovals,
}: {
  detail: TaskDetail | null;
  onOpenApprovals: () => void;
}) {
  if (!detail) {
    return (
      <div className="flex flex-1 items-center justify-center text-[13px] text-ink-3">
        选择左侧任务查看切片时间线,或输入新需求让 Manager 规划。
      </div>
    );
  }

  const slices = detail.plan?.slices ?? [];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
      <div className="mb-1 font-mono text-xs text-ink-3">
        任务 {shortId(detail.threadId)} · 切片计划 {slices.length} 段
      </div>
      <div className="mb-3 text-sm whitespace-pre-wrap">{detail.request}</div>
      {detail.result?.error && (
        <div className="mb-3 rounded-lg border border-st-failed/40 bg-st-failed-bg px-3 py-2 text-[13px] text-st-failed">
          未完成:{detail.result.error}
        </div>
      )}
      <div className="flex max-w-[620px] flex-col gap-2">
        {slices.map((slice) => {
          const state = sliceState(slice, detail);
          const label = sliceStateLabel(state);
          return (
            <div
              key={slice.no}
              className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-3.5 py-2.5 shadow-card"
            >
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full border border-line bg-surface-2 text-[11px] font-semibold">
                {slice.no}
              </span>
              <span className="shrink-0 text-xs text-ink-2">
                {AGENT_LABELS[slice.agent] ?? slice.agent}
                {slice.depends_on.length > 0 &&
                  ` · 依赖段 ${slice.depends_on.join(', ')}`}
              </span>
              <span className="truncate text-[13px]">{slice.description}</span>
              <span
                className={`ml-auto shrink-0 text-xs font-medium ${label.color}`}
              >
                {label.text}
              </span>
              {state.kind === 'waiting_approval' && (
                <button
                  onClick={onOpenApprovals}
                  className="shrink-0 cursor-pointer rounded-lg border border-st-approval/50 bg-st-approval-bg px-2.5 py-1 text-xs text-st-approval transition-colors hover:bg-st-approval/15"
                >
                  去审批
                </button>
              )}
            </div>
          );
        })}
      </div>
      {/* spec #25:summary 契约口径为 string;历史脏行(对象)静默不渲染,防对象子节点再白屏 */}
      {typeof detail.result?.summary === 'string' && (
        <div className="mt-4 max-w-[620px] rounded-xl border border-line bg-surface px-3.5 py-2.5 text-[13px] text-ink-2 shadow-card whitespace-pre-wrap">
          {detail.result.summary}
        </div>
      )}
    </div>
  );
}

export function CsvImportCard() {
  // CSV 批量导入(spec #8 B8 + spec #11):文件读取后以文本体上传,行级报告 {created, skipped, errors}
  const [kind, setKind] = useState<'products' | 'orders' | 'customers'>(
    'products',
  );
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
    <div className="flex flex-col gap-2 border-b border-line px-3.5 py-3">
      <div className="text-[13px] font-semibold">CSV 数据导入</div>
      {/* 纵向堆叠(issue #30):左栏 ~280px,选择框固有宽度被最长选项文案撑到 200px+,
          横排会把文件输入压成 42px 窄条、「选择文件」按钮文字被裁 */}
      <div className="flex flex-col gap-2">
        <select
          value={kind}
          onChange={(event) =>
            setKind(event.target.value as 'products' | 'orders' | 'customers')
          }
          className="rounded-lg border border-line bg-surface px-2 py-1.5 text-xs text-ink outline-none focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
        >
          <option value="products">商品(sku 幂等,落草稿)</option>
          <option value="orders">订单(reference 去重)</option>
          <option value="customers">买家(email 幂等,先于订单导入)</option>
        </select>
        <input
          type="file"
          accept=".csv,text/csv"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            // 重置 value(先取 file 再清):浏览器对「同路径文件重选」不派发 change,
            // 不清则同名文件修正后重传会静默无效(走查缺陷)
            event.target.value = '';
            if (file) void upload(file);
          }}
          className="text-xs text-ink-2 file:mr-2 file:cursor-pointer file:rounded-lg file:border file:border-line file:bg-surface-2 file:px-2.5 file:py-1 file:text-xs file:text-ink-2 hover:file:bg-brand-soft"
        />
      </div>
      {busy && <div className="text-xs text-ink-3">导入中…</div>}
      {report && (
        <div className="rounded-lg border border-line bg-surface-2 px-2.5 py-2 text-xs text-ink-2">
          新建 {report.created} · 跳过 {report.skipped} · 错误{' '}
          {report.errors.length}
          {report.errors.slice(0, 3).map((item) => (
            <div key={item.row} className="text-st-failed">
              第 {item.row} 行:{item.reason}
            </div>
          ))}
        </div>
      )}
      {error && <div className="text-xs text-st-failed">{error}</div>}
    </div>
  );
}

export function Cockpit({ onOpenApprovals }: { onOpenApprovals: () => void }) {
  const pipeline = useCollabPipeline();
  const {
    sessionId,
    conversations,
    error: sessionError,
    refresh: refreshConversations,
    select: switchSession,
    startNew,
    remove: removeSession,
    rename: renameSession,
  } = useSessions();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshTasks = useCallback(() => {
    fetchTasks(sessionId)
      .then((rows) => {
        setTasks(rows);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, [sessionId]);

  useEffect(() => {
    setSelected(null);
    setDetail(null);
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
      const created = await createTask(input.trim(), sessionId);
      setInput('');
      refreshTasks();
      refreshConversations(); // 首条消息后会话惰性落库,列表补上标题
      setSelected(created.threadId);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
      {/* 签名视图:协作管线(通栏) */}
      <CollabPipeline state={pipeline} />

      <div className="flex min-h-0 flex-1 gap-4">
        {/* 左栏:会话 + Manager 输入 + 导入 + 任务列表 */}
        <div className="flex w-[300px] shrink-0 flex-col overflow-hidden rounded-card border border-line bg-surface shadow-card">
          <SessionBar
            current={sessionId}
            conversations={conversations}
            onSelect={(target) => {
              switchSession(target);
              // 点会话名 = 回到该会话的对话视图(spec #20 会话/任务双视图);换会话时下方 effect
              // 已清选中,但点当前会话不改 sessionId、effect 不重跑,故须在此显式清
              setSelected(null);
            }}
            onNew={startNew}
            onDelete={(target) => void removeSession(target)}
            onRename={(target, title) => void renameSession(target, title)}
            error={sessionError}
          />
          <div className="flex flex-col gap-2 border-b border-line px-3.5 py-3">
            <div className="text-[13px] font-semibold">Manager 第 4 角色</div>
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
              rows={3}
              className="resize-none rounded-lg border border-line bg-surface px-2.5 py-2 text-[13px] outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25"
            />
            <button
              onClick={() => void submit()}
              disabled={busy || !input.trim()}
              className="cursor-pointer rounded-lg bg-brand py-2 text-[13px] font-medium text-brand-contrast transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? '规划中…' : '发起任务'}
            </button>
            {error && <div className="text-xs text-st-failed">{error}</div>}
          </div>
          <CsvImportCard />
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
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

        {/* 中栏:经营快照 + 会话消息流 / 切片时间线 */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
          <BusinessSnapshot />
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-card border border-line bg-surface shadow-card">
            {selected ? (
              <SliceTimeline
                detail={detail}
                onOpenApprovals={onOpenApprovals}
              />
            ) : (
              <SessionMessages sessionId={sessionId} />
            )}
          </div>
        </div>

        {/* 右栏:事件流实况墙 */}
        <div className="flex w-[320px] shrink-0">
          <EventWall />
        </div>
      </div>
    </div>
  );
}
