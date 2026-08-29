// 业务 i18n 封装：resourceManager 按名取值（R5 场景；key 传变量则为 R4 动态引用）
export class I18n {
  static t(name: string): string {
    return getContext(this)?.resourceManager?.getStringByNameSync(name) ?? name
  }
}

// 动态引用示例：key 来自变量，regex MVP 无法解析，记为 dynamic（阶段二 AST 常量传播/函数摘要处理）
const DYNAMIC_KEY = 'unused_string'
export function dynText(): string {
  return I18n.t(DYNAMIC_KEY)
}
