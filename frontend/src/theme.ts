// 设计 token(增量 4:三视图壳 + 审批中心)
// 方向:台账式内部工具——纸面暖灰底、墨色正文、松绿主色(上架绿)、mono 流水号、状态脊线
export const theme = {
  color: {
    brand: '#2E5D4F', // 深松绿:批准与品牌主色
    brandSoft: '#E8F0EB',
    bg: '#F6F4EF', // 纸面
    surface: '#FFFFFF',
    border: '#E4DFD5',
    text: '#1D1B17', // 墨
    textSecondary: '#6E675C',
    textMuted: '#9A9286',
    success: '#3F6B50', // 苔绿:已执行/已连接
    successBg: '#E9F1EB',
    warning: '#9A6B1D', // 琥珀:影子段
    warningBg: '#F5EDDB',
    danger: '#B3402C', // 赭红:拒绝
    dangerBg: '#F7E9E4',
  },
  radius: { sm: 6, md: 10, full: 999 },
  shadow: { card: '0 1px 2px rgba(29, 27, 23, 0.05)' },
  font: {
    body: "system-ui, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    mono: "ui-monospace, 'SF Mono', 'Cascadia Code', Consolas, monospace",
  },
} as const;
