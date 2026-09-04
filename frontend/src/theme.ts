// 一次性视觉升级的设计 token(顺手美化范围,不引组件库)
// 方向:工程仪表盘——细边框、极轻投影、mono 数据、语义状态灯
export const theme = {
  color: {
    brand: '#2563eb',
    brandSoft: '#eff6ff',
    bg: '#f6f7f9',
    surface: '#ffffff',
    border: '#e5e7eb',
    text: '#0f172a',
    textSecondary: '#64748b',
    textMuted: '#94a3b8',
    success: '#10b981',
    successBg: '#ecfdf5',
    warning: '#f59e0b',
    warningBg: '#fffbeb',
    danger: '#ef4444',
    dangerBg: '#fef2f2',
  },
  radius: { sm: 6, md: 10, full: 999 },
  shadow: { card: '0 1px 2px rgba(15, 23, 42, 0.05)' },
  font: {
    body: "system-ui, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    mono: "ui-monospace, 'SF Mono', 'Cascadia Code', Consolas, monospace",
  },
} as const;
