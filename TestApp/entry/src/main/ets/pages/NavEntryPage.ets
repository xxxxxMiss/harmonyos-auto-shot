// 导航入口页：演示三种 NavDestination 路由场景
// 场景1：不同列表项 → 同一个 NavDetailPage，携带不同 index 参数
// 场景2：按指定条件（列表项类型）→ 进入不同路由（NavRouteA / NavRouteB）
// 场景3：多语句函数条件 + 对象数组 + 函数返回数组 + 跨文件 import
// NavPathStack 是全局类型，无需 import
import { isPremiumItem, getRouteItems, RouteItem, isVipLevel, isEvenIndexed } from '../common/RouteUtil';

interface NavDetailParam {
  index: number
}

interface NavRouteParam {
}

@Entry
@Component
struct NavEntryPage {
  private pathStack: NavPathStack = new NavPathStack()
  // 场景2：用类型决定进哪个路由
  private routes: string[] = ['A', 'B', 'A', 'B']

  build() {
    Navigation(this.pathStack) {
      Scroll() {
        Column({ space: 16 }) {
          Text($r('app.string.nav_entry_title'))
            .fontSize(24)
            .fontWeight(FontWeight.Bold)
            .margin({ top: 64 })
          Text($r('app.string.nav_entry_hint'))
            .fontSize(14)
            .fontColor('#999999')
        // 场景1：同路由不同参数（3 个列表项都进 NavDetailPage，index 0/1/2）
        List({ space: 10 }) {
          ForEach([0, 1, 2], (idx: number) => {
            ListItem() {
              Text(`详情项${idx}`)
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#E8F4FD')
                .borderRadius(8)
                .onClick(() => {
                  this.pathStack.pushPathByName('NavDetailPage', { index: idx } as NavDetailParam)
                })
            }
          }, (idx: number) => `d${idx}`)
        }
        .width('94%')
        // 场景2：不同条件进不同路由（按类型 A/B）
        List({ space: 10 }) {
          ForEach(this.routes, (type: string, idx: number) => {
            ListItem() {
              Text(`路由项${type}${idx}`)
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#FDECEC')
                .borderRadius(8)
                .onClick(() => {
                  if (type === 'A') {
                    this.pathStack.pushPathByName('NavRouteAPage', {} as NavRouteParam)
                  } else {
                    this.pathStack.pushPathByName('NavRouteBPage', {} as NavRouteParam)
                  }
                })
            }
          }, (type: string, idx: number) => `r${idx}`)
        }
        .width('94%')
        // 场景3：多语句函数条件 + 对象数组（函数返回）+ 跨文件 import
        List({ space: 10 }) {
          ForEach(getRouteItems(), (item: RouteItem, idx: number) => {
            ListItem() {
              Text(`高级项${item.kind}${idx}`)
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#E8FDE8')
                .borderRadius(8)
                .onClick(() => {
                  if (isPremiumItem(item)) {
                    this.pathStack.pushPathByName('NavRouteAPage', {} as NavRouteParam)
                  } else {
                    this.pathStack.pushPathByName('NavRouteBPage', {} as NavRouteParam)
                  }
                })
            }
          }, (item: RouteItem, idx: number) => `p${idx}`)
        }
        .width('94%')
        // 场景4：枚举常量路由判断（isVipLevel 内部用 ItemLevel.Premium 枚举）
        List({ space: 10 }) {
          ForEach(getRouteItems(), (item: RouteItem, idx: number) => {
            ListItem() {
              Text(`VIP项${item.kind}${idx}`)
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#FDFDE8')
                .borderRadius(8)
                .onClick(() => {
                  if (isVipLevel(item)) {
                    this.pathStack.pushPathByName('NavRouteAPage', {} as NavRouteParam)
                  } else {
                    this.pathStack.pushPathByName('NavRouteBPage', {} as NavRouteParam)
                  }
                })
            }
          }, (item: RouteItem, idx: number) => `v${idx}`)
        }
        .width('94%')
        // 场景5：字符串方法（startsWith）+ 算术（% 取模）路由判断
        List({ space: 10 }) {
          ForEach(getRouteItems(), (item: RouteItem, idx: number) => {
            ListItem() {
              Text(`偶数项${item.kind}${idx}`)
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#E8E8FD')
                .borderRadius(8)
                .onClick(() => {
                  if (isEvenIndexed(item, idx)) {
                    this.pathStack.pushPathByName('NavRouteAPage', {} as NavRouteParam)
                  } else {
                    this.pathStack.pushPathByName('NavRouteBPage', {} as NavRouteParam)
                  }
                })
            }
          }, (item: RouteItem, idx: number) => `e${idx}`)
        }
        .width('94%')
        // 场景6：三元动态路由名（P3-b）—— pushPathByName(cond ? 'A' : 'B')
        List({ space: 10 }) {
          ForEach(this.routes, (type: string, idx: number) => {
            ListItem() {
              Text(`三元项${type}${idx}`)
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#FDE8E8')
                .borderRadius(8)
                .onClick(() => {
                  this.pathStack.pushPathByName(type === 'A' ? 'NavRouteAPage' : 'NavRouteBPage', {} as NavRouteParam)
                })
            }
          }, (type: string, idx: number) => `t${idx}`)
        }
        .width('94%')
        // 场景7：三元条件文案（P3-b）—— Text(cond ? '文案A' : '文案B')
        List({ space: 10 }) {
          ForEach(this.routes, (type: string, idx: number) => {
            ListItem() {
              Text(type === 'A' ? '三元文案A' : '三元文案B')
                .fontSize(18)
                .width('100%')
                .padding(24)
                .backgroundColor('#E8FDE8')
                .borderRadius(8)
                .onClick(() => {
                  this.pathStack.pushPathByName(type === 'A' ? 'NavRouteAPage' : 'NavRouteBPage', {} as NavRouteParam)
                })
            }
          }, (type: string, idx: number) => `c${idx}`)
        }
          .width('94%')
        }
        .width('100%')
        .alignItems(HorizontalAlign.Center)
      }
      .scrollable(ScrollDirection.Vertical)
      .width('100%')
      .height('100%')
    }
  }
}

