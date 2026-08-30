// NavDestination 详情页：同路由不同参数（params.index 决定渲染内容）
// 通过 NavPathStack.pushPathByName('NavDetailPage', {index: idx}) 跳转
interface NavDetailParams {
  index?: number
}

@Component
export struct NavDetailPage {
  @State index: number = -1

  build() {
    NavDestination() {
      Column({ space: 16 }) {
        Text($r('app.string.nav_detail_title'))
          .fontSize(24)
          .fontWeight(FontWeight.Bold)
          .margin({ top: 64 })
        if (this.index === 0) {
          Text($r('app.string.nav_detail_0')).fontSize(18)
        } else if (this.index === 1) {
          Text($r('app.string.nav_detail_1')).fontSize(18)
        } else if (this.index === 2) {
          Text($r('app.string.nav_detail_2')).fontSize(18)
        } else {
          Text($r('app.string.nav_detail_0')).fontSize(18)
        }
      }
      .width('100%')
      .height('100%')
      .alignItems(HorizontalAlign.Center)
    }
    .title('详情')
    .onReady((ctx: NavDestinationContext) => {
      const idx = (ctx.pathInfo.param as NavDetailParams)?.index
      if (idx !== undefined) {
        this.index = idx
      }
    })
  }
}

@Builder
export function NavDetailPageBuilder() {
  NavDetailPage()
}
