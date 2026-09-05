# 运维手册:内部卖家工具(局域网部署)

> 定位:2-5 人跨境卖家团队内部工具。单机 Docker 部署,前端由后端静态托管(单端口 3000)。

## 快速启动

```bash
# 1. 配置环境(必填项)
cp .env.example .env
#   - LLM_API_KEY:DeepSeek API Key
#   - AUTH_JWT_SECRET:openssl rand -hex 32 生成
#   - AUTH_ADMIN_PASSWORD:初始管理员密码(seed 时创建,改密码需删 users 行后重跑 seed)
#   - EMBEDDING_API_URL:宿主机 Ollama 默认 http://localhost:11434;容器内 Ollama 改为 http://ollama:11434

# 2. 启动基础设施 + 应用(首次自动迁移 + 播种,均幂等)
docker compose up -d

# 3. 可选:容器内跑 Ollama(默认用宿主机)
docker compose --profile embed up -d ollama
docker compose exec ollama ollama pull bge-m3

# 4. 可选:开启模拟流量(每 300 秒一轮随机买家行为,烧真 DeepSeek token)
docker compose --profile sim up -d
```

局域网任意机器访问 `http://<宿主机IP>:3000`,用 admin 登录。

## 硬约束与注意事项

- **单 worker**:`uvicorn --workers 1` 是架构硬约束。EventBus 是进程内实现,跨 Agent 事件订阅、
  审批等待协程、WS sid→用户映射都依赖单进程。多 worker 会**静默断开跨 Agent 事件桥接**。
  扩容需先把 EventBus 换成 Redis pub/sub(明确列为后续项)。
- **审批机制**:高危写操作(订单状态流转、商品上架)需人工审批,审批请求 4 小时超时;
  进程重启会丢失在途等待(陈旧 pending 由启动清扫置 expired,重新发起指令即可)。
- **影子模式**:`.env` 设 `SHADOW_MODE=true` 后,AI 高危建议只记录不执行,审批中心一键补执行。
  建议配合模拟流量跑 7 天,收集建议/采纳率数据后再决定哪些工具可降级自动。
- **DeepSeek 限流**:系统内置并发闸(默认 2)+ 指数退避重试 + 熔断(连续 5 败开 60s)。
  高峰期任务仍可能失败(agent 状态 error 可见),依据日志中的 429 频率决定是否降频。
- **LLM 预算**:模拟流量默认每天 200 个 LLM 任务,达到后当天只浏览(`--daily-budget` 可调)。

## 日常运维

```bash
# 备份(Postgres 数据即全部业务数据;Redis 只是缓存,可丢)
docker compose exec postgres pg_dump -U postgres multi_agent_ecommerce > backup_$(date +%F).sql
# 恢复
cat backup_xxx.sql | docker compose exec -T postgres psql -U postgres multi_agent_ecommerce

# 查看日志
docker compose logs -f app            # 应用(含 LLM 重试/熔断日志)
docker compose logs -f simulator      # 模拟流量

# 重启应用(改代码后重新构建)
docker compose up -d --build app
```

## 审计 SQL(跑一天后评估)

```sql
-- Agent 任务失败率与原因
SELECT type, status, count(*) FROM agent_tasks GROUP BY type, status ORDER BY type;

-- 审批统计(采纳率/决策人分布)
SELECT mode, status, count(*) FROM approval_requests GROUP BY mode, status;
SELECT decidedBy, count(*) FROM approval_requests WHERE decidedBy IS NOT NULL GROUP BY decidedBy;

-- 影子建议采纳率(mode='shadow' 中 executed 占比)
SELECT status, count(*) FROM approval_requests WHERE mode='shadow' GROUP BY status;

-- 模拟流量产量
SELECT count(*) FROM orders WHERE createdAt > now() - interval '1 day';
SELECT count(*) FROM agent_tasks WHERE type='customer_service' AND "createdAt" > now() - interval '1 day';
```

## 新增用户

无用户管理界面,直接插库(密码用 bcrypt):

```bash
docker compose exec postgres psql -U postgres multi_agent_ecommerce -c \
  "INSERT INTO users (username, \"passwordHash\") VALUES ('alice', '<bcrypt哈希>');"
```

生成哈希:`uv run python -c "from python_backend.api.auth import hash_password; print(hash_password('密码'))"`

## 已知取舍(详见计划文档)

- 全员平权审批:任何登录者可通过/拒绝,`decidedBy/comment` 审计兜底
- JWT 24h 无吊销:改 `AUTH_JWT_SECRET` 全员下线
- store API(下单入口)保留且需认证,供模拟流量使用;"演示买家张伟前台直购"叙述已过时
