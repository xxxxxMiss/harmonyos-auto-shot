// 首页：$r 直引 + resourceManager 按名取值 + %d 占位符格式化
//
// 穿戴适配：手表屏只有 233x233vp（HUAWEI WATCH 6：466x466px @2x）且是圆屏，
// 手机版这一列 7 个入口按钮一屏放不下、又不可滚动，后面的入口根本够不着。
// 因此整体套一层 Scroll（手机内容本来就放得下，不出现滚动），
// 检测到 wearable 时再收紧间距 / 按钮尺寸与宽度，让按钮落在圆屏内接区域内。
import { router } from '@kit.ArkUI';
import { deviceInfo } from '@kit.BasicServicesKit';

/** 是否穿戴设备：决定紧凑布局参数 */
const IS_WEARABLE: boolean = deviceInfo.deviceType === 'wearable';

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
    Scroll() {
      Column({ space: IS_WEARABLE ? 10 : 16 }) {
        Text($r('app.string.main_title'))
          .fontSize(IS_WEARABLE ? 20 : 28)
          .fontWeight(FontWeight.Bold)
        Text(this.rmHello)
          .fontSize(IS_WEARABLE ? 12 : 16)
          .fontColor('#666666')
        Text($r('app.string.remaining_count', 3))
          .fontSize(IS_WEARABLE ? 12 : 16)
          .fontColor('#333333')
        Button($r('app.string.nav_settings'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/SettingsPage' })
          })
        Button($r('app.string.nav_list'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/ListPage' })
          })
        Button($r('app.string.nav_offset_list'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/OffsetListPage' })
          })
        Button($r('app.string.nav_color_border'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/ColorBorderPage' })
          })
        Button($r('app.string.nav_state'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/StatePage' })
          })
        Button($r('app.string.nav_entry'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/NavEntryPage' })
          })
        Button($r('app.string.nav_stream'))
          .width(IS_WEARABLE ? '72%' : '80%')
          .height(IS_WEARABLE ? 34 : 40)
          .fontSize(IS_WEARABLE ? 13 : 16)
          .onClick(() => {
            router.pushUrl({ url: 'pages/StreamingDotsPage' })
          })
      }
      .width('100%')
      .alignItems(HorizontalAlign.Center)
      .padding({ top: IS_WEARABLE ? 26 : 64, bottom: IS_WEARABLE ? 24 : 32 })
    }
    .width('100%')
    .height('100%')
    .scrollBar(BarState.Off)
    .edgeEffect(EdgeEffect.Spring, { alwaysEnabled: true })
  }
}
