// 彩色边框演示页：用 DrawModifier 画渐变边框，并演示三个参数
//   1. 圆角大小（滑块实时调）
//   2. 渐变颜色（按钮切换调色板）
//   3. 是否开启颜色流动（开关；开启后颜色沿边框环绕流动）
//
// 具体绘制逻辑在 common/ColorBorderModifier.ets，本页只负责挂上去和调参。
import { router } from '@kit.ArkUI';
import { ColorBorderModifier, ColorBorderOptions, cloneOptions } from '../common/ColorBorderModifier';

/** 可选调色板：首尾同色，保证流动一圈无缝衔接 */
const PALETTES: number[][] = [
  [0xFF60A5FA, 0xFFA78BFA, 0xFFF472B6, 0xFFFBBF24, 0xFF60A5FA],
  [0xFF34D399, 0xFF22D3EE, 0xFF60A5FA, 0xFF34D399],
  [0xFFFB923C, 0xFFEF4444, 0xFFEC4899, 0xFFFB923C],
  [0xFF818CF8, 0xFFA855F7, 0xFFEC4899, 0xFF818CF8]
];

/** 主演示框尺寸 */
const DEMO_WIDTH: number = 240;
const DEMO_HEIGHT: number = 140;

@Entry
@Component
struct ColorBorderPage {
  /** 圆角（vp），滑块可调 */
  @State radius: number = 24;
  /** 是否开启颜色流动 */
  @State flowing: boolean = true;
  /** 当前调色板下标 */
  @State paletteIndex: number = 0;

  /** 主演示框：三个参数都可调 */
  private mainBorder: ColorBorderModifier = new ColorBorderModifier(
    new ColorBorderOptions(24, 4, PALETTES[0], true));
  /** 对照：直角 + 静态渐变（不流动） */
  private squareBorder: ColorBorderModifier = new ColorBorderModifier(
    new ColorBorderOptions(0, 4, PALETTES[2], false));
  /** 对照：胶囊大圆角 + 静态渐变 */
  private pillBorder: ColorBorderModifier = new ColorBorderModifier(
    new ColorBorderOptions(40, 3, PALETTES[1], false));

  aboutToDisappear(): void {
    // 必须停：不然 animator 会一直跑
    this.mainBorder.stopFlow();
  }

  /** 把当前参数同步给主演示框并重绘 */
  private applyStyle(): void {
    const opts: ColorBorderOptions = cloneOptions(this.mainBorder.getOptions());
    opts.radius = this.radius;
    opts.colors = PALETTES[this.paletteIndex];
    this.mainBorder.applyOptions(opts);
  }

  build() {
    Column({ space: 18 }) {
      Text($r('app.string.color_border_title'))
        .fontSize(22)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 20 })

      // ---- 主演示框 ----
      Stack() {
        Text(this.radius.toString() + 'vp')
          .fontSize(16)
          .fontColor('#333333')
      }
      .width(DEMO_WIDTH)
      .height(DEMO_HEIGHT)
      .backgroundColor('#FFFFFF')
      .drawModifier(this.mainBorder)
      // UI 挂载后再启动流动，确保 animator 能正常重绘
      .onAppear(() => {
        if (this.flowing) {
          this.mainBorder.startFlow(this.getUIContext());
        }
      })

      // ---- 参数控制 ----
      Row({ space: 10 }) {
        Text($r('app.string.color_border_flow'))
          .fontSize(16)
          .fontColor('#333333')
        Toggle({ type: ToggleType.Switch, isOn: this.flowing })
          .onChange((isOn: boolean) => {
            this.flowing = isOn;
            this.mainBorder.setFlowing(this.getUIContext(), isOn);
          })
      }
      .width('90%')
      .justifyContent(FlexAlign.Center)

      Column({ space: 4 }) {
        Row({ space: 6 }) {
          Text($r('app.string.color_border_radius'))
            .fontSize(14)
            .fontColor('#666666')
          Text(this.radius.toString() + 'vp')
            .fontSize(14)
            .fontColor('#666666')
        }
        Slider({ value: this.radius, min: 0, max: 60, step: 1 })
          .width('86%')
          .onChange((value: number, _mode: SliderChangeMode) => {
            this.radius = Math.round(value);
            this.applyStyle();
          })
      }
      .width('100%')

      Button($r('app.string.color_border_palette'))
        .width('70%')
        .backgroundColor('#6D28D9')
        .onClick(() => {
          this.paletteIndex = (this.paletteIndex + 1) % PALETTES.length;
          this.applyStyle();
        })

      // ---- 参数对照 ----
      Row({ space: 16 }) {
        Column({ space: 6 }) {
          Stack() {
            Text('0')
              .fontSize(16)
              .fontColor('#999999')
          }
          .width(88)
          .height(64)
          .backgroundColor('#FFFFFF')
          .drawModifier(this.squareBorder)

          Text('radius 0')
            .fontSize(12)
            .fontColor('#999999')
        }

        Column({ space: 6 }) {
          Stack() {
            Text('40')
              .fontSize(16)
              .fontColor('#999999')
          }
          .width(88)
          .height(64)
          .backgroundColor('#FFFFFF')
          .drawModifier(this.pillBorder)

          Text('radius 40')
            .fontSize(12)
            .fontColor('#999999')
        }
      }

      Button($r('app.string.back_home'))
        .width('70%')
        .backgroundColor('#CCCCCC')
        .margin({ top: 4, bottom: 24 })
        .onClick(() => {
          router.back();
        })
    }
    .width('100%')
    .height('100%')
    .alignItems(HorizontalAlign.Center)
  }
}
