// 流式文本 + 三色点阵 loading：文本逐字输出（打字机效果），
// 三个小点在红/蓝/黄间轮转并同步改变透明度；文本输出完毕后点阵消失。
// 仅使用 ArkUI 原生能力：setInterval 状态驱动 + Circle + .animation() 隐式动画。
import { router } from '@kit.ArkUI';

@Entry
@Component
struct StreamingDotsPage {
  // 点阵色板：红 → 蓝 → 黄 循环
  private readonly palette: string[] = ['#FF3B30', '#0A84FF', '#FFCC00']
  // 与色板同周期的透明度波：换色的点最亮，随后渐暗，形成向右传播的波
  private readonly wave: number[] = [1.0, 0.6, 0.3]
  private readonly fullText: string =
    'HarmonyOS NEXT 原生演示：这段文字正在逐字流式输出，模拟大模型回复的' +
    '打字机效果。输出期间，文本后方三个小点在红、蓝、黄三色间轮转，同时改变' +
    '透明度；文本输出完毕后，点阵随之消失。'

  @State streamed: string = ''
  @State streaming: boolean = true
  @State dotPhase: number = 0
  private streamTimer: number = -1
  private dotTimer: number = -1

  aboutToAppear(): void {
    this.startPlay()
  }

  aboutToDisappear(): void {
    this.clearTimers()
  }

  private startPlay(): void {
    this.clearTimers()
    this.streamed = ''
    this.dotPhase = 0
    this.streaming = true
    // 流式输出：每 70ms 吐出一个字符
    this.streamTimer = setInterval(() => {
      if (this.streamed.length >= this.fullText.length) {
        this.finishPlay()
      } else {
        this.streamed = this.fullText.substring(0, this.streamed.length + 1)
      }
    }, 70)
    // 点阵节拍：每 320ms 走一相，三个点按 index 错相，同一时刻红蓝黄各现一色
    this.dotTimer = setInterval(() => {
      this.dotPhase = (this.dotPhase + 1) % 3
    }, 320)
  }

  private finishPlay(): void {
    this.clearTimers()
    this.streaming = false // 触发 if 分支卸载，点阵淡出消失
  }

  private clearTimers(): void {
    if (this.streamTimer !== -1) {
      clearInterval(this.streamTimer)
      this.streamTimer = -1
    }
    if (this.dotTimer !== -1) {
      clearInterval(this.dotTimer)
      this.dotTimer = -1
    }
  }

  // 第 index 个点当前的颜色/透明度：按 dotPhase 错开一拍
  private dotColor(index: number): string {
    return this.palette[(this.dotPhase + index) % 3]
  }

  private dotOpacity(index: number): number {
    return this.wave[(this.dotPhase + index) % 3]
  }

  build() {
    Column({ space: 16 }) {
      Text($r('app.string.stream_title'))
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 48 })

      // 对话气泡：文本与点阵同处一个换行容器，点阵始终跟在末字符后方
      Column() {
        Flex({ wrap: FlexWrap.Wrap, alignItems: ItemAlign.End }) {
          Text(this.streamed)
            .fontSize(17)
            .lineHeight(26)
          if (this.streaming) {
            Row({ space: 6 }) {
              Circle({ width: 10, height: 10 })
                .fill(this.dotColor(0))
                .opacity(this.dotOpacity(0))
                .animation({ duration: 280, curve: Curve.EaseInOut })
              Circle({ width: 10, height: 10 })
                .fill(this.dotColor(1))
                .opacity(this.dotOpacity(1))
                .animation({ duration: 280, curve: Curve.EaseInOut })
              Circle({ width: 10, height: 10 })
                .fill(this.dotColor(2))
                .opacity(this.dotOpacity(2))
                .animation({ duration: 280, curve: Curve.EaseInOut })
            }
            .margin({ left: 6, bottom: 4 })
            .transition(TransitionEffect.OPACITY.animation({ duration: 250 }))
          }
        }
        .width('100%')
      }
      .padding(16)
      .backgroundColor('#F2F3F5')
      .borderRadius(12)
      .width('92%')

      Text(this.streaming ? '正在输出…' : '输出完成，点阵已消失')
        .fontSize(13)
        .fontColor('#999999')

      Button($r('app.string.stream_replay'))
        .width('60%')
        .onClick(() => {
          this.startPlay()
        })
      Button($r('app.string.back_home'))
        .width('60%')
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
