import { describe, expect, it } from 'vitest';
import {
  AGENT_ORDER,
  initialPipeline,
  reducePipeline,
} from './useCollabPipeline';
import { EventType } from '../types/events';

type Step = [string, Record<string, unknown>];

function run(...steps: Step[]) {
  return steps.reduce(
    (state, [event, payload]) => reducePipeline(state, event, payload),
    initialPipeline(),
  );
}

const created: Step = [
  EventType.TASK_CREATED,
  { threadId: 't1', status: 'created' },
];
const planned: Step = [
  EventType.TASK_PLANNED,
  {
    threadId: 't1',
    slices: [
      {
        no: 1,
        agent: 'order_management',
        description: '上架商品',
        dependsOn: [],
        approvalPoints: ['上架审批'],
      },
      {
        no: 2,
        agent: 'customer_service',
        description: '起草回复',
        dependsOn: [],
        approvalPoints: [],
      },
    ],
  },
];

describe('协作管线状态机(useCollabPipeline reducer)', () => {
  it('待命初始态:manager/三 Agent 全 idle,无切片', () => {
    const state = initialPipeline();
    expect(state.threadId).toBeNull();
    expect(state.manager).toBe('idle');
    expect(AGENT_ORDER.every((a) => state.agents[a] === 'idle')).toBe(true);
    expect(state.slices).toEqual([]);
  });

  it('任务创建 → Manager 规划中;计划到达 → 切片入列、Manager 转入执行', () => {
    const planning = run(created);
    expect(planning.manager).toBe('planning');

    const ready = run(created, planned);
    expect(ready.manager).toBe('running');
    expect(ready.slices.map((s) => s.status)).toEqual(['queued', 'queued']);
    // 计划含两个不同 Agent 的切片 → 两节点均亮(有活)
    expect(ready.agents.order_management).toBe('running');
    expect(ready.agents.customer_service).toBe('running');
    expect(ready.agents.product_research).toBe('idle');
  });

  it('分派 → 切片执行中;审批请求 → 切片等待、Manager 挂起等待', () => {
    const started = run(created, planned, [
      EventType.SLICE_STARTED,
      { threadId: 't1', sliceNo: 1, agent: 'order_management' },
    ]);
    expect(started.slices[0].status).toBe('running');
    expect(started.agents.order_management).toBe('running');

    const waiting = reducePipeline(started, EventType.APPROVAL_REQUESTED, {
      threadId: 't1',
      sliceNo: 1,
      agent: 'order_management',
      batches: [],
    });
    expect(waiting.slices[0].status).toBe('waiting');
    expect(waiting.agents.order_management).toBe('waiting');
    expect(waiting.manager).toBe('waiting');
  });

  it('审批中同时另一 Agent 在跑:该 Agent 保持执行中(不被等待态覆盖)', () => {
    const state = run(
      created,
      planned,
      [
        EventType.SLICE_STARTED,
        { threadId: 't1', sliceNo: 1, agent: 'order_management' },
      ],
      [
        EventType.SLICE_STARTED,
        { threadId: 't1', sliceNo: 2, agent: 'customer_service' },
      ],
      [
        EventType.APPROVAL_REQUESTED,
        { threadId: 't1', sliceNo: 1, agent: 'order_management', batches: [] },
      ],
    );
    expect(state.agents.order_management).toBe('waiting');
    expect(state.agents.customer_service).toBe('running');
  });

  it('切片完成 → 打勾归位;全部完成后任务收尾 Manager 完成', () => {
    const done = run(
      created,
      planned,
      [
        EventType.SLICE_STARTED,
        { threadId: 't1', sliceNo: 1, agent: 'order_management' },
      ],
      [
        EventType.SLICE_COMPLETED,
        {
          threadId: 't1',
          sliceNo: 1,
          agent: 'order_management',
          status: 'completed',
        },
      ],
      [EventType.TASK_COMPLETED, { threadId: 't1', status: 'completed' }],
    );
    expect(done.slices.every((s) => s.status === 'done')).toBe(true);
    expect(done.manager).toBe('done');
    expect(done.agents.order_management).toBe('done');
  });

  it('拒绝/失败如实映射:rejected 与 failed 不混同,任务失败收敛在跑切片', () => {
    const rejected = run(
      created,
      planned,
      [
        EventType.SLICE_STARTED,
        { threadId: 't1', sliceNo: 1, agent: 'order_management' },
      ],
      [
        EventType.SLICE_COMPLETED,
        {
          threadId: 't1',
          sliceNo: 1,
          agent: 'order_management',
          status: 'rejected',
        },
      ],
    );
    expect(rejected.slices[0].status).toBe('rejected');

    const failed = run(
      created,
      planned,
      [
        EventType.SLICE_STARTED,
        { threadId: 't1', sliceNo: 1, agent: 'order_management' },
      ],
      [
        EventType.TASK_FAILED,
        { threadId: 't1', status: 'failed', error: 'LLM 失败' },
      ],
    );
    expect(failed.manager).toBe('failed');
    expect(failed.slices[0].status).toBe('failed');
    expect(failed.agents.order_management).toBe('failed');
  });

  it('陌生 thread 的事件被忽略(保状态引用不变,防串台)', () => {
    const base = run(created, planned);
    const other = reducePipeline(base, EventType.SLICE_STARTED, {
      threadId: 'other-thread',
      sliceNo: 9,
      agent: 'product_research',
    });
    expect(other).toBe(base);
  });
});
