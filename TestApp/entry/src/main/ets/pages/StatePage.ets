// 状态演示页：条件渲染（断网错误提示 / 空列表态），需点击触发后文案才显示
import { router } from '@kit.ArkUI';

@Entry
@Component
struct StatePage {
  @State offline: boolean = false
  @State showEmpty: boolean = false
  private items: string[] = ['数据项A', '数据项B', '数据项C']

  build() {
    Column({ space: 16 }) {
      Text($r('app.string.state_title'))
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 64 })
      Button($r('app.string.toggle_offline'))
        .width('80%')
        .onClick(() => {
          this.offline = !this.offline
        })
      if (this.offline) {
        Text($r('app.string.network_error'))
          .fontSize(16)
          .fontColor(Color.Red)
          .padding(16)
          .backgroundColor('#FDECEC')
          .borderRadius(8)
      }
      Button($r('app.string.toggle_empty'))
        .width('80%')
        .onClick(() => {
          this.showEmpty = !this.showEmpty
        })
      if (this.showEmpty) {
        Text($r('app.string.empty_list'))
          .fontSize(16)
          .fontColor('#999999')
          .padding(32)
      } else {
        List({ space: 8 }) {
          ForEach(this.items, (item: string, idx: number) => {
            ListItem() {
              Text(item)
                .fontSize(18)
                .width('100%')
                .padding(20)
                .backgroundColor('#F2F3F5')
                .borderRadius(8)
            }
          }, (item: string) => item)
        }
        .layoutWeight(1)
        .width('94%')
      }
      Button($r('app.string.back_home'))
        .width('80%')
        .margin({ bottom: 24 })
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
