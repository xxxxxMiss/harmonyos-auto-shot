// 设置页：静态文案 + 点击触发的 AlertDialog / Toast
import { router } from '@kit.ArkUI';
import { promptAction } from '@kit.ArkUI';

@Entry
@Component
struct SettingsPage {
  build() {
    Column({ space: 16 }) {
      Text($r('app.string.settings_title'))
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 64 })
      Text($r('app.string.settings_about'))
        .fontSize(18)
        .padding(12)
      Button($r('app.string.dialog_button'))
        .width('80%')
        .margin({ top: 32 })
        .onClick(() => {
          AlertDialog.show({
            title: $r('app.string.settings_about'),
            message: $r('app.string.dialog_message'),
          })
        })
      Button($r('app.string.toast_button'))
        .width('80%')
        .onClick(() => {
          promptAction.showToast({
            message: $r('app.string.toast_message'),
            duration: 8000,
          })
        })
      Button($r('app.string.back_home'))
        .width('80%')
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
