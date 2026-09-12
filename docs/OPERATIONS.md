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

> ⚠️ **dev server 必须落在 5173**:后端 `cors_origins` 默认只放行 `http://localhost:5173`。
> 5173 被占时 Vite 会自动跳到 5174+,此时 REST 仍通(代理直连),但 **socket.io 握手会被
> 后端按来源拒绝**(WS 升级 403 + 轮询 400「session unknown」,界面显「实时通道未连接」)。
> 现象极具误导性——看起来像 WS 坏了,实为端口/来源不匹配。多个 Vite 实例常驻时先杀掉再起。

## 硬约束与注意事项

- **单 worker**:`uvicorn --workers 1` 是架构硬约束(run.py 统一入口)。事件通道是进程内实现
  (`EventEmitter`),审批等待协程、WS sid→用户映射都依赖单进程。多 worker 会静默断开跨进程事件桥接。
  扩容需先把事件通道换成 Redis pub/sub(明确列为后续项)。
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
- **认证**:JWT 有效期 24h(`AUTH_TOKEN_TTL_HOURS`),**无吊销机制**——改 `AUTH_JWT_SECRET` 即全员下线;
  改管理员密码需删 users 行后重启(启动时懒 seed)。

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

## dev 库清库 runbook(演示/交接前)

把 dev 库恢复到「零业务数据 + 幂等重 seed」的可展示状态——不清则驾驶舱/报表/通知铃铛满屏历史数据
(测试 tag 行与模拟流量长期累积,notifications 尤甚:按用户全量扇出)。

> ⚠️ 只清 `mae`(dev 库)。`multi_agent_ecommerce` 是**旧系统冻结库,任何情况下不要碰**
> (它不参与新系统运行,也不充当新库的备份)。

### 清理范围

13 张业务表 + 3 张 checkpoint 数据表:

```
users, products, customers, orders, conversations, tasks, approval_batches,
tickets, reply_templates, faq, market_intel, agent_tasks, notifications,
checkpoints, checkpoint_blobs, checkpoint_writes
```

**有意保留:**

| 表 | 保留理由 |
|---|---|
| `checkpoint_migrations` | LangGraph checkpoint 的 DDL 版本台账;清掉会让 `saver.setup()` 在下次启动时重放建表 DDL |
| `alembic_version` | Alembic 迁移台账;表结构归 Alembic 管——清库只 `TRUNCATE`,不做任何 DDL |

### 执行

```bash
docker compose exec postgres psql -U postgres mae -c "TRUNCATE TABLE \
  users, products, customers, orders, conversations, tasks, approval_batches, \
  tickets, reply_templates, faq, market_intel, agent_tasks, notifications, \
  checkpoints, checkpoint_blobs, checkpoint_writes RESTART IDENTITY CASCADE;"
```

`RESTART IDENTITY` 复位自增序列(id 从 1 重计),`CASCADE` 连带清外键引用行。

### 重 seed

```bash
docker compose restart app
```

启动 lifespan 幂等重 seed:

- **管理员**:`ensure_admin_user`(core/auth.py)——按 `AUTH_ADMIN_USERNAME` 建行,密码取 `AUTH_ADMIN_PASSWORD`(留空则跳过,登录不可用)
- **演示买家**:张伟 / zhangwei@example.com(`ensure_demo_buyers`,core/customer_store.py;仅 `ENVIRONMENT=dev`)

验收(期望 `users ≥ 1` 含 admin、customers 含张伟):

```bash
docker compose exec postgres psql -U postgres mae -c \
  "SELECT username FROM users; SELECT name, email FROM customers;"
```

### 清库后灌演示底料(空库演示必做)

空库没有商品,审批/下单链路无对象可动。演示商品集在 `docs/demo-data/products.csv`
(12 件,含售罄/严重不足/偏低/接近安全线各档,便于展示库存告警与低库存聚合):
驾驶舱左栏「CSV 数据导入」选「商品(sku 幂等,落草稿)」上传 → 商品落 draft,
上架经任务走审批护栏(顺带验收批量审批打包)。买家 张伟 已由 seed 提供,模拟流量即可下单。

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

## 已知取舍与待办(逐条落点)

| 事项 | 落点 |
|---|---|
| 全员平权审批:任何登录者可通过/拒绝,`decided_by`/`comment` 审计兜底 | ADR-0005「前端」节:登录平权无角色 |
| JWT 24h、无吊销:改 `AUTH_JWT_SECRET` 全员下线 | 本手册「硬约束与注意事项 → 认证」 |
| 审批批次无自动过期清扫(`approval_ttl_hours` 预留) | 本手册「硬约束与注意事项 → 审批机制」 |
| 前端尚无生产托管(开发态 Vite 代理,部署形态待定) | ADR-0005「部署」节:前端静态托管未落地 |
| 下单入口 REST `/api/orders` 保留且需认证,供模拟流量使用 | ADR-0005「业务强化」节:数据入口 |
| 买家前台维持移除(旧「演示买家前台直购」叙述已过时) | ADR-0005「被修订/取代的既有决策」节:ADR-0003 条 |
