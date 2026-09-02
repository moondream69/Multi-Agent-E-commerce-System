import { Controller, Get } from '@nestjs/common';
import { OrchestratorService } from '../../core/orchestrator/orchestrator.service';
import { AgentStatus } from '../../common/interfaces';

@Controller('api/dashboard')
export class DashboardController {
  constructor(private readonly orchestrator: OrchestratorService) {}

  @Get('agents')
  getAgents() {
    return this.orchestrator.getRegisteredAgents().map((a) => ({
      id: a.id,
      name: a.name,
      description: a.description,
      status: a.getStatus(),
      tools: a.getTools(),
    }));
  }

  @Get('status')
  getStatus() {
    const agents = this.orchestrator.getRegisteredAgents();
    return {
      totalAgents: agents.length,
      onlineAgents: agents.filter(
        (a) =>
          a.getStatus() === AgentStatus.IDLE ||
          a.getStatus() === AgentStatus.BUSY,
      ).length,
      timestamp: new Date().toISOString(),
    };
  }
}
