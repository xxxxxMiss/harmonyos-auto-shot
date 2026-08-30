// 跨文件路由判断工具：多语句函数体 + 对象数组条件
export interface RouteItem {
  kind: string
  level: number
  enabled: boolean
}

// 多语句 + if/else + 多 return + 局部变量
export function isPremiumItem(item: RouteItem): boolean {
  const kind = item.kind
  if (kind === 'premium') {
    return true
  }
  if (kind === 'vip') {
    const lvl = item.level
    return lvl >= 3
  }
  return false
}

// 函数返回数组：数据源是函数调用
export function getRouteItems(): RouteItem[] {
  const items: RouteItem[] = []
  for (let i = 0; i < 4; i++) {
    const item: RouteItem = { kind: i < 2 ? 'premium' : 'basic', level: i, enabled: true }
    items.push(item)
  }
  return items
}

// P2 增量：枚举 + 常量（跨文件符号解析，之前手写符号表无法覆盖）
export enum ItemLevel {
  Basic = 0,
  Premium = 3,
  VIP = 5
}

export const VIP_THRESHOLD = 3

// 用枚举常量做路由判断
export function isVipLevel(item: RouteItem): boolean {
  return item.level >= ItemLevel.Premium
}

// P0 增量：字符串方法（startsWith）+ 算术（% 取模）
export function isEvenIndexed(item: RouteItem, idx: number): boolean {
  return idx % 2 === 0 && item.kind.startsWith('pre')
}

// P0 增量：数组方法（some）
export function hasPremiumKind(items: RouteItem[]): boolean {
  return items.some((it: RouteItem) => it.kind === 'premium')
}
