import { useCallback, useEffect, useState } from 'react';
import { useSocket } from '../hooks/useSocket';
import { useTaskTerminalEvents } from '../hooks/useTaskTerminalEvents';
import { fetchFxCard } from '../services/fx';
import { EventType, FxCardPayload } from '../types/events';

// 模块级常量:useSocket 依赖引用稳定,避免每次渲染重连
const REFRESH_EVENTS = [EventType.NOTIFICATION_CREATED];

const TREND_WIDTH = 132;
const TREND_HEIGHT = 34;
const TREND_PAD = 3;
const DAY_MS = 86_400_000;

const pad2 = (value: number) => String(value).padStart(2, '0');

/** 时刻 → 「09-12 08:30」(本地时区显示;后端给 ISO)。 */
function formatMoment(iso: string): string {
  const at = new Date(iso);
  return `${pad2(at.getMonth() + 1)}-${pad2(at.getDate())} ${pad2(at.getHours())}:${pad2(at.getMinutes())}`;
}

/** 「YYYY-MM-DD」→ UTC 日序号(与后端日期分桶口径一致,不受本地时区影响)。 */
function dayNumber(date: string): number {
  return Math.floor(Date.parse(`${date}T00:00:00Z`) / DAY_MS);
}

/**
 * 走势折线(spec #34):近 N 日按日快照,横轴按**日期**定位(不是按点序号)。
 *
 * 只连相邻日(无单日留断点)——不跨空档连线,不伪造中间值;窗口外/无成交的日不落点。
 */
function TrendLine({ trend }: { trend: FxCardPayload['trend'] }) {
  const points = trend.points;
  if (points.length === 0) {
    return (
      <div className="flex h-[34px] items-center text-[11px] text-ink-3">
        近 {trend.windowDays} 日暂无成交快照
      </div>
    );
  }

  const today = Math.floor(Date.now() / DAY_MS);
  const span = Math.max(trend.windowDays - 1, 1);
  // 窗口外的点(边界跨日)夹到两端,不越界出图
  const offsets = points.map((point) =>
    Math.min(Math.max(today - dayNumber(point.date), 0), span),
  );
  const rates = points.map((point) => Number(point.rate));
  const min = Math.min(...rates);
  const max = Math.max(...rates);
  const spread = max - min;
  const xAt = (offset: number) =>
    TREND_PAD + ((span - offset) / span) * (TREND_WIDTH - TREND_PAD * 2);
  const yAt = (rate: number) =>
    spread === 0
      ? TREND_HEIGHT / 2
      : TREND_HEIGHT -
        TREND_PAD -
        ((rate - min) / spread) * (TREND_HEIGHT - TREND_PAD * 2);

  const nodes = points.map((point, index) => ({
    x: xAt(offsets[index]),
    y: yAt(rates[index]),
    offset: offsets[index],
    point,
  }));
  // 只连相邻日:线段的两个端点日序号差 1,空档处自然断开
  const segments: Array<{
    from: (typeof nodes)[number];
    to: (typeof nodes)[number];
  }> = [];
  for (let index = 0; index < nodes.length - 1; index += 1) {
    if (nodes[index].offset - nodes[index + 1].offset === 1) {
      segments.push({ from: nodes[index], to: nodes[index + 1] });
    }
  }

  return (
    <svg
      viewBox={`0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`}
      className="h-[34px] w-full"
      role="img"
      aria-label={`近 ${trend.windowDays} 日汇率走势`}
    >
      {segments.map(({ from, to }) => (
        <line
          key={`${from.point.date}-${to.point.date}`}
          x1={from.x}
          y1={from.y}
          x2={to.x}
          y2={to.y}
          className="stroke-brand"
          strokeWidth={1.5}
          strokeLinecap="round"
        />
      ))}
      {nodes.map(({ x, y, point }) => (
        <circle key={point.date} cx={x} cy={y} r={2.2} className="fill-brand">
          <title>{`${point.date} · ${point.rate}`}</title>
        </circle>
      ))}
    </svg>
  );
}

/** 汇率卡片(ADR-0006 / spec #34):当期汇率(基准 CNY)+ 缓存时刻 + 近 7 日成交快照走势。 */
export function FxCard() {
  const [payload, setPayload] = useState<FxCardPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetchFxCard()
      .then((row) => {
        setPayload(row);
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);
  // 订单随任务 apply / REST 下单产生,两条通道都订阅(与 BusinessSnapshot 同款,无轮询)
  useTaskTerminalEvents(refresh);
  useSocket(REFRESH_EVENTS, refresh);

  const sourceLabel =
    payload?.source === 'cache'
      ? '缓存'
      : payload?.source === 'live'
        ? '实时'
        : null;

  return (
    <div className="flex w-[264px] shrink-0 flex-col gap-2 rounded-card border border-line bg-surface px-4 py-3 shadow-card">
      <div className="flex items-baseline gap-1.5">
        <span className="text-[11px] text-ink-3">汇率</span>
        {payload && (
          <span className="font-mono text-[11px] text-ink-3">
            {payload.currency}→{payload.base}
          </span>
        )}
        <span className="ml-auto text-[10px] text-ink-3">
          {sourceLabel && payload?.cachedAt
            ? `${sourceLabel} ${formatMoment(payload.cachedAt)}`
            : sourceLabel || '—'}
        </span>
      </div>

      {!payload && !error && (
        <span className="self-center text-xs text-ink-3">加载中…</span>
      )}
      {error && <span className="text-xs text-st-failed">{error}</span>}

      {payload && (
        <>
          {payload.rate === null ? (
            <>
              <span className="text-xl leading-none font-semibold text-st-approval">
                待核
              </span>
              <span className="text-[11px] text-ink-3">
                汇率不可用,下单将留空快照待人工核对
              </span>
            </>
          ) : (
            <>
              <span className="tnum text-xl leading-none font-semibold">
                {payload.rate}
              </span>
              <span className="text-[11px] text-ink-3">
                1 {payload.currency} = {payload.rate} {payload.base}
              </span>
            </>
          )}
          <TrendLine trend={payload.trend} />
          <span className="text-[10px] text-ink-3">
            近 {payload.trend.windowDays} 日 · {payload.trend.points.length}{' '}
            个成交日(订单快照口径)
          </span>
        </>
      )}
    </div>
  );
}
