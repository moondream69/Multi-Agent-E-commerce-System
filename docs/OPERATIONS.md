# 运维手册:内部卖家工具(局域网部署)

> 定位:2-5 人跨境卖家团队内部工具。后端单容器(Docker Compose),前端开发态经 Vite 代理访问。

## 快速启动

```bash
# 1. 配置环境(仓库根 .env 单一真源,compose 共读)
cp .env.example .env
#   - LLM_API_KEY:DeepSeek API Key
#   - AUTH_JWT_SECRET:openssl rand -hex 32 生成(生产必改)
#   - AUTH_ADMIN_PASSWORD:初始管理员密码(启动时懒 seed;改密码需删 users 行后重启)
#   - EMBEDDING_API_URL:宿主机 Ollama 默认 http://localhost:11434;容器内 Ollama 改为 http://ollama:11434
#   - LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY:留空即禁用观测(no-op);自托管实例默认 http://localhost:3001

# 2. 启动基础设施 + 后端(首次自动迁移;管理员与演练买家幂等 seed)
docker compose up -d

# 3. 可选:容器内跑 Ollama(默认用宿主机)
docker compose --profile embed up -d ollama
docker compose exec ollama ollama pull bge-m3

# 4. 可选:开启模拟流量(每 300 秒一轮随机买家行为,烧真 DeepSeek token)
docker compose --profile sim up -d

# 5. 前端(开发态):npm run dev(5173,/api 与 /socket.io 代理到 3000)
cd frontend && npm run dev
```

## 硬约束与注意事项

- **单 worker**:`uvicorn --workers 1` 是架构硬约束(run.py 统一入口)。EventBus 是进程内实现,
  审批等待协程、WS sid→用户映射都依赖单进程。多 worker 会静默断开跨进程事件桥接。
  扩容需先把 EventBus 换成 Redis pub/sub(明确列为后续项)。
- **审批机制**:高危写操作(上架/下架/改价/删除/订单流转/取消)按切片打包为审批批次,
  经 durable interrupt 挂起(PostgresSaver 落库);批准/拒绝后用户指令驱动恢复(按钮或自然消息双入口),
  可跨进程重启恢复。批次不自动过期(approval_ttl_hours 为预留配置,暂无清扫任务)。
- **影子模式**:由 `ENVIRONMENT` 派生(dev=演练开影子/prod=生产锁审批,运行时不可切换)。
  演练环境 AI 高危建议只记录不执行,审批中心一键补执行。
- **LLM 分层失败语义**:瞬时错误(429/5xx/网络)本地重试 1-2 次(退避 1s/2s),仍败上抛;
  4xx(非 429)不重试。并发闸默认 2(`LLM_MAX_CONCURRENCY`,DeepSeek 账号级限流防护)。
  子图 LLM 失败收敛为切片「未完成+原因」,不穿透为服务错误;辅助簿记(记忆/广播)失败只记日志。
- **数据入口顺序**:CSV 导入**买家 → 订单**(订单按 email 解析买家,查不到即行级报错);
  商品 sku 幂等、订单 reference 幂等、买家 email 幂等;导入卡在驾驶舱左栏。
- **汇率**:非 CNY 订单落库时取快照(Redis 缓存 4h);汇率 API 与缓存双失效时**留空落库**
  并产生 fx_missing 通知(人工核对),不拒单。
- **LLM 预算**:模拟流量默认每天 200 个 LLM 任务,达到后当天只浏览(`--daily-budget` 可调;
  下单与买家查询不计预算)。

## 日常运维

```bash
# 备份(Postgres 数据即全部业务数据;Redis 只是缓存,可丢)
docker compose exec postgres pg_dump -U postgres mae > backup_$(date +%F).sql
# 恢复
cat backup_xxx.sql | docker compose exec -T postgres psql -U postgres mae

# 查看日志
docker compose logs -f app            # 应用(含 LLM 重试/失败收敛日志)
docker compose logs -f simulator      # 模拟流量

# 重启应用(改代码后重新构建)
docker compose up -d --build app
```

## 审计 SQL(跑一天后评估)

```bash
docker compose exec postgres psql -U postgres mae
```

```sql
-- 任务失败率与分布
SELECT type, status, count(*) FROM tasks GROUP BY type, status ORDER BY type;

-- 审批批次(采纳率/决策人分布)
SELECT mode, status, count(*) FROM approval_batches GROUP BY mode, status;
SELECT decided_by, count(*) FROM approval_batches WHERE decided_by IS NOT NULL GROUP BY decided_by;

-- 影子建议采纳率(mode='shadow' 中 executed 占比)
SELECT status, count(*) FROM approval_batches WHERE mode='shadow' GROUP BY status;

-- 工单(未结/已结)
SELECT status, count(*) FROM tickets GROUP BY status;

-- 模拟流量产量
SELECT count(*) FROM orders WHERE created_at > now() - interval '1 day';
SELECT count(*) FROM tasks WHERE created_at > now() - interval '1 day';
```

## 新增用户

无用户管理界面,直接插库(密码用 bcrypt):

```bash
docker compose exec postgres psql -U postgres mae -c \
  "INSERT INTO users (username, password_hash) VALUES ('alice', '<bcrypt哈希>');"
```

生成哈希:`uv run python -c "from python_backend.core.auth import hash_password; print(hash_password('密码'))"`

## 已知取舍与待办(详见 ADR-0005)

- 全员平权审批:任何登录者可通过/拒绝,`decided_by`/`comment` 审计兜底
- JWT 24h 无吊销:改 `AUTH_JWT_SECRET` 全员下线
- 审批批次无自动过期清扫(approval_ttl_hours 预留)
- 前端尚无生产托管(开发态 Vite 代理;部署形态待定),买家前台维持移除
- 下单入口 REST `/api/orders` 保留且需认证,供模拟流量使用(旧「演示买家前台直购」叙述已过时)
