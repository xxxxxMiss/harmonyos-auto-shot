// 首页：$r 直引 + resourceManager 按名取值 + %d 占位符格式化
import { router } from '@kit.ArkUI';

@Entry
@Component
struct Index {
  @State rmHello: string = ''

  aboutToAppear() {
    try {
      this.rmHello = getContext(this).resourceManager.getStringByNameSync('rm_hello')
    } catch (e) {
      this.rmHello = 'rm_hello'
    }
  }

  build() {
    Column({ space: 16 }) {
      Text($r('app.string.main_title'))
        .fontSize(28)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 64 })
      Text(this.rmHello)
        .fontSize(16)
        .fontColor('#666666')
      Text($r('app.string.remaining_count', 3))
        .fontSize(16)
        .fontColor('#333333')
      Button($r('app.string.nav_settings'))
        .width('80%')
        .margin({ top: 32 })
        .onClick(() => {
          router.pushUrl({ url: 'pages/SettingsPage' })
        })
      Button($r('app.string.nav_list'))
        .width('80%')
        .onClick(() => {
          router.pushUrl({ url: 'pages/ListPage' })
        })
      Button($r('app.string.nav_state'))
        .width('80%')
        .onClick(() => {
          router.pushUrl({ url: 'pages/StatePage' })
        })
      Button($r('app.string.nav_entry'))
        .width('80%')
        .onClick(() => {
          router.pushUrl({ url: 'pages/NavEntryPage' })
        })
    }
    .width('100%')
    .height('100%')
    .alignItems(HorizontalAlign.Center)
  }
}
