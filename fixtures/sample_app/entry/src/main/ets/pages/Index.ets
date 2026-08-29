// 示例页面：覆盖 $r 与 resourceManager 两种引用形态（仅用于离线扫描测试）
import { promptAction } from '@kit.ArkUI';
import { BusinessError } from '@kit.BasicServicesKit';
import { I18n } from '../common/I18n';

@Entry
@Component
struct Index {
  @State remain: number = 3

  build() {
    Column() {
      Text($r('app.string.main_title'))
        .fontSize(24)
      Button($r('app.string.login_button'))
        .onClick(() => {
          promptAction.showToast({ message: $r('app.string.login_button') })
        })
      Text(this.context.resourceManager.getStringSync($r('app.string.remaining_count').id, this.remain))
      Text(I18n.t('settings_title'))
    }
  }
}
