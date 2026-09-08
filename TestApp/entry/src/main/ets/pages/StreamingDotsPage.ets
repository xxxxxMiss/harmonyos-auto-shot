// 流式文本 + 内联三色点阵 loading：三个"●"作为 Span 内联在 Text 尾部，
// 与正文同处一个文本流，换行时紧随末字符一起流动（不会掉到单独一行）。
// 点阵驱动：33ms 相位时钟（约 30fps），颜色按红→蓝→黄离散切换，
// 透明度用余弦波连续变化（以 ARGB alpha 编码，Span 原生支持）。
// 文本输出完毕后点阵先全局淡出、再从文本流中移除。
// 仅使用 ArkUI 原生能力。
import { router } from '@kit.ArkUI';

const DOT_PERIOD: number = 960 // 单个点走完红→蓝→黄一轮的周期（ms）
const DOT_TICK: number = 33    // 点阵刷新间隔（ms，约 30fps）
const FADE_MS: number = 300    // 结束时点阵整体淡出时长（ms）

@Entry
@Component
struct StreamingDotsPage {
  private readonly fullText: string =
    'HarmonyOS NEXT 原生演示：这段文字正在逐字流式输出，模拟大模型回复的' +
    '打字机效果。输出期间，文本末尾三个小点在红、蓝、黄三色间轮转，同时改变' +
    '透明度；它们内联在文本流中，会紧跟最后一个字符一起换行。文本输出完毕' +
    '后，点阵随之消失。'
  private readonly dotPalette: string[] = ['#FF3B30', '#0A84FF', '#FFCC00']

  @State streamed: string = ''
  @State streaming: boolean = true
  @State dotClock: number = 0      // 点阵相位时钟，取值 [0, DOT_PERIOD)
  @State dotAlphaScale: number = 1 // 消失淡出系数，1 → 0

  private streamTimer: number = -1
  private dotTimer: number = -1
  private fadeTimer: number = -1
  private fadeSteps: number = 0

  aboutToAppear(): void {
    this.startPlay()
  }

  aboutToDisappear(): void {
    this.clearTimers()
  }

  private startPlay(): void {
    this.clearTimers()
    this.streamed = ''
    this.dotClock = 0
    this.dotAlphaScale = 1
    this.fadeSteps = 0
    this.streaming = true
    // 流式输出：每 70ms 吐出一个字符
    this.streamTimer = setInterval(() => {
      if (this.streamed.length >= this.fullText.length) {
        this.finishPlay()
      } else {
        this.streamed = this.fullText.substring(0, this.streamed.length + 1)
      }
    }, 70)
    // 点阵节拍：推进相位时钟，颜色与透明度全部由 dotColor() 按 clock 现算
    this.dotTimer = setInterval(() => {
      this.dotClock = (this.dotClock + DOT_TICK) % DOT_PERIOD
    }, DOT_TICK)
  }

  private finishPlay(): void {
    if (this.streamTimer !== -1) {
      clearInterval(this.streamTimer)
      this.streamTimer = -1
    }
    // 点阵整体淡出 FADE_MS 后从文本流中移除
    this.fadeSteps = 0
    this.fadeTimer = setInterval(() => {
      this.fadeSteps++
      this.dotAlphaScale = Math.max(0, 1 - this.fadeSteps * DOT_TICK / FADE_MS)
      if (this.fadeSteps * DOT_TICK >= FADE_MS) {
        if (this.fadeTimer !== -1) {
          clearInterval(this.fadeTimer)
          this.fadeTimer = -1
        }
        if (this.dotTimer !== -1) {
          clearInterval(this.dotTimer)
          this.dotTimer = -1
        }
        this.dotAlphaScale = 1
        this.dotClock = 0
        this.streaming = false
      }
    }, DOT_TICK)
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
    if (this.fadeTimer !== -1) {
      clearInterval(this.fadeTimer)
      this.fadeTimer = -1
    }
  }

  // 第 index 个点的颜色：三个点按 1/3 周期错相；
  // 颜色按周期三等分离散切换（红→蓝→黄），透明度沿余弦波连续起伏。
  private dotColor(index: number): string {
    const offset = index * DOT_PERIOD / 3
    const p = ((this.dotClock + offset) % DOT_PERIOD) / DOT_PERIOD
    const idx = Math.floor(p * 3) % 3
    const wave = 0.3 + 0.7 * (0.5 + 0.5 * Math.cos(2 * Math.PI * p))
    const alpha = Math.min(1, Math.max(0, wave * this.dotAlphaScale))
    const a = Math.round(alpha * 255).toString(16).padStart(2, '0')
    return `#${a}${this.dotPalette[idx].substring(1)}`
  }

  build() {
    Column({ space: 16 }) {
      Text($r('app.string.stream_title'))
        .fontSize(24)
        .fontWeight(FontWeight.Bold)
        .margin({ top: 48 })

      // 对话气泡：点阵是 Text 内的 Span，与正文同一文本流，随末字符换行
      Column() {
        Text() {
          Span(this.streamed)
          if (this.streaming) {
            Span('●').fontSize(14).letterSpacing(4).fontColor(this.dotColor(0))
            Span('●').fontSize(14).letterSpacing(4).fontColor(this.dotColor(1))
            Span('●').fontSize(14).letterSpacing(4).fontColor(this.dotColor(2))
          }
        }
        .fontSize(17)
        .lineHeight(26)
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
