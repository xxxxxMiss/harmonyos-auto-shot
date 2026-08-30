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
