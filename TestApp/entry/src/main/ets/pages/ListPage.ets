// 长列表页：第 21 项是目标文案，验证"滚动到视野"能力
import { router } from '@kit.ArkUI';

@Entry
@Component
struct ListPage {
  @State items: string[] = []

  aboutToAppear(): void {
    const arr: string[] = []
    for (let i = 0; i < 30; i++) {
      arr.push(`列表第${i + 1}项`)
    }
    this.items = arr
  }

  build() {
    Column() {
      Text($r('app.string.list_title'))
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 48, bottom: 16 })
      List({ space: 10 }) {
        ForEach(this.items, (item: string, idx: number) => {
          ListItem() {
            Text(idx === 20 ? $r('app.string.list_target') : item)
              .fontSize(18)
              .width('100%')
              .padding(24)
              .backgroundColor('#F2F3F5')
              .borderRadius(8)
          }
        }, (_item: string, idx: number) => idx.toString())
      }
      .layoutWeight(1)
      .width('94%')
      Button($r('app.string.back_home'))
        .width('80%')
        .margin({ top: 12, bottom: 24 })
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
