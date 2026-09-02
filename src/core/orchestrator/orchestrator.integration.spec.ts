import { Test, TestingModule } from '@nestjs/testing';
import { EventEmitterModule } from '@nestjs/event-emitter';
import { OrchestratorService } from './orchestrator.service';
import { EventBusService } from '../event-bus/event-bus.service';
import { BaseAgent } from '../agent-base/base-agent';
import { ReActLoopService } from '../agent-base/react-loop.service';
import {
  AgentEvent,
  AgentStatus,
  AgentTask,
  TaskStatus,
  TaskType,
  AgentEventType,
  ToolDefinition,
} from '../../common/interfaces';

const mockReActLoop = {
  run: jest.fn().mockResolvedValue({ result: 'test' }),
} as unknown as ReActLoopService;

class TestResearchAgent extends BaseAgent {
  id = 'research-1';
  name = '选品Agent';
  description = '负责选品分析';
  systemPrompt = 'test';

  getTools(): ToolDefinition[] {
    return [];
  }
  executeTask(task: AgentTask): Promise<Record<string, unknown>> {
    return Promise.resolve({ report: `分析完成: ${String(task.input.query)}` });
  }
  handleEvent(event: AgentEvent): Promise<void> {
    void event;
    return Promise.resolve();
  }
}

class TestOrderAgent extends BaseAgent {
  id = 'order-1';
  name = '订单Agent';
  description = '负责订单处理';
  systemPrompt = 'test';

  getTools(): ToolDefinition[] {
    return [];
  }
  executeTask(task: AgentTask): Promise<Record<string, unknown>> {
    return Promise.resolve({
      order: `订单已处理: ${JSON.stringify(task.input)}`,
    });
  }
  handleEvent(event: AgentEvent): Promise<void> {
    this.logger.log(`收到事件: ${event.type}`);
    return Promise.resolve();
  }
}

describe('Framework Integration', () => {
  let orchestrator: OrchestratorService;
  let eventBus: EventBusService;

  beforeEach(async () => {
    const module: TestingModule = await Test.createTestingModule({
      imports: [EventEmitterModule.forRoot()],
      providers: [OrchestratorService, EventBusService],
    }).compile();

    orchestrator = module.get<OrchestratorService>(OrchestratorService);
    eventBus = module.get<EventBusService>(EventBusService);
  });

  it('完整的 Agent 注册 → 任务路由 → 结果返回流程', async () => {
    const emitSpy = jest.spyOn(eventBus, 'emit');
    const researchAgent = new TestResearchAgent(mockReActLoop, eventBus);
    orchestrator.registerAgent(researchAgent, TaskType.PRODUCT_RESEARCH);

    const task: AgentTask = {
      id: 'task-int-1',
      type: TaskType.PRODUCT_RESEARCH,
      input: { query: '蓝牙耳机市场趋势' },
      createdAt: new Date(),
    };

    const result = await orchestrator.routeTask(task);
    expect(result.status).toBe(TaskStatus.COMPLETED);
    expect(result.output).toHaveProperty('report');

    expect(emitSpy).toHaveBeenCalledWith(
      AgentEventType.AGENT_STATUS_CHANGED,
      expect.objectContaining({
        agentId: 'research-1',
        status: AgentStatus.BUSY,
      }),
      undefined,
      'research-1',
    );
    expect(emitSpy).toHaveBeenCalledWith(
      AgentEventType.AGENT_STATUS_CHANGED,
      expect.objectContaining({
        agentId: 'research-1',
        status: AgentStatus.IDLE,
      }),
      undefined,
      'research-1',
    );
  });

  it('事件广播通知所有 Agent', async () => {
    const orderAgent = new TestOrderAgent(mockReActLoop, eventBus);
    orchestrator.registerAgent(orderAgent);

    const handleEventSpy = jest.spyOn(orderAgent, 'handleEvent');

    await orchestrator.broadcastEvent(AgentEventType.REPORT_GENERATED, {
      reportId: 'r-1',
    });

    expect(handleEventSpy).toHaveBeenCalled();
    expect(handleEventSpy).toHaveBeenCalledWith(
      expect.objectContaining({ type: AgentEventType.REPORT_GENERATED }),
    );
  });

  it('并发任务重叠时状态保持 BUSY，最后一个完成才变为 IDLE', async () => {
    const researchAgent = new TestResearchAgent(mockReActLoop, eventBus);
    orchestrator.registerAgent(researchAgent, TaskType.PRODUCT_RESEARCH);
    const emitSpy = jest.spyOn(eventBus, 'emit');

    // TestResearchAgent 覆写了 executeTask(不走 reactLoop.run),直接 spy 它挂起第一个任务
    let resolveFirst: (value: unknown) => void = () => {};
    jest
      .spyOn(
        researchAgent as unknown as {
          executeTask: (t: AgentTask) => Promise<unknown>;
        },
        'executeTask',
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockReturnValueOnce(Promise.resolve({ result: 'second' }));

    const first = orchestrator.routeTask({
      id: 'c1',
      type: TaskType.PRODUCT_RESEARCH,
      input: { query: 'first' },
      createdAt: new Date(),
    });
    const second = orchestrator.routeTask({
      id: 'c2',
      type: TaskType.PRODUCT_RESEARCH,
      input: { query: 'second' },
      createdAt: new Date(),
    });

    // 第二个任务先完成，但第一个仍在执行 → 状态保持 BUSY
    await second;
    expect(researchAgent.getStatus()).toBe(AgentStatus.BUSY);

    resolveFirst({ result: 'first' });
    await first;
    expect(researchAgent.getStatus()).toBe(AgentStatus.IDLE);

    expect(emitSpy).toHaveBeenCalledWith(
      AgentEventType.AGENT_STATUS_CHANGED,
      expect.objectContaining({
        agentId: 'research-1',
        status: AgentStatus.BUSY,
        taskId: 'c1',
      }),
      undefined,
      'research-1',
    );
    expect(emitSpy).toHaveBeenCalledWith(
      AgentEventType.AGENT_STATUS_CHANGED,
      expect.objectContaining({
        agentId: 'research-1',
        status: AgentStatus.IDLE,
        taskId: 'c1',
      }),
      undefined,
      'research-1',
    );
  });
});
