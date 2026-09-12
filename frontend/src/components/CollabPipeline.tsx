import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import {
  AGENT_ORDER,
  NodeState,
  PipelineState,
  SliceChip,
} from '../hooks/useCollabPipeline';

/** 协作管线(签名视图):Manager 与三业务 Agent 的实时编排状态。
 *  数据源 = task.planned / slice.started / slice.completed / approval.requested 事件。 */

export const AGENT_META: Record<string, { name: string; mark: string }> = {
  product_research: { name: '选品分析', mark: '选' },
  order_management: { name: '订单管理', mark: '单' },
  customer_service: { name: '客服', mark: '客' },
};

const NODE_STYLE: Record<
  NodeState,
  { text: string; label: string; dot: string; ring: string }
> = {
  idle: {
    text: 'text-ink-3',
    label: '待命',
    dot: 'bg-st-idle',
    ring: 'ring-line',
  },
  planning: {
    text: 'text-st-planning',
    label: '规划中',
    dot: 'bg-st-planning',
    ring: 'ring-st-planning/40',
  },
  running: {
    text: 'text-st-running',
    label: '执行中',
    dot: 'bg-st-running',
    ring: 'ring-st-running/40',
  },
  waiting: {
    text: 'text-st-approval',
    label: '等待审批',
    dot: 'bg-st-approval',
    ring: 'ring-st-approval/40',
  },
  done: {
    text: 'text-st-done',
    label: '已完成',
    dot: 'bg-st-done',
    ring: 'ring-st-done/40',
  },
  failed: {
    text: 'text-st-failed',
    label: '失败',
    dot: 'bg-st-failed',
    ring: 'ring-st-failed/40',
  },
};

const CHIP_STYLE: Record<
  SliceChip['status'],
  { dot: string; text: string; label: string }
> = {
  queued: { dot: 'bg-st-idle', text: 'text-ink-3', label: '待执行' },
  running: { dot: 'bg-st-running', text: 'text-st-running', label: '执行中' },
  waiting: {
    dot: 'bg-st-approval',
    text: 'text-st-approval',
    label: '等待审批',
  },
  done: { dot: 'bg-st-done', text: 'text-st-done', label: '完成' },
  failed: { dot: 'bg-st-failed', text: 'text-st-failed', label: '失败' },
  rejected: { dot: 'bg-st-failed', text: 'text-st-failed', label: '已拒绝' },
};

const ACTIVE: NodeState[] = ['planning', 'running', 'waiting'];

function stateSummary(state: PipelineState): string {
  if (!state.threadId) return '待命 · 在下方向 Manager 下达需求';
  const total = state.slices.length;
  const done = state.slices.filter((s) => s.status === 'done').length;
  switch (state.manager) {
    case 'planning':
      return 'Manager 正在规划切片…';
    case 'running':
      return total > 0 ? `执行中 · ${done}/${total} 个切片完成` : '协调执行中…';
    case 'waiting':
      return '等待人工审批 · 批准后继续';
    case 'done':
      return `任务完成 · 共 ${total} 个切片`;
    case 'failed':
      return '任务失败 · 详情见事件流';
    default:
      return '待命';
  }
}

/** 点状脉冲(状态变化的轻量提示;reduced-motion 下静止)。 */
function pulse(animate: boolean) {
  return animate
    ? {
        initial: { scale: 0.94, opacity: 0.6 },
        animate: { scale: 1, opacity: 1 },
      }
    : { initial: false as const, animate: { scale: 1, opacity: 1 } };
}

export function CollabPipeline({ state }: { state: PipelineState }) {
  const reduced = useReducedMotion() ?? false;
  const boxRef = useRef<HTMLDivElement>(null);
  const managerRef = useRef<HTMLDivElement>(null);
  const agentRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [curves, setCurves] = useState<Array<{ agent: string; d: string }>>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });

  // 测量节点锚点 → 生成总线路径(Manager 右心中点 → 各 Agent 左心中点)
  useLayoutEffect(() => {
    const measure = () => {
      const box = boxRef.current;
      const manager = managerRef.current;
      if (!box || !manager) return;
      const base = box.getBoundingClientRect();
      const mx = manager.getBoundingClientRect().right - base.left + 10;
      const my =
        manager.getBoundingClientRect().top -
        base.top +
        manager.getBoundingClientRect().height / 2;
      const next: Array<{ agent: string; d: string }> = [];
      for (const agent of AGENT_ORDER) {
        const node = agentRefs.current[agent];
        if (!node) continue;
        const rect = node.getBoundingClientRect();
        const ax = rect.left - base.left - 10;
        const ay = rect.top - base.top + rect.height / 2;
        const mid = mx + (ax - mx) * 0.5;
        next.push({
          agent,
          d: `M ${mx} ${my} C ${mid} ${my}, ${mid} ${ay}, ${ax} ${ay}`,
        });
      }
      setCurves(next);
      setSize({ w: base.width, h: base.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (boxRef.current) observer.observe(boxRef.current);
    return () => observer.disconnect();
  }, [state.slices.length, state.threadId]);

  useEffect(() => {
    if (!state.pulse) return;
    const id = window.setTimeout(() => undefined, 0);
    return () => window.clearTimeout(id);
  }, [state.pulse]);

  const managerStyle = NODE_STYLE[state.manager];
  const active = state.threadId !== null;

  return (
    <section
      aria-label="协作管线"
      className="rounded-card border border-line bg-surface shadow-card"
    >
      <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <span className="text-[11px] font-semibold tracking-[0.14em] text-ink-3 uppercase">
          协作管线
        </span>
        <span
          className={`flex items-center gap-1.5 text-xs ${managerStyle.text}`}
        >
          <span
            className={`size-1.5 rounded-full ${managerStyle.dot} ${state.manager === 'waiting' ? 'animate-breathe' : ''}`}
          />
          {stateSummary(state)}
        </span>
        {state.threadId && (
          <code className="ml-auto rounded-md bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-ink-3">
            {state.threadId.slice(0, 8)}
          </code>
        )}
      </header>

      <div ref={boxRef} className="relative px-5 py-4">
        <svg
          className="pointer-events-none absolute inset-0"
          width={size.w}
          height={size.h}
          viewBox={`0 0 ${size.w} ${size.h}`}
          aria-hidden
        >
          {curves.map(({ agent, d }) => {
            const agentState = state.agents[agent] ?? 'idle';
            const isActive = active && ACTIVE.includes(agentState);
            const isDone = agentState === 'done';
            const stroke = isDone
              ? 'var(--st-done)'
              : agentState === 'waiting'
                ? 'var(--st-approval)'
                : agentState === 'failed'
                  ? 'var(--st-failed)'
                  : 'var(--st-running)';
            return (
              <g key={agent}>
                <path
                  d={d}
                  fill="none"
                  stroke="var(--pipe-line)"
                  strokeWidth={1.2}
                />
                {(isActive || isDone) && (
                  <motion.path
                    d={d}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={1.6}
                    initial={reduced ? { pathLength: 1 } : { pathLength: 0 }}
                    animate={{ pathLength: 1 }}
                    transition={{
                      duration: reduced ? 0 : 0.55,
                      ease: 'easeOut',
                    }}
                  />
                )}
                {(agentState === 'running' || agentState === 'planning') && (
                  <path
                    d={d}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={1.6}
                    strokeDasharray="3 7"
                    className="animate-flow"
                    opacity={0.9}
                  />
                )}
              </g>
            );
          })}
        </svg>

        <div className="relative z-10 flex flex-col items-stretch gap-3 lg:flex-row lg:items-start lg:gap-5">
          {/* Manager 节点 */}
          <motion.div
            ref={managerRef}
            key={`manager-${state.manager}`}
            {...pulse(!reduced)}
            transition={{ type: 'spring', stiffness: 380, damping: 26 }}
            className={`flex shrink-0 items-center gap-2.5 rounded-[14px] border border-line bg-surface-2 px-3.5 py-2.5 ring-1 ${
              active ? managerStyle.ring : 'ring-transparent'
            }`}
          >
            <span
              className={`flex size-8 items-center justify-center rounded-[10px] text-sm font-bold ${
                active
                  ? `${NODE_STYLE[state.manager].text} bg-surface`
                  : 'text-ink-3 bg-surface'
              } border border-line`}
            >
              M
            </span>
            <span className="flex flex-col leading-tight">
              <span className="text-xs font-semibold">Manager</span>
              <span
                className={`flex items-center gap-1 text-[11px] ${managerStyle.text}`}
              >
                {managerStyle.label}
                {state.manager === 'planning' && (
                  <span className="inline-flex gap-0.5">
                    {[0, 1, 2].map((i) => (
                      <motion.span
                        key={i}
                        className="inline-block size-0.5 rounded-full bg-current"
                        animate={reduced ? {} : { opacity: [0.2, 1, 0.2] }}
                        transition={{
                          duration: 1.1,
                          repeat: Infinity,
                          delay: i * 0.18,
                        }}
                      />
                    ))}
                  </span>
                )}
              </span>
            </span>
          </motion.div>

          {/* 三业务 Agent 节点 + 各自切片 chips */}
          <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-3 lg:gap-5">
            {AGENT_ORDER.map((agent) => {
              const node = NODE_STYLE[state.agents[agent] ?? 'idle'];
              const meta = AGENT_META[agent];
              const chips = state.slices.filter((s) => s.agent === agent);
              const lit = active && (state.agents[agent] ?? 'idle') !== 'idle';
              return (
                <div
                  key={agent}
                  className="flex min-w-0 flex-col items-center gap-1.5"
                >
                  <motion.div
                    ref={(el) => {
                      agentRefs.current[agent] = el;
                    }}
                    key={`${agent}-${state.agents[agent]}`}
                    {...pulse(!reduced && lit)}
                    transition={{ type: 'spring', stiffness: 380, damping: 24 }}
                    className={`flex w-full max-w-[220px] items-center gap-2.5 rounded-xl border border-line px-3 py-2 ${
                      lit ? `${node.ring} ring-1` : ''
                    } ${lit ? 'bg-surface-2' : 'bg-surface opacity-60'}`}
                  >
                    <span
                      className={`flex size-6 items-center justify-center rounded-lg border border-line bg-surface text-[11px] font-semibold ${node.text}`}
                    >
                      {meta.mark}
                    </span>
                    <span className="flex min-w-0 flex-col leading-tight">
                      <span className="truncate text-xs font-medium">
                        {meta.name}
                      </span>
                      <span
                        className={`flex items-center gap-1 text-[11px] ${node.text}`}
                      >
                        <span
                          className={`size-1.5 rounded-full ${node.dot} ${state.agents[agent] === 'waiting' ? 'animate-breathe' : ''}`}
                        />
                        {node.label}
                      </span>
                    </span>
                  </motion.div>

                  <div className="flex w-full max-w-[240px] flex-col items-stretch gap-1">
                    <AnimatePresence initial={false}>
                      {chips.map((chip) => {
                        const style = CHIP_STYLE[chip.status];
                        return (
                          <motion.div
                            key={chip.no}
                            layout
                            initial={
                              reduced
                                ? { opacity: 0 }
                                : { opacity: 0, y: -6, scale: 0.96 }
                            }
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.96 }}
                            transition={{
                              type: 'spring',
                              stiffness: 480,
                              damping: 32,
                            }}
                            className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2 py-1"
                          >
                            <span
                              className={`size-1.5 shrink-0 rounded-full ${style.dot} ${chip.status === 'waiting' ? 'animate-breathe' : ''}`}
                            />
                            <span className="shrink-0 font-mono text-[10px] text-ink-3">
                              #{chip.no}
                            </span>
                            <span
                              className="truncate text-[11px] text-ink-2"
                              title={chip.description}
                            >
                              {chip.description || AGENT_META[chip.agent]?.name}
                            </span>
                            <span
                              className={`ml-auto shrink-0 text-[10px] ${style.text}`}
                            >
                              {style.label}
                            </span>
                          </motion.div>
                        );
                      })}
                    </AnimatePresence>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
