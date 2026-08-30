// 详情页：根据路由参数 index 渲染不同内容（验证"同路由不同参数"场景）
import { router } from '@kit.ArkUI';

interface DetailParams {
  index?: number
}

@Entry
@Component
struct DetailPage {
  @State index: number = -1

  aboutToAppear(): void {
    const params = router.getParams() as DetailParams
    if (params && params.index !== undefined) {
      this.index = params.index
    }
  }

  build() {
    Column({ space: 16 }) {
      Text($r('app.string.detail_title'))
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 64 })
      if (this.index === 0) {
        Text($r('app.string.detail_0')).fontSize(18)
      } else if (this.index === 1) {
        Text($r('app.string.detail_1')).fontSize(18)
      } else if (this.index === 2) {
        Text($r('app.string.detail_2')).fontSize(18)
      } else if (this.index === 3) {
        Text($r('app.string.detail_3')).fontSize(18)
      } else if (this.index === 4) {
        Text($r('app.string.detail_4')).fontSize(18)
      } else {
        Text($r('app.string.detail_default')).fontSize(18)
      }
      Button($r('app.string.back_home'))
        .width('80%')
        .margin({ top: 32 })
        .backgroundColor('#CCCCCC')
        .onClick(() => {
          router.back()
        })
    }
    .width('100%')
    .height('100%')
    .alignItems(HorizontalAlign.Center)
  }
}
