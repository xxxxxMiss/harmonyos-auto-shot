// 导航入口页：演示三种 NavDestination 路由场景
// 场景1：不同列表项 → 同一个 NavDetailPage，携带不同 index 参数
// 场景2：按指定条件（列表项类型）→ 进入不同路由（NavRouteA / NavRouteB）
// 场景3：多语句函数条件 + 对象数组 + 函数返回数组 + 跨文件 import
// NavPathStack 是全局类型，无需 import
import { isPremiumItem, getRouteItems, RouteItem } from '../common/RouteUtil';

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
      }
      .width('100%')
      .height('100%')
      .alignItems(HorizontalAlign.Center)
    }
  }
}

