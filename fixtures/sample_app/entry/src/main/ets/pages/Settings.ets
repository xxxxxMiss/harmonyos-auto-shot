// 设置页：条件渲染（断网提示）与弹窗（点击触发）示例
@Component
export struct SettingsPage {
  @State networkOk: boolean = true
  @Prop aboutTitle: string = ''

  build() {
    Column() {
      if (!this.networkOk) {
        Text($r('app.string.network_error'))
          .fontSize(16)
      }
      Text($r('app.string.settings_about'))
        .onClick(() => {
          AlertDialog.show({
            title: $r('app.string.settings_about'),
            message: $r('app.string.settings_title')
          })
        })
    }
  }
}
