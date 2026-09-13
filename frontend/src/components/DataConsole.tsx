import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useSocket } from '../hooks/useSocket';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { ORDER_STATUS_LABELS, PRODUCT_STATUS_LABELS } from '../labels';
import { fetchOrders, fetchProducts } from '../services/console';
import { EventType, OrderListItem, ProductListItem } from '../types/events';

// 模块级常量:useSocket 依赖引用稳定,避免每次渲染重连
const REFRESH_EVENTS = [EventType.NOTIFICATION_CREATED];

const ORDER_PAGE_SIZE = 50;

type Tab = 'products' | 'orders';
type ProductSort = 'latest' | 'stock';

const PRODUCT_SORTS: Array<{ value: ProductSort; label: string }> = [
  { value: 'latest', label: '最新导入在前' },
  { value: 'stock', label: '库存升序(缺货优先)' },
];

const ORDER_STATUS_OPTIONS = Object.entries(ORDER_STATUS_LABELS);

const inputClass =
  'rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand/25';

function AskAgentButton({
  prompt,
  onAsk,
}: {
  prompt: string;
  onAsk: (prompt: string) => void;
}) {
  return (
    <button
      onClick={() => onAsk(prompt)}
      title={`跳转驾驶舱并预填:${prompt}`}
      className="cursor-pointer rounded-lg border border-line px-2 py-1 text-[11px] text-ink-2 transition-colors hover:border-brand hover:bg-brand-soft hover:text-brand"
    >
      问 Agent
    </button>
  );
}

function Th({
  children,
  align = 'left',
}: {
  children: string;
  align?: 'left' | 'right';
}) {
  return (
    <th
      className={`sticky top-0 z-1 border-b border-line bg-surface px-3 py-2 text-[11px] font-medium text-ink-3 ${
        align === 'right' ? 'text-right' : 'text-left'
      }`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = 'left',
  className = '',
  title,
}: {
  children: ReactNode;
  align?: 'left' | 'right';
  className?: string;
  title?: string;
}) {
  return (
    <td
      title={title}
      className={`border-b border-line/60 px-3 py-1.5 ${align === 'right' ? 'text-right' : ''} ${className}`}
    >
      {children}
    </td>
  );
}

/** 商品表(spec #34):全量拉取 + 前端筛选/排序(商品量级小,筛选不必上服务端)。 */
function ProductTable({
  products,
  onAskAgent,
}: {
  products: ProductListItem[];
  onAskAgent: (prompt: string) => void;
}) {
  const [keyword, setKeyword] = useState('');
  const [status, setStatus] = useState('all');
  const [category, setCategory] = useState('all');
  const [sort, setSort] = useState<ProductSort>('latest');

  // issue #42(A5「按类目查询」):类目下拉选项取自当前数据(去重 + 中文排序)
  const categories = useMemo(
    () =>
      [...new Set(products.map((product) => product.category))].sort((a, b) =>
        a.localeCompare(b, 'zh'),
      ),
    [products],
  );

  const rows = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    const filtered = products.filter((product) => {
      const hit =
        needle === '' ||
        product.sku.toLowerCase().includes(needle) ||
        product.title.toLowerCase().includes(needle);
      return (
        hit &&
        (status === 'all' || product.status === status) &&
        (category === 'all' || product.category === category)
      );
    });
    return sort === 'stock'
      ? [...filtered].sort((a, b) => a.stock - b.stock)
      : filtered;
  }, [products, keyword, status, category, sort]);

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <input
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索 SKU / 标题"
          className={`w-[200px] ${inputClass}`}
        />
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          className={inputClass}
        >
          <option value="all">全部状态</option>
          {Object.entries(PRODUCT_STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          className={inputClass}
        >
          <option value="all">全部类目</option>
          {categories.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(event) => setSort(event.target.value as ProductSort)}
          className={inputClass}
        >
          {PRODUCT_SORTS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
        <span className="ml-auto text-[11px] text-ink-3">
          共 {rows.length} 件
          {rows.length !== products.length && ` / 全部 ${products.length}`}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <Th>SKU</Th>
              <Th>标题</Th>
              <Th align="right">价格</Th>
              <Th>类目</Th>
              <Th>状态</Th>
              <Th align="right">库存</Th>
              <Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((product) => {
              const low = product.stock < product.alertThreshold;
              return (
                <tr key={product.id} className="hover:bg-surface-2">
                  <Td className="font-mono text-[11px] text-ink-2">
                    {product.sku}
                  </Td>
                  <Td>{product.title}</Td>
                  <Td align="right" className="tnum">
                    {product.price} {product.currency}
                  </Td>
                  <Td className="text-ink-2">{product.category}</Td>
                  <Td className="text-ink-2">
                    {PRODUCT_STATUS_LABELS[product.status] ?? product.status}
                  </Td>
                  <Td
                    align="right"
                    className={`tnum ${low ? 'text-st-approval' : ''}`}
                  >
                    {product.stock}
                    {low && (
                      <span className="ml-1.5 rounded-full bg-st-approval-bg px-1.5 py-0.5 text-[10px]">
                        低于阈值 {product.alertThreshold}
                      </span>
                    )}
                  </Td>
                  <Td>
                    <AskAgentButton
                      prompt={`查看商品 ${product.sku} ${product.title} 的库存与定价,给运营建议`}
                      onAsk={onAskAgent}
                    />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-ink-3">
            没有匹配的商品。可回驾驶舱用 CSV 导入。
          </div>
        )}
      </div>
    </>
  );
}

/** 订单表(spec #34):状态筛选与分页走服务端(订单表随导入/流量持续增长)。 */
function OrderTable({
  orders,
  products,
  total,
  status,
  offset,
  onStatus,
  onOffset,
  onAskAgent,
}: {
  orders: OrderListItem[];
  products: ProductListItem[];
  total: number;
  status: string;
  offset: number;
  onStatus: (status: string) => void;
  onOffset: (offset: number) => void;
  onAskAgent: (prompt: string) => void;
}) {
  // 商品名客户端拼装(后端不做 join;商品已全量加载)
  const byId = useMemo(
    () => new Map(products.map((product) => [product.id, product])),
    [products],
  );
  const pageEnd = Math.min(offset + orders.length, total);
  const hasPrev = offset > 0;
  const hasNext = pageEnd < total;

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <select
          value={status}
          onChange={(event) => onStatus(event.target.value)}
          className={inputClass}
        >
          <option value="all">全部状态</option>
          {ORDER_STATUS_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="ml-auto text-[11px] text-ink-3">
          {total === 0
            ? '共 0 单'
            : `第 ${offset + 1}–${pageEnd} / 共 ${total} 单`}
        </span>
        <button
          onClick={() => onOffset(Math.max(offset - ORDER_PAGE_SIZE, 0))}
          disabled={!hasPrev}
          className="cursor-pointer rounded-lg border border-line px-2.5 py-1 text-xs text-ink-2 transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
        >
          上一页
        </button>
        <button
          onClick={() => onOffset(offset + ORDER_PAGE_SIZE)}
          disabled={!hasNext}
          className="cursor-pointer rounded-lg border border-line px-2.5 py-1 text-xs text-ink-2 transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
        >
          下一页
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr>
              <Th>订单号</Th>
              <Th>商品</Th>
              <Th>买家</Th>
              <Th align="right">金额</Th>
              <Th>状态</Th>
              <Th align="right">汇率快照</Th>
              <Th>创建时间</Th>
              <Th>操作</Th>
            </tr>
          </thead>
          <tbody>
            {orders.map((order) => {
              const product = byId.get(order.productId);
              return (
                <tr key={order.id} className="hover:bg-surface-2">
                  <Td className="font-mono text-[11px]">
                    {order.reference ?? `#${order.id}`}
                  </Td>
                  <Td title={product?.sku}>
                    {product
                      ? `${product.title} · ${product.sku}`
                      : `商品 #${order.productId}`}
                  </Td>
                  <Td className="text-ink-2">
                    {order.customerId === null ? '—' : `#${order.customerId}`}
                  </Td>
                  <Td align="right" className="tnum">
                    {order.totalAmount} {order.currency}
                  </Td>
                  <Td className="text-ink-2">
                    {ORDER_STATUS_LABELS[order.status] ?? order.status}
                  </Td>
                  <Td align="right" className="tnum">
                    {order.fxRate === null ? (
                      <span className="rounded-full bg-st-approval-bg px-1.5 py-0.5 text-[10px] text-st-approval">
                        待核
                      </span>
                    ) : (
                      order.fxRate
                    )}
                  </Td>
                  <Td className="tnum text-[11px] text-ink-3">
                    {order.createdAt
                      ? order.createdAt.slice(0, 16).replace('T', ' ')
                      : '—'}
                  </Td>
                  <Td>
                    <AskAgentButton
                      prompt={`查一下订单 ${order.reference ?? `#${order.id}`} 的状态与履约情况`}
                      onAsk={onAskAgent}
                    />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {orders.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-ink-3">
            没有匹配的订单。
          </div>
        )}
      </div>
    </>
  );
}

/**
 * 数据台(ADR-0006 / spec #34):商品与订单的**只读**视图。
 *
 * 边界:不含任何写操作——对外状态变更仍走对话 + 审批护栏(ADR-0006 明文,不得静默扩张);
 * 行级「问 Agent」只跳转驾驶舱并预填 Manager 输入,不代发、不切会话。
 */
export function DataConsole({
  onAskAgent,
}: {
  onAskAgent: (prompt: string) => void;
}) {
  const [tab, setTab] = useState<Tab>('products');
  const [products, setProducts] = useState<ProductListItem[]>([]);
  const [orders, setOrders] = useState<OrderListItem[]>([]);
  const [orderTotal, setOrderTotal] = useState(0);
  const [orderStatus, setOrderStatus] = useState('all');
  const [orderOffset, setOrderOffset] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshProducts = useCallback(() => {
    fetchProducts()
      .then((rows) => {
        setProducts(rows);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  const refreshOrders = useCallback(() => {
    fetchOrders({
      status: orderStatus === 'all' ? undefined : orderStatus,
      limit: ORDER_PAGE_SIZE,
      offset: orderOffset,
    })
      .then((body) => {
        setOrders(body.orders);
        setOrderTotal(body.total);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => setLoaded(true));
  }, [orderStatus, orderOffset]);

  useEffect(() => {
    refreshProducts();
  }, [refreshProducts]);
  useEffect(() => {
    refreshOrders();
  }, [refreshOrders]);
  // 审批 apply / 导入 / 下单都会改这两张表:订阅任务终态与通知广播,无轮询
  const refreshAll = useCallback(() => {
    refreshProducts();
    refreshOrders();
  }, [refreshProducts, refreshOrders]);
  useTaskTerminalEvents(refreshAll);
  useSocket(REFRESH_EVENTS, refreshAll);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-4">
      <div className="flex flex-wrap items-baseline gap-3 rounded-card border border-line bg-surface px-4 py-3 shadow-card">
        <h2 className="m-0 text-[15px] font-semibold">数据台</h2>
        <span className="text-[11px] text-ink-3">
          只读视图:对外状态变更仍走对话 + 审批护栏;行级「问
          Agent」跳转驾驶舱并预填,不代发
        </span>
        <nav className="ml-auto flex gap-1">
          {(
            [
              { key: 'products', label: '商品' },
              { key: 'orders', label: '订单' },
            ] as Array<{ key: Tab; label: string }>
          ).map((item) => (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              className={`cursor-pointer rounded-lg px-3.5 py-1.5 text-[13px] transition-colors ${
                tab === item.key
                  ? 'bg-brand-soft font-medium text-brand'
                  : 'text-ink-2 hover:bg-surface-2'
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>
        {error && <span className="text-xs text-st-failed">{error}</span>}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-card border border-line bg-surface shadow-card">
        {!loaded && !error && (
          <div className="px-4 py-10 text-center text-[13px] text-ink-3">
            加载中…
          </div>
        )}
        {loaded && tab === 'products' && (
          <ProductTable products={products} onAskAgent={onAskAgent} />
        )}
        {loaded && tab === 'orders' && (
          <OrderTable
            orders={orders}
            products={products}
            total={orderTotal}
            status={orderStatus}
            offset={orderOffset}
            onStatus={(next) => {
              setOrderStatus(next);
              setOrderOffset(0);
            }}
            onOffset={setOrderOffset}
            onAskAgent={onAskAgent}
          />
        )}
      </div>
    </div>
  );
}
