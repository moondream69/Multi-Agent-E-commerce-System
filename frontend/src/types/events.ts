// 契约真源:契约测试 python-backend/tests/test_contract.py 对照本文件断言
// 历史清理(spec #9):旧系统聊天响应三形状、Agent 事件系列与未消费 payload 接口已删除。

// —— WS 事件名(实况墙/审批通道/通知铃铛的订阅源)——

export const EventType = {
  TASK_CREATED: 'task.created',
  TASK_PLANNED: 'task.planned',
  TASK_INTERRUPTED: 'task.interrupted',
  TASK_COMPLETED: 'task.completed',
  TASK_FAILED: 'task.failed',
  SLICE_STARTED: 'slice.started',
  SLICE_COMPLETED: 'slice.completed',
  APPROVAL_REQUESTED: 'approval.requested',
  APPROVAL_DECIDED: 'approval.decided',
  NOTIFICATION_CREATED: 'notification.created',
  NOTIFICATION_READ: 'notification.read',
} as const;

export type EventType = (typeof EventType)[keyof typeof EventType];

export interface TaskLifecyclePayload {
  threadId: string;
  status: 'created' | 'interrupted' | 'completed' | 'failed';
  error?: string | null;
}

// —— 协作面板数据源(规划/分派轨迹透明):计划产出即广播,不必等任务终态 ——

export interface PlannedSlice {
  no: number;
  agent: string;
  description: string;
  dependsOn: number[];
  approvalPoints: string[];
}

export interface TaskPlannedPayload {
  threadId: string;
  slices: PlannedSlice[];
}

export interface SliceStartedPayload {
  threadId: string;
  sliceNo: number;
  agent: string;
}

// 切片终态:rejected > failed > completed(与图侧 _emit_completed 优先级一致)
export interface SliceCompletedPayload extends SliceStartedPayload {
  status: 'completed' | 'failed' | 'rejected';
}

// —— 切片式审批批次(spec #7:切片内同类高危动作打包真实参数快照,批内同进同退)——

export type ApprovalBatchStatus =
  'pending' | 'approved' | 'rejected' | 'expired' | 'shadow' | 'executed';

export interface ApprovalActionSnapshot {
  action: string;
  params: Record<string, unknown>;
  snapshot: Record<string, unknown> | null;
}

export interface ApprovalBatch {
  batchId: string;
  threadId: string;
  sliceNo: number;
  actionType: string;
  actions: ApprovalActionSnapshot[];
  status: ApprovalBatchStatus;
  mode: 'approval' | 'shadow';
  comment: string | null;
  result: Record<string, unknown> | null;
  runOutput: Record<string, unknown> | null;
}

// 一次 interrupt 携带切片全部批次(spec #7),逐批决定一次提交
export interface ApprovalRequestedPayload {
  threadId: string;
  sliceNo: number;
  agent: string;
  batches: ApprovalBatchRequested[];
}

export interface ApprovalBatchRequested {
  batchId: string;
  actionType: string;
  actions: ApprovalActionSnapshot[];
}

export interface ApprovalDecidedPayload {
  threadId: string;
  batchId: string;
  decision: 'approve' | 'reject';
  comment?: string | null;
}

// —— 审批列表信封(spec #34:GET /api/approvals 与按线程端点同构)——
// plans 为线程级旁挂(ADR-0005「审批单携带任务上下文 + 后续计划预览」):计划是线程属性,
// 挂批次会按批次重复;无任务行的线程不入表(前端按缺省不渲染计划区)。

export interface ThreadPlan {
  /** 任务原始需求(任务上下文) */
  request: string | null;
  /** 切片计划(与 TaskDetail.plan 同形,复用 SlicePlanSlice) */
  plan: { slices: SlicePlanSlice[] };
}

export interface ApprovalListResponse {
  approvals: ApprovalBatch[];
  plans: Record<string, ThreadPlan>;
}

// —— 通知铃铛(spec #9 A8/A9/A14:notification.created)——

// kind 已知集合:order_status / inventory_alert / fx_missing;未知 kind 前端归入兜底组
export interface NotificationMessage {
  notificationId: string;
  message: string;
  kind: string;
  orderId: number | null;
  timestamp: string;
}

// GET /api/notifications 响应(增量 8-T2):当前用户通知历史(每组最近 50 条)+ 全量未读计数
export interface NotificationFeed {
  notifications: NotificationMessage[];
  unread: number;
}

// —— 会话(A2 多会话:新建/切换/删除、标题截断 20 字、空白会话惰性落库)——

export interface ConversationMeta {
  sessionId: string;
  title: string;
  updatedAt: string;
  messageCount: number;
}

// 会话消息流(spec #20 A2 延伸:GET /api/conversations/{sessionId}/messages)
// 原序=落库序即时间序,服务端全量返回;展示方向由组件决定(驾驶舱取最新在前)。
export interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string | null;
  taskId: string | null;
}

export interface ConversationMessages {
  conversation: ConversationMeta;
  messages: ConversationMessage[];
}

// —— 动作元数据(spec #8:GET /api/actions,前端标签不再硬编码)——

export interface ActionMeta {
  action: string;
  risk: 'auto' | 'approval';
  label: string;
}

// —— 认证(spec #8 A1:POST /api/auth/login)——

export interface LoginResponse {
  token: string;
  username: string;
}

// —— 驾驶舱(spec #8:任务列表/详情端点)——

// POST /api/tasks 响应(发起任务;issue #26:顶层响应键驼峰化,对齐 TaskListItem/TaskDetail;终态四键/interrupted 二键)
export interface TaskCreated {
  threadId: string;
  status: string;
  error?: string | null;
  summary?: string | null;
}

export interface TaskListItem {
  threadId: string;
  sessionId: string;
  type: string;
  status: string;
  title: string;
  createdAt: string | null;
}

export interface SlicePlanSlice {
  no: number;
  agent: string;
  description: string;
  depends_on: number[];
  approval_points: string[];
}

export interface TaskDetail {
  threadId: string;
  sessionId: string;
  type: string;
  status: string;
  request: string | null;
  plan: { slices: SlicePlanSlice[] } | null;
  results: Record<string, unknown> | null;
  result: { summary?: string; error?: string } | null;
  batches: ApprovalBatch[];
  createdAt: string | null;
}

// —— 起草工作台(spec #8 B11/B19:POST /api/drafting)——

export interface DraftingEvidence {
  faq_hits: Array<{
    id: string;
    score: number;
    payload: Record<string, unknown>;
  }>;
  order: Record<string, unknown> | null;
  order_id: number | null;
  products: Array<{
    id: number;
    sku: string;
    title: string;
    price: string;
    currency: string;
    category: string;
    status: string;
    stock: number;
  }>;
  products_truncated: boolean;
}

export interface DraftingResponse {
  draft: string;
  evidence: DraftingEvidence;
  citations: Citation[];
}

// —— 引用小点(issue #51 / ADR-0007 C 段:答案带切块级引用,数据随回答一起下发;
//    #67 扩系统记录条目)——
// 编号即答案文本里的上标编号(同一文档 / 同一记录合并为一号);点开显示被引内容与完整溯源:
// 语料给各切块原文,系统记录(商品/订单)给记录标识与查询结果正文。

export interface CitationChunk {
  id: string; // 切块标识(如 faq-returns#6)
  score: number; // 本次检索得分
  section: string; // 章节或页码
  chunk_index: number | null; // 切块序号
  content: string; // 被引切块原文
}

/** 系统记录条目(#67:商品/订单的查库结果;不是语料切块,故没有 chunks)。 */
export interface CitationRecord {
  id: string; // 记录标识(如 product:82 / order:1042)
  content: string; // 查询结果正文(后端按证据字段拼好的可读行)
}

/** 语料切块引用(#51;后端自 #67 起显式落 kind,引入前落库的老载荷可缺——缺即本类)。 */
export interface CorpusCitation {
  number: number; // 上标编号(1 起,按文本内首次出现排)
  kind?: 'corpus';
  doc_id: string; // 文档标识(切块标识的「#」前缀)
  title: string;
  source: string; // 来源渠道
  published_at: string | null; // 发布日期
  chunks: CitationChunk[];
}

/** 系统记录引用(#67):商品/订单证据的标注(非语料切块,故无 chunks/文档溯源),正文在 record 里。 */
export interface RecordCitation {
  number: number; // 上标编号(1 起,按文本内首次出现排)
  kind: 'product' | 'order';
  title: string;
  source: string; // 系统查询结果
  record: CitationRecord;
}

export type Citation = CorpusCitation | RecordCitation;

// —— CSV 导入(spec #8 B8:POST /api/import/products|orders)——

export interface ImportReport {
  created: number;
  skipped: number;
  errors: Array<{ row: number; reason: string }>;
}

// —— 商品只读列表(spec #9:GET /api/products,模拟流量发现商品;spec #34:数据台盘货数据源)——

export interface ProductListItem {
  id: number;
  sku: string;
  title: string;
  price: string;
  currency: string;
  category: string;
  status: string;
  stock: number;
  alertThreshold: number;
}

// —— 订单只读列表(spec #34:GET /api/orders,数据台对账数据源;纯只读,ADR-0006 边界)——

export interface OrderListItem {
  id: number;
  /** CSV 导入幂等键;手工/REST 下单为空 → 前端回落 #id 显示 */
  reference: string | null;
  productId: number;
  customerId: number | null;
  status: string;
  totalAmount: string;
  currency: string;
  /** 落库汇率快照(基准 CNY);空 = 下单时汇率不可用,数据台显「待核」 */
  fxRate: string | null;
  fxBaseCurrency: string;
  platform: string | null;
  createdAt: string | null;
}

export interface OrderListResponse {
  orders: OrderListItem[];
  /** 同筛选条件下的总行数(服务端分页口径) */
  total: number;
}

// —— 汇率卡片(ADR-0006 / spec #34:GET /api/fx,驾驶舱)——
// rate=null = 汇率 API 与缓存双失效(前端显「待核」);cachedAt=null = 旧缓存无伴生时刻键;
// source:cache 缓存命中 / live 本次实时拉取 / null 基准币自身。走势 = 订单快照按日聚合。

export interface FxCardPayload {
  base: string;
  currency: string;
  rate: string | null;
  cachedAt: string | null;
  source: 'cache' | 'live' | null;
  trend: {
    windowDays: number;
    points: Array<{ date: string; rate: string }>;
  };
}

// —— 工单(A11 收口:GET /api/tickets + PATCH 结单)——

export interface TicketItem {
  ticketId: number;
  message: string;
  status: 'open' | 'closed';
  customerName: string | null;
  createdAt: string | null;
  resolvedAt: string | null;
}

// —— 经营快照(spec #11:GET /api/reports/summary,零 LLM 纯 SQL 聚合)——

export interface ReportSummary {
  orders: { byStatus: Record<string, number>; total: number };
  revenue: {
    windowDays: number;
    baseCurrency: string;
    amount: string;
    unconverted: number;
  };
  lowStock: Array<{
    productId: number;
    sku: string;
    title: string;
    stock: number;
    alertThreshold: number;
  }>;
  tickets: { open: number };
}
