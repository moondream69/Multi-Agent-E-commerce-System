# 运维手册:内部卖家工具(局域网部署)

> 定位:2-5 人跨境卖家团队内部工具。单容器(Docker Compose)同源托管前端静态产物与 API——**浏览器直接开 `http://<主机>:3000`**;开发态另可用 Vite 代理(见快速启动第 5 步)。

## 快速启动

```bash
# 1. 配置环境(仓库根 .env 单一真源,compose 共读)
cp .env.example .env
#   - LLM_API_KEY:DeepSeek API Key
#   - AUTH_JWT_SECRET:openssl rand -hex 32 生成(生产必改)
#   - AUTH_ADMIN_PASSWORD:初始管理员密码(启动时懒 seed;改密码需删 users 行后重启)
#   - EMBEDDING_API_URL:仅本机 uv 开发读取(默认 http://localhost:11434);app 容器内由 compose 固定为 http://ollama:11434
#   - LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY:留空即禁用观测(no-op);自托管实例默认 http://localhost:3001

# 2. 启动基础设施 + 后端(首次自动迁移;管理员与演练买家幂等 seed)
docker compose up -d

# 3. 容器内 Ollama(app 容器 embedding 固定指向它,容器化运行须起;本机 uv 开发走宿主机 Ollama)
docker compose --profile embed up -d ollama
docker compose exec ollama ollama pull bge-m3

# 4. 可选:开启模拟流量(每 300 秒一轮随机买家行为,烧真 DeepSeek token)
docker compose --profile sim up -d

# 5. 前端:生产形态已随 app 镜像托管——浏览器开 http://localhost:3000 即可(前端 + API 同源)
#    改前端代码须 docker compose build app(镜像内是构建产物,无挂载);要 HMR 才用下面的 Vite:
cd frontend && npm run dev   # 开发态:5173,/api 与 /socket.io 代理到 3000
```

> ⚠️ **dev server 必须落在 5173**:后端 `cors_origins` 默认只放行 `http://localhost:5173`。
> 5173 被占时 Vite 会自动跳到 5174+,此时 REST 仍通(代理直连),但 **socket.io 握手会被
> 后端按来源拒绝**(WS 升级 403 + 轮询 400「session unknown」,界面显「实时通道未连接」)。
> 现象极具误导性——看起来像 WS 坏了,实为端口/来源不匹配。多个 Vite 实例常驻时先杀掉再起。

> ⚠️ **换 origin 访问(局域网 IP / 域名)须列入 `CORS_ORIGINS`**:单端口同源后 REST 不再跨域,
> 但 socket.io 对**每个带 Origin 的握手**都校验来源——用 `http://192.168.x.x:3000` 打开而白名单
> 只有 localhost 时,症状与上条同款(页面能开、事件流不动)。**不可置空绕过**:空值等于跳过校验,
> 而事件是全局广播、无房间、无鉴权。

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
- **演示买家**:张伟 / zhangwei@example.com(`ensure_demo_buyers`,db/customer_store.py;仅 `ENVIRONMENT=dev`)

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

> ⚠️ **演示前须知(issue #36 会话实测补记,两条都实测踩过)**:
> ① **投诉/工单演示需要库里有订单**——空库 + 只导商品 CSV 时,客服 Agent 查不到订单会如实拒建单
> (自述「缺少必要输入参数,非业务不可处理」),起草工作台显示「未结 0」。先跑一轮模拟流量
> (`cd python-backend && uv run python -m python_backend.simulator --once`)补出订单与工单再演。
> ② **演示下架要挑「在售」商品**——商品 CSV 导入后全为 `draft`,此时说「把商品 N 下架」是空操作,
> Agent 会拒绝提交无意义批次(对 draft 商品下架无可撤销的对外可见状态)。先上架(如 1、4、9),
> 再下架其中之一,才出审批批次。

## 试运行数据 provisioning(合成数据集,确定性可复现)

> 2026-09-13 大档(500 商品 / 200 买家 / 2000 订单)全链路实测:导入零错误、重导幂等、
> 报表/汇率走势成形。**定位**:规模化演练数据——ADR-0005「真实数据 CSV 导入」一环仍欠,
> 真实数据到手后走同一路径导入即可。

```bash
# 1. 生成(确定性 seed=20260913;输出 docs/demo-data/synth-{products,customers,orders}.csv,不覆盖演示集)
cd python-backend && uv run python scripts/gen_synth_data.py

# 2. 按序导入(驾驶舱导入卡或直接 POST /api/import/*;顺序硬约束:商品→买家→订单)
#    导入报告 created=500/200/2000,零 errors;重导一次全 skipped(幂等键 sku/email/reference)

# 3. 批量激活:商品入库为 draft,上架走审批需 ~11 个 Agent 任务(切片 ≤10 步),
#    故试运行数据以 provisioning 直写激活为主(与订单 CSV 同样绕开下单链路的既有先例),
#    保留末 25 件 draft 供「上架审批」演示:
docker compose exec postgres psql -U postgres mae -c \
  "UPDATE products SET status='active' WHERE id NOT IN (SELECT id FROM products ORDER BY id DESC LIMIT 25);"

# 4. 时间轴与汇率快照回填(导入入口按设计不取快照/统一 now();合成口径:近 14 天铺开、
#    当日汇率 ±0.5% 正弦曲线——真实数据导入时改为按当日真实汇率回填,勿造波动):
docker compose exec postgres psql -U postgres mae -c "
UPDATE orders SET created_at = now() - (interval '1 day' * (id % 14));
UPDATE orders SET fx_base_currency='CNY', fx_rate = CASE currency
    WHEN 'CNY' THEN 1 WHEN 'USD' THEN 6.73078865 WHEN 'EUR' THEN 7.80152910 WHEN 'GBP' THEN 9.08884344
  END * (1 + 0.005 * sin(2*pi()*(id%14)/7)) WHERE fx_rate IS NULL;"

# 5. 验收:五档分布(38/63/52/86/261)、七态齐全、/api/reports/summary 成交额非零且缺汇率待核=0
```

> ⚠️ 回填 SQL 的汇率常量按回填当日实际值替换(取 `/api/fx` 或 `default_fx()`);上面是 2026-09-13 快照。

## 生产切换清单(试运行前)

> 2026-09-13 本机**同库**实切演练已跑通(库未清、dev 遗留在场,正好覆盖「同库跨剖面」最难场景;
> 证据 `docs/handoffs/evidence-2026-09-13-b15-profile/`)。
> **同日第二轮**:清库重 seed + 合成数据集在库 + 强凭据轮换 + 局域网 origin,**六项验证二度全绿**;
> 证据 `docs/handoffs/evidence-2026-09-13-trial/`(含 dev 影子段 4 条在 prod 隐藏、dev 复现回归)。

**1. 库**:建议干净库(走上一节 runbook)——dev 遗留的任务/订单/通知在生产界面同样可见。
影子段不必再担心:prod 剖面审批中心自动过滤、补执行端点 403(验收 B15,issue #37)。

**2. 强凭据**(`.env`):`AUTH_JWT_SECRET` 用 `openssl rand -hex 32` 重生成(改它 = 全员下线);
`AUTH_ADMIN_PASSWORD` 换强口令——⚠️ **懒 seed 只对空表生效,改密码须先删 users 行再重启**
(2026-09-13 实测三证:旧口令 401 / 新口令 200 / **持旧密钥自签的 token 401**——pyjwt 同时警告
旧密钥 20 字节低于 SHA256 推荐下限,正是要换的理由):

```bash
docker compose exec postgres psql -U postgres mae -c "DELETE FROM users WHERE username='admin';"
docker compose up -d app   # 重启时按新口令重 seed
```

**3. CORS_ORIGINS**:列出浏览器实际访问的全部 origin(局域网部署即 `http://<局域网IP>:3000`)。
⚠️ **`127.0.0.1` 与 `localhost` 不等价**(2026-09-13 实测:前者 400 / 后者 200)——未列出的
origin 在 socket.io 握手被拒 400,而页面照常打开,症状是「登录页能进、实时通道不动」。访问地址一变即同步。
⚠️ 2026-09-13 实测经 `http://192.168.1.100:3000` 本机访问已通(握手 200、静态入口 200);
**第二设备实访(含 Windows 防火墙入站放行)尚未验证**——若另一台设备打不开,先查防火墙
入站规则(tcp 3000),再核对对应 origin 是否在白名单。

**4. 切剖面**:`ENVIRONMENT=prod docker compose up -d app`(shell 环境优先于 `.env` 插值;回退 = 去掉前缀重跑)。

**5. 关模拟流量**:别在 prod 下启 `--profile sim`(`ensure_demo_buyers` 亦仅 dev 生效)。

**6. 启用观测(Langfuse)**:`.env` 填 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`(登录 3001 建项目后生成);`LANGFUSE_HOST` 容器内已由 compose 固定为 `http://langfuse-server:3000`(本机 uv 开发才在 `.env` 指 `http://localhost:3001`);密钥留空即 no-op(当前 dev 部署即如此)。验证:登录 3001 可见任务 trace。

**7. 验证点**(对照实测):

| 项 | 命令 | 期望 |
|---|---|---|
| 剖面生效 | `curl -s localhost:3000/health` | `"environment":"prod"` |
| 影子段隐藏 | `GET /api/approvals`(带 token) | 批次 mode 集合不含 `shadow` |
| 补执行关闭 | `POST /api/threads/{tid}/shadow-batches/{bid}/execute` | 403「影子批次补执行仅演练剖面可用」 |
| 审批锁死 | 发高危任务(如下架在售商品) | `status=interrupted`,不落 shadow |
| 静态入口 | `curl -s -o /dev/null -w "%{http_code}" localhost:3000/` | 200(且含 `<div id="root">`) |
| 实时通道 | `curl -s -o /dev/null -w "%{http_code}" -H "Origin: http://<实际访问地址>" "http://<实际访问地址>/socket.io/?EIO=4&transport=polling"` | 200(400 = 该 origin 未列入 CORS_ORIGINS) |

## 审计 SQL(跑一天后评估)

```bash
docker compose exec postgres psql -U postgres mae
```

```sql
-- 任务失败率与分布
SELECT type, status, count(*) FROM tasks GROUP BY type, status ORDER BY type;

-- 审批批次(采纳率/决定人分布;decided_by = 登录用户名——resume 按钮与自然消息两条决定入口均落库,
-- 未带 token 的决定路径留空;幂等重放不覆盖首次决定人)
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
| 跨 origin 访问须手工维护 `CORS_ORIGINS`(局域网 IP/域名一变就要同步,否则实时通道静默失效) | 本手册「快速启动」第 5 步下第二条警告;`.env.example` 同注 |
| 下单入口 REST `/api/orders` 保留且需认证,供模拟流量使用 | ADR-0005「业务强化」节:数据入口 |
| 买家前台维持移除(旧「演示买家前台直购」叙述已过时) | ADR-0005「被修订/取代的既有决策」节:ADR-0003 条 |
