/**
 * 通用彩色边框（基于 DrawModifier）
 *
 * 用法：
 *   private border: ColorBorderModifier = new ColorBorderModifier(options);
 *   build() {
 *     SomeContainer() { ... }
 *       .drawModifier(this.border)          // 挂在任意组件上
 *   }
 *   // 需要流动时（在 onAppear / aboutToAppear 里调）：
 *   this.border.startFlow(this.getUIContext());
 *   // 页面销毁一定要停，否则 animator 会一直跑：
 *   aboutToDisappear() { this.border.stopFlow(); }
 *
 * 原理：
 *   1. 用 sweep（扫描/圆锥）渐变当画笔的 shader。扫描渐变的定义就是"角度 -> 颜色"，
 *      而边框正好是环绕中心的一圈，所以角度一旋转，颜色就沿着边框流动。
 *   2. 边框本体是"圆角矩形 + 描边"：描边宽度就是边框粗细，
 *      描边以路径为中心向两侧各画一半，所以路径整体内缩 borderWidth/2，
 *      边框才完整落在组件范围内。
 *   3. 流动靠 animator 每帧推进渐变起始角度，再调 invalidate() 让组件重绘。
 *      invalidate() 由框架实现，开发者不用也无法重写。
 *
 * 注意：
 *   - 想让循环无缝，colors 的首尾要用同一个颜色。
 *   - 绘制 API 全部以 px 为单位，本实现用 DrawContext.sizeInPixel 直接拿 px，
 *     并用 size / sizeInPixel 反推屏幕密度，因此不依赖 UIContext 也不需要 vp2px。
 */
import { AnimatorOptions, AnimatorResult, DrawContext, UIContext } from '@kit.ArkUI';
import { common2D, drawing } from '@kit.ArkGraphics2D';

/** 默认调色板：首尾同色，保证流动一圈无缝衔接 */
const DEFAULT_COLORS: number[] = [0xFF60A5FA, 0xFFA78BFA, 0xFFF472B6, 0xFFFBBF24, 0xFF60A5FA];

/** 彩色边框参数 */
export class ColorBorderOptions {
  /** 圆角大小（vp）。会被自动收敛到不超过短边一半 */
  radius: number = 16;
  /** 边框粗细（vp） */
  borderWidth: number = 3;
  /** 渐变颜色（0xAARRGGBB），首尾同色才能无缝循环 */
  colors: number[] = DEFAULT_COLORS;
  /** 每个颜色对应的位置（0~1），留空表示均匀分布；填了就必须和 colors 等长 */
  positions: number[] = [];
  /** 是否开启颜色流动 */
  flowing: boolean = false;
  /** 流动一圈耗时（ms） */
  duration: number = 2600;

  constructor(radius: number, borderWidth: number, colors: number[], flowing: boolean) {
    this.radius = radius;
    this.borderWidth = borderWidth;
    this.colors = colors;
    this.flowing = flowing;
  }
}

/** 直接改字段用的拷贝工具，避免多个边框共享同一个 options 对象 */
export function cloneOptions(src: ColorBorderOptions): ColorBorderOptions {
  const dst: ColorBorderOptions = new ColorBorderOptions(src.radius, src.borderWidth, src.colors, src.flowing);
  dst.positions = src.positions;
  dst.duration = src.duration;
  return dst;
}

export class ColorBorderModifier extends DrawModifier {
  private options: ColorBorderOptions;
  /** 当前渐变起始角度，流动时由 animator 每帧推进 */
  private flowAngle: number = 0;
  private animator: AnimatorResult | undefined = undefined;

  constructor(options: ColorBorderOptions) {
    super();
    this.options = options;
  }

  /** 换一套样式（圆角 / 颜色 / 粗细），立即重绘生效 */
  applyOptions(options: ColorBorderOptions): void {
    this.options = options;
    this.invalidate();
  }

  getOptions(): ColorBorderOptions {
    return this.options;
  }

  /**
   * 开关颜色流动。
   * 需要 UIContext 是因为 animator 要从它创建；传 null 表示只改样式不启停动画。
   */
  setFlowing(ui: UIContext, flowing: boolean): void {
    this.options.flowing = flowing;
    if (flowing) {
      if (ui !== null) {
        this.startFlow(ui);
      }
    } else {
      this.stopFlow();
    }
    this.invalidate();
  }

  /** 启动颜色流动：animator 无限循环推进角度，每帧请求重绘 */
  startFlow(ui: UIContext): void {
    this.stopFlow();
    if (this.options.colors.length < 2) {
      return;
    }
    const config: AnimatorOptions = {
      duration: this.options.duration,
      easing: 'linear',
      delay: 0,
      fill: 'forwards',
      direction: 'normal',
      iterations: -1,
      begin: 0,
      end: 360
    };
    const animator: AnimatorResult = ui.createAnimator(config);
    animator.onFrame = (value: number) => {
      this.flowAngle = value;
      // 只触发重绘，不重建组件树
      this.invalidate();
    };
    animator.play();
    this.animator = animator;
  }

  /** 停止流动。页面 aboutToDisappear 里务必调用 */
  stopFlow(): void {
    if (this.animator !== undefined) {
      this.animator.cancel();
      this.animator = undefined;
    }
  }

  /** 在组件背景层画边框 */
  drawBehind(context: DrawContext): void {
    const colors: number[] = this.options.colors;
    if (colors.length < 2) {
      return;
    }
    // 绘制 API 走 px：sizeInPixel 直接就是 px，省掉 vp->px 换算
    const widthPx: number = context.sizeInPixel.width;
    const heightPx: number = context.sizeInPixel.height;
    if (widthPx <= 0 || heightPx <= 0) {
      return;
    }
    // 用 vp / px 两套尺寸反推屏幕密度，这样不依赖 UIContext
    const vpWidth: number = context.size.width;
    const scale: number = vpWidth > 0 ? widthPx / vpWidth : 1;

    const borderPx: number = this.options.borderWidth * scale;
    const shortSidePx: number = Math.min(widthPx, heightPx);
    if (borderPx <= 0 || borderPx * 2 >= shortSidePx) {
      return;
    }

    // 扫描渐变：角度 -> 颜色。边框是环绕中心的一圈，旋转角度颜色就沿边框流动
    const center: common2D.Point = { x: widthPx / 2, y: heightPx / 2 };
    const positions: number[] | null =
      this.options.positions.length === colors.length ? this.options.positions : null;
    const shader: drawing.ShaderEffect = drawing.ShaderEffect.createSweepGradient(
      center,
      colors,
      drawing.TileMode.CLAMP,
      this.flowAngle,
      this.flowAngle + 360,
      positions,
      null
    );

    const pen: drawing.Pen = new drawing.Pen();
    pen.setAntiAlias(true);
    pen.setStrokeWidth(borderPx);
    pen.setShaderEffect(shader);

    // 描边向路径两侧各扩展一半，所以内缩 borderPx/2
    const inset: number = borderPx / 2;
    const maxRadiusPx: number = shortSidePx / 2 - inset;
    let radiusPx: number = this.options.radius * scale;
    if (radiusPx > maxRadiusPx) {
      radiusPx = maxRadiusPx;
    }
    const rect: common2D.Rect = {
      left: inset,
      top: inset,
      right: widthPx - inset,
      bottom: heightPx - inset
    };
    const roundRect: drawing.RoundRect = new drawing.RoundRect(rect, radiusPx, radiusPx);

    context.canvas.attachPen(pen);
    context.canvas.drawRoundRect(roundRect);
    context.canvas.detachPen();
  }
}
