// 偏移列表页：List 配置 contentStartOffset / contentEndOffset + 底部固定按钮
//
// 场景：contentEndOffset 被**故意设置得很大**（见 geom.contentEndOffset）。
// 因此框架意义上的"真正底部"（onReachEnd / 直接滚到最大偏移所到达的位置）
// 并不是我们要的底部——停在那个位置时，末条离按钮还有一大截空白。
//
// 本页要验证的"底部" = 最后一条记录刚好处在底部按钮上方。
//   1. List 底部固定一个按钮，不跟随列表滑动（Stack 悬浮）
//   2. 初始加载自动滚到"真正底部"，再自动滚回"有效底部"（末条贴着按钮上方）
//   3. 到顶部后继续往回滚一次，加载 10 条插到列表开头
//   4. **末条只要还在屏上，就不允许它停在安全线以外的位置**——不管什么手势、往哪个方向、
//      速度快慢（拖动 / 惯性甩动 / 表冠）：停在安全线下方就收上来，被拉到真正底部那侧就放下去。
//      想让末条离开末尾区域，就用一次幅度够大的滑动把它滑出屏幕（那时不再插手）。
//      这条是硬约束：以前"用户自己往回划停下就不干预"会导致末条压在提示条上不回弹，
//      真机反馈"有时候划到顶部没有回弹回来"，现在一律拉回安全线。
//
// 兼容穿戴设备：
//   - 交互上全程不用 onTouch（手表上主要是旋转表冠），
//     滚动一律通过 onDidScroll / onScrollStop 这类滚动事件驱动。
//   - 几何上按设备给两套参数（见 OffsetGeometry）：
//     手表可用高度只有 ~233vp（HUAWEI WATCH 6：466x466px @2x），
//     照手机参数（末条 64 + 按钮 44+24 + 提示条 30+8+24）算下来安全线会跑到列表顶部之外，
//     末条直接被挤没；因此穿戴参数整体收窄，并把控件宽度限制在圆屏内接区域内。
import { router } from '@kit.ArkUI';
import { deviceInfo } from '@kit.BasicServicesKit';
import { hilog } from '@kit.PerformanceAnalysisKit';

/** 真机排障用日志标签：hdc shell hilog | grep OffSetDbg */
const DBG_TAG: string = 'OffSetDbg';
const DBG_DOMAIN: number = 0x0000;

/**
 * 页面几何参数（vp）。
 *
 * 修正逻辑本身与这些数值无关（修正量 = 末条实测坐标 - 安全线），
 * 但"安全线"能不能落在列表可视区内，完全由这些值决定，所以小屏必须给一套小参数。
 */
interface OffsetGeometry {
  /** 列表项高度（固定高度便于按几何关系精确计算修正距离） */
  itemHeight: number;
  /** 列表项主轴间距 */
  itemSpace: number;
  /** 列表项内边距（左右） */
  itemPadding: number;
  /** 列表项行内间距 */
  itemRowSpace: number;
  /** 列表项正文字号 */
  itemFontSize: number;
  /** 序号徽标直径 / 字号 */
  badgeSize: number;
  badgeFontSize: number;
  /** "末条"标记字号 */
  tagFontSize: number;
  /** 顶部状态条高度 / 字号 / 宽度 */
  statusBarHeight: number;
  statusFontSize: number;
  statusBarWidth: string;
  /** 列表左右内缩：圆屏上让整行落在圆内接区域里，避免行首/行尾被圆边裁掉 */
  listPadding: number;
  /** 底部固定按钮高度 / 距底边 margin / 宽度 / 字号 */
  buttonHeight: number;
  buttonBottomMargin: number;
  buttonWidth: string;
  buttonFontSize: number;
  /** "内容由AI生成"提示条高度（含流光边框）/ 圆角 / 边框宽度 */
  disclaimerHeight: number;
  disclaimerRadius: number;
  disclaimerBorder: number;
  /** 提示条底边与按钮顶边之间的间隙 */
  disclaimerBottomGap: number;
  /** 末条底边与提示条顶边之间保留的间隙 */
  lastItemGap: number;
  /** 提示条显隐判定阈值（vp）：接近 / 让开 */
  disclaimerApproach: number;
  disclaimerClear: number;
  /** 内容区起始偏移量 */
  contentStartOffset: number;
  /** 内容区末尾偏移量（故意很大，用来验证"有效底部"回滚） */
  contentEndOffset: number;
}

/** 手机 / 平板 / 2in1：原参数，保持既有表现 */
const PHONE_GEOMETRY: OffsetGeometry = {
  itemHeight: 64,
  itemSpace: 10,
  itemPadding: 12,
  itemRowSpace: 12,
  itemFontSize: 16,
  badgeSize: 28,
  badgeFontSize: 13,
  tagFontSize: 12,
  statusBarHeight: 36,
  statusFontSize: 13,
  statusBarWidth: '100%',
  listPadding: 0,
  buttonHeight: 44,
  buttonBottomMargin: 24,
  buttonWidth: '90%',
  buttonFontSize: 16,
  disclaimerHeight: 30,
  disclaimerRadius: 15,
  disclaimerBorder: 1.5,
  disclaimerBottomGap: 10,
  lastItemGap: 24,
  disclaimerApproach: 24,
  disclaimerClear: 12,
  contentStartOffset: 24,
  contentEndOffset: 520
};

/**
 * 穿戴设备：466x466px @2x = 233x233vp 圆屏（半径 116.5vp，圆心 (116.5,116.5)）。
 *
 * 圆屏上"能不能放得下"由内接矩形决定：离圆心垂直距离 d 处只有 2*sqrt(R²-d²) 宽，
 * 所以越靠上下边缘，可用宽度越窄。下面每个控件都按自己的底边深度验证过：
 *   状态条  y 0~22   ，y=22 处可用 136vp，取 54%=126vp
 *   按钮    y 173~203，底边 d=86.5 处可用 156vp，取 66%=154vp（字号 10，文案刚好放得下）
 *   提示条  y 147~167，d=50.5 处可用 210vp，提示条本身仅约 90vp
 *   末条    y 99~139 ，d=22.5 处可用 228vp，整条完整可见
 * 安全线（末条底边落点）= 提示条顶边 147 - lastItemGap 8 = 139vp。
 * 列表可视区 22~233vp，末条落在中下段最宽处，正是要观察的对象。
 *
 * **contentStartOffset + contentEndOffset 必须 ≤ 列表显示区高度**：
 * 文档写明两者之和超过 List 内容区长度时会被置 0（实测手表上 12+200=212 > 211 就整体失效，
 * 表现为 contentEndOffset 完全不产生可滚动空间、末条永远贴着列表底边）。手表列表高 211vp，
 * 所以取 12+160=172，留 39vp 余量；末条贴底时底边在 233-160=73vp，距安全线 66vp，
 * 回弹一眼可见。
 */
const WEARABLE_GEOMETRY: OffsetGeometry = {
  itemHeight: 40,
  itemSpace: 6,
  itemPadding: 8,
  itemRowSpace: 6,
  itemFontSize: 12,
  badgeSize: 20,
  badgeFontSize: 10,
  tagFontSize: 9,
  statusBarHeight: 22,
  statusFontSize: 9,
  statusBarWidth: '54%',
  listPadding: 8,
  buttonHeight: 30,
  buttonBottomMargin: 30,
  buttonWidth: '66%',
  buttonFontSize: 10,
  disclaimerHeight: 20,
  disclaimerRadius: 10,
  disclaimerBorder: 1,
  disclaimerBottomGap: 6,
  lastItemGap: 8,
  disclaimerApproach: 10,
  disclaimerClear: 4,
  contentStartOffset: 12,
  contentEndOffset: 160
};

/** 是否穿戴设备：决定用哪套几何参数 */
const IS_WEARABLE: boolean = deviceInfo.deviceType === 'wearable';

/** 提示条显隐动效时长（ms） */
const DISCLAIMER_ANIM_DURATION: number = 220;

/** 流光扫光单圈时长（ms）与起止位移 */
const SHIMMER_DURATION: number = 1600;
const SHINE_START: string = '-100%';
const SHINE_END: string = '100%';

/** 初始 Mock 数据条数 */
const INITIAL_COUNT: number = 30;
/** 每次加载条数 */
const PAGE_SIZE: number = 10;
/** 在顶部继续往回滚的累加阈值（vp），超过就加载下一页 */
const PULL_DOWN_THRESHOLD: number = 20;
/** 回弹动画时长（ms）：太短会看成瞬间跳变，看不出"回弹"过程 */
const BOUNCE_DURATION: number = 420;
/**
 * 修正动画"在飞"的保护窗口（ms）。
 *
 * 实测（HUAWEI WATCH 6）：在 onScrollStop 里调 scroller.scrollTo 之后，框架会在 ~1ms 内
 * 再补发一次 onScrollStop——此时偏移还停在修正前的位置，会被 pendingTarget 结算误判成
 * "目标没达到（被滚动范围夹住）"，于是本次修正直接放弃，列表永远停在框架真正底部。
 * 用时间窗把这段动画期内的补发事件挡掉；窗口远小于 BOUNCE_DURATION，不影响真正的落位结算。
 */
const CORRECTION_GUARD_MS: number = 150;
/** 首屏"拉到真正底部"用的目标偏移（vp）：给个足够大的值，由框架自动钳位到最大偏移 */
const INITIAL_SCROLL_TARGET: number = 100000;
/** 首屏"拉到真正底部"的动画时长（ms） */
const INITIAL_SCROLL_DURATION: number = 300;
/** 判定"已经在正确位置"的容差（vp） */
const OVERFLOW_TOLERANCE: number = 0.5;

/** 列表 / 末条 / 底部按钮的 id，用 getRectangleById 实测位置 */
const LIST_ID: string = 'offset_list';
const LAST_ITEM_ID: string = 'offset_last_item';
const BUTTON_ID: string = 'offset_bottom_button';
/** 提示条不参与几何换算（位置由按钮锚点推出），id 仅用于调试定位 */
const DISCLAIMER_ID: string = 'offset_disclaimer';

@Entry
@Component
struct OffsetListPage {
  @State items: string[] = [];
  @State statusText: string = '';
  private scroller: Scroller = new Scroller();
  /** 当前设备的几何参数 */
  private geom: OffsetGeometry = IS_WEARABLE ? WEARABLE_GEOMETRY : PHONE_GEOMETRY;
  /** 加载批次计数 */
  private batchNo: number = 0;
  /** 首屏是否已经滚过一次 */
  private initialScrolled: boolean = false;
  /** 本次滚动过程中是否到达过框架意义上的底部（onReachEnd） */
  private reachedRealEnd: boolean = false;
  /** 顶部往回滚的累加距离（vp） */
  private pullAccum: number = 0;
  /**
   * 上一次发起的修正"期望落到的偏移"，-1 表示没有待确认的修正。
   * 下一次滚动停下来时拿实际偏移跟它比对：对上了说明修正生效；对不上说明这次修正
   * 被后续手势改写了（用户又划了），此时**不能放弃**——按当前实测重新结算一次。
   */
  private pendingTarget: number = -1;
  /** 上一次发起修正动画的时刻（Date.now()），配合 CORRECTION_GUARD_MS 挡掉动画期的补发事件 */
  private correctionStartedAt: number = 0;
  /** 发起上一次修正时的偏移：用来区分"框架补发的重复事件"和"用户手势真的结束了" */
  private correctionFromOffset: number = Number.NaN;
  /** 本次手势中末条底边是否到过安全线（自证"确实滚到底了"，不依赖 onReachEnd/isAtEnd 的时序） */
  private touchedSafeLine: boolean = false;
  /** 首屏那次滚动是不是我们自己发起的（决定是否强制修正） */
  private awaitingInitialSettle: boolean = false;

  /** 提示条是否显示（默认显示，对应"静止在有效底部"的状态） */
  @State disclaimerVisible: boolean = true;
  /**
   * 提示条是否已经"让位"。
   * 上闩之后，末条一路往列表下方走都不再冒出来，只有末条重新回到提示条上方才解锁。
   */
  private disclaimerLatched: boolean = false;
  /** 流光高光的水平位移，靠它扫出"流光"效果 */
  @State shineX: string = SHINE_START;
  /** 流光动画是否已经启动过 */
  private shimmerStarted: boolean = false;
  /** 底部按钮顶边的窗口坐标（px）；-1 表示还没量到。按钮一直在，作为几何锚点 */
  private buttonTopPx: number = -1;
  /** 锚点被实测了几次（排障用） */
  private anchorMeasureCount: number = 0;

  aboutToAppear(): void {
    const arr: string[] = [];
    for (let i = 1; i <= INITIAL_COUNT; i++) {
      arr.push(`初始 Mock 数据 #${i}`);
    }
    this.items = arr;
    this.statusText = `共 ${arr.length} 条 · contentEndOffset=${this.geom.contentEndOffset}vp`;
  }

  /** 提示条相对列表底边的 margin：正好落在按钮正上方 */
  private disclaimerMargin(): number {
    return this.geom.buttonHeight + this.geom.buttonBottomMargin + this.geom.disclaimerBottomGap;
  }

  /**
   * 安全线相对"按钮顶边"的距离（vp）：
   * 提示条底边间隙 + 提示条高度 + 末条与提示条顶边的间隙。
   *
   * 注意别写成 disclaimerMargin()：那个值里还含 buttonHeight + buttonBottomMargin，
   * 而按钮顶边本身已经把它们算掉了，重复相减会把安全线凭空抬高
   * （手机参数下正好抬高 38vp，且与兜底路径 fallbackInset() 自相矛盾）。
   */
  private safeLineInsetFromButtonTop(): number {
    return this.geom.disclaimerBottomGap + this.geom.disclaimerHeight + this.geom.lastItemGap;
  }

  /**
   * 末条量不到时的兜底内缩量（vp）：安全线距**屏底**的距离。
   * 正常路径不需要它（修正距离由末条与按钮的实测坐标相减得到，与列表高度无关），
   * 只在末条被 contentEndOffset 推出显示区时按配置值推算该收回多少。
   */
  private fallbackInset(): number {
    return this.geom.buttonBottomMargin + this.geom.buttonHeight + this.safeLineInsetFromButtonTop();
  }

  /**
   * 量一次底部按钮的位置并缓存为几何锚点。
   * 按钮一直在（不像提示条会被 if 摘掉），所以任何时候都能量到；
   * 提示条和安全线的位置都由它换算出来，也就不用再维护列表高度。
   *
   * 注意：**每次滚动停下来都要重新量**（onScrollStop 里失效缓存）。
   * 首帧的布局还没稳定，那时量到的按钮位置会偏（实测偏 22vp），
   * 一旦缓存住就永远偏高，末条会被停到错误的位置。
   */
  private ensureAnchor(): boolean {
    if (this.buttonTopPx >= 0) {
      return true;
    }
    try {
      const button = this.getUIContext().getComponentUtils().getRectangleById(BUTTON_ID);
      if (button.size.height <= 0) {
        return false;
      }
      this.buttonTopPx = button.windowOffset.y;
      this.anchorMeasureCount++;
      hilog.info(DBG_DOMAIN, DBG_TAG,
        `anchor#${this.anchorMeasureCount}: top=${this.buttonTopPx.toFixed(1)} ` +
        `h=${button.size.height} screenY=${button.screenOffset.y.toFixed(1)}`);
      return true;
    } catch (err) {
      return false;
    }
  }

  /**
   * 实测末条底边的窗口坐标（px），**末条不在列表显示区内时返回 NaN**。
   *
   * 注意必须显式判可见性：getRectangleById 对已经滚出显示区的列表项照样返回布局矩形
   * （实测能给出屏幕下方 1000px 的坐标），只看 size.height 会把"末条早就不在屏上"
   * 误判成"还在屏上"，于是用户往上翻到一半又被拽回底部。
   */
  private measureLastItemBottomPx(): number {
    try {
      const ui = this.getUIContext();
      const item = ui.getComponentUtils().getRectangleById(LAST_ITEM_ID);
      if (item.size.height <= 0) {
        return Number.NaN;
      }
      const list = ui.getComponentUtils().getRectangleById(LIST_ID);
      if (list.size.height <= 0) {
        return Number.NaN;
      }
      const itemTop: number = item.windowOffset.y;
      const itemBottom: number = itemTop + item.size.height;
      const listTop: number = list.windowOffset.y;
      const listBottom: number = listTop + list.size.height;
      // 与显示区有交集才算"在屏上"（露出一点也算）
      if (itemBottom <= listTop || itemTop >= listBottom) {
        return Number.NaN;
      }
      return itemBottom;
    } catch (err) {
      return Number.NaN;
    }
  }

  /** 安全线的窗口坐标（px），量不到锚点返回 NaN */
  private safeLineBottomPx(): number {
    if (!this.ensureAnchor()) {
      return Number.NaN;
    }
    return this.buttonTopPx - this.getUIContext().vp2px(this.safeLineInsetFromButtonTop());
  }

  /** 当前滚动偏移，取不到返回 NaN */
  private currentOffsetOrNaN(): number {
    try {
      return this.scroller.currentOffset().yOffset;
    } catch (err) {
      return Number.NaN;
    }
  }

  /**
   * 每帧检查：末条底边是不是已经到过/越过安全线。
   * 到过就锁存到本次滚动结算——这是"确实滚到底了"的自证，
   * 不再依赖 onReachEnd / isAtEnd()（实测它们会漏报，导致漏掉回弹）。
   * 末条量不到（已经滚出显示区）说明用户离开了末尾区域，锁存跟着清掉。
   */
  private markSafeLineTouched(): void {
    const itemBottomPx: number = this.measureLastItemBottomPx();
    if (Number.isNaN(itemBottomPx)) {
      this.touchedSafeLine = false;
      return;
    }
    if (this.touchedSafeLine) {
      return;
    }
    const safePx: number = this.safeLineBottomPx();
    if (Number.isNaN(safePx)) {
      return;
    }
    if (itemBottomPx <= safePx + this.getUIContext().vp2px(OVERFLOW_TOLERANCE)) {
      this.touchedSafeLine = true;
    }
  }

  /**
   * 实测末条底边越过"安全线"的距离（vp）。
   * 安全线 = 提示条顶边 - lastItemGap，也就是末条刚好停在提示条上方时底边的位置。
   * 返回 NaN 表示量不到（未布局 / 已滚出显示区）。
   */
  private measureOverflow(): number {
    const safeBottomPx: number = this.safeLineBottomPx();
    if (Number.isNaN(safeBottomPx)) {
      hilog.info(DBG_DOMAIN, DBG_TAG, 'measure: 量不到按钮锚点');
      return Number.NaN;
    }
    const itemBottomPx: number = this.measureLastItemBottomPx();
    const ui = this.getUIContext();
    if (Number.isNaN(itemBottomPx)) {
      hilog.info(DBG_DOMAIN, DBG_TAG,
        `measure: 末条量不到 buttonTop=${this.buttonTopPx} safe=${safeBottomPx.toFixed(1)}`);
      return Number.NaN;
    }
    const ovf: number = ui.px2vp(itemBottomPx - safeBottomPx);
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `measure: itemBottom=${itemBottomPx.toFixed(1)} buttonTop=${this.buttonTopPx.toFixed(1)} ` +
      `safe=${safeBottomPx.toFixed(1)} overflow=${ovf.toFixed(2)}`);
    return ovf;
  }

  /**
   * 根据末条位置更新提示条显隐（带"让位"闩锁）。
   *
   * 判定只看末条底边 d 相对提示条底边的位置：
   *   d 高出提示条顶边 clear 之上（末条在提示条上方） -> 显示，解锁
   *   d 落在 approach 以内（接近/经过/停在提示条上）  -> 隐藏，上闩
   *   d 还在更下方                                    -> 保持隐藏状态（上过闩就不再冒头）
   *
   * 上闩是关键：末条往下走穿过提示条后，它会继续往列表下方跑，
   * 如果没有闩，d 变大又会满足"显示"条件，就出现"先消失再冒出来"的抖动。
   */
  private updateDisclaimerVisibility(): void {
    if (!this.ensureAnchor()) {
      return;
    }
    const itemBottomPx: number = this.measureLastItemBottomPx();
    if (Number.isNaN(itemBottomPx)) {
      return;
    }
    const ui = this.getUIContext();
    const disclaimerBottomPx: number = this.buttonTopPx - ui.vp2px(this.geom.disclaimerBottomGap);
    const heightPx: number = ui.vp2px(this.geom.disclaimerHeight);
    // d > 0：末条底边还在提示条底边下方；d < 0：已经升到提示条底边之上
    const d: number = itemBottomPx - disclaimerBottomPx;
    if (d <= -(heightPx + ui.vp2px(this.geom.disclaimerClear))) {
      // 末条已经回到提示条上方 -> 恢复显示，并解除"让位"状态
      this.disclaimerLatched = false;
      this.disclaimerVisible = true;
    } else if (d <= ui.vp2px(this.geom.disclaimerApproach)) {
      // 接近 / 正经过 / 停在提示条上 -> 隐藏，并上闩
      this.disclaimerLatched = true;
      this.disclaimerVisible = false;
    } else {
      // 末条还在提示条下方：只有从没让过位才显示。
      // 这是"继续往下滑不能出现"的关键——让位之后一路往下都不再冒头，
      // 必须等末条被拉回提示条上方才恢复。
      this.disclaimerVisible = !this.disclaimerLatched;
    }
  }

  /** 启动流光：高光带在边框里来回扫 */
  private startShimmer(): void {
    if (this.shimmerStarted) {
      return;
    }
    this.shimmerStarted = true;
    this.shineX = SHINE_START;
    this.getUIContext().animateTo({
      duration: SHIMMER_DURATION,
      curve: Curve.Linear,
      iterations: -1
    }, () => {
      this.shineX = SHINE_END;
    });
  }

  /** 提示条上的流光高光层：铺满整条，靠位移扫过去 */
  @Builder
  disclaimerShine() {
    Row()
      .width('100%')
      .height('100%')
      .translate({ x: this.shineX })
      .linearGradient({
        angle: 90,
        colors: [
          ['rgba(255,255,255,0)', 0.0],
          ['rgba(255,255,255,0.9)', 0.5],
          ['rgba(255,255,255,0)', 1.0]
        ]
      })
  }

  /**
   * 把列表停到"有效底部"：末条底边刚好落在提示条上方（留 lastItemGap 间隙）。
   *
   * contentEndOffset 很大时，"真正底部"会让末条离按钮很远；反过来末条也可能被
   * 按钮压住。两种偏差都用同一个位移修正：
   *     位移 = 末条底边 - 安全线
   *     为正 -> 往前滚（把末条顶上来）
   *     为负 -> 往回滚（把多余空白收掉）
   *
   * @param animate 是否带过渡动画
   * @param force   是否强制修正（已到达框架底部时，两个方向都要纠正）
   */
  private settleAtEffectiveBottom(animate: boolean, force: boolean): void {
    if (this.items.length === 0) {
      return;
    }
    let current: number = 0;
    try {
      current = this.scroller.currentOffset().yOffset;
    } catch (err) {
      hilog.info(DBG_DOMAIN, DBG_TAG, 'settle: currentOffset 取不到');
      return;
    }
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `settle: force=${force} cur=${current.toFixed(1)} pending=${this.pendingTarget.toFixed(1)} ` +
      `items=${this.items.length}`);

    // 先给上一次修正结账。关键：**落不落位都继续往下算**。
    // 落位了说明修正生效；没落位只可能是"上一次修正被后面的手势改写了"（用户又划了），
    // 这时必须按当前实测重新结算一次——以前这里直接 return 放弃，
    // 结果就是列表永远停在"真正底部"、状态误报"已到滚动极限"（真机复现过）。
    if (this.pendingTarget >= 0) {
      const expected: number = this.pendingTarget;
      const landed: boolean = Math.abs(current - expected) <= OVERFLOW_TOLERANCE;
      this.pendingTarget = -1;
      if (landed) {
        hilog.info(DBG_DOMAIN, DBG_TAG, 'settle: 上次修正已落位，继续');
      } else {
        hilog.info(DBG_DOMAIN, DBG_TAG,
          `settle: 上次修正被后续手势改写（cur=${current.toFixed(1)} 期望=${expected.toFixed(1)}），按实测重算`);
      }
    }

    const overflow: number = this.measureOverflow();
    let delta: number = 0;
    if (Number.isNaN(overflow)) {
      // 末条已经滚出显示区（用户在列表中部，或 contentEndOffset 把它推出去了）。
      // 只有确认到达框架底部（或首屏那次），才按配置值推算需要收回的距离；
      // 否则用户在列表中部停下来时不该被插手。
      if (!force) {
        hilog.info(DBG_DOMAIN, DBG_TAG, 'settle: 末条不在屏上且非强制，不干预');
        return;
      }
      delta = -(this.geom.contentEndOffset - this.fallbackInset());
    } else {
      if (Math.abs(overflow) <= OVERFLOW_TOLERANCE) {
        // 末条刚好停在安全线上
        hilog.info(DBG_DOMAIN, DBG_TAG, `settle: 已在有效底部（overflow=${overflow.toFixed(2)}）`);
        return;
      }
      // 末条只要还在屏上，就不允许它停在安全线以外的地方：
      //   为正（压在提示条/按钮那边）-> 往回收上来
      //   为负（被拉到真正底部那侧）-> 放下去
      // 不看手势方向、不看 onReachEnd——"不管怎么划，末条都得回到安全线"。
      delta = overflow;
    }

    let target: number = current + delta;
    if (target < 0) {
      target = 0;
    }
    if (Math.abs(target - current) <= OVERFLOW_TOLERANCE) {
      // 目标位置就是当前位置（已被内容边界夹住），无需滚动
      hilog.info(DBG_DOMAIN, DBG_TAG, `settle: 目标==当前 ${target.toFixed(1)}，无需滚动`);
      return;
    }
    this.pendingTarget = target;
    this.correctionStartedAt = Date.now();
    this.correctionFromOffset = current;
    this.statusText = `自动滚回底部 · 修正 ${delta.toFixed(0)}vp`;
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `settle: 发起修正 delta=${delta.toFixed(1)} target=${target.toFixed(1)} animate=${animate}`);
    this.scroller.scrollTo({
      xOffset: 0,
      yOffset: target,
      animation: animate ? { duration: BOUNCE_DURATION, curve: Curve.EaseOut } : false
    });
  }

  /** 加载下一页：插入到列表开头（同步插入，不做假延时） */
  private loadMore(): void {
    this.batchNo++;
    const fresh: string[] = [];
    for (let i = 1; i <= PAGE_SIZE; i++) {
      fresh.push(`下拉加载第${this.batchNo}批 · ${i}/${PAGE_SIZE}`);
    }
    this.items = fresh.concat(this.items);
    this.pendingTarget = -1;
    this.statusText = `已加载 ${this.batchNo * PAGE_SIZE} 条 · 当前共 ${this.items.length} 条`;
    hilog.info(DBG_DOMAIN, DBG_TAG, `loadMore: 第${this.batchNo}批，共 ${this.items.length} 条`);
  }

  /** 列表是否停在顶部 */
  private isAtTop(): boolean {
    try {
      return this.scroller.currentOffset().yOffset <= 0;
    } catch (err) {
      return false;
    }
  }

  build() {
    Column() {
      // 顶部状态条：方便真机核对当前滚动 / 修正结果。
      // 穿戴设备上收窄到 62% 并居中——圆屏顶部只有中间一条是可见的。
      Row() {
        Text(this.statusText)
          .fontSize(this.geom.statusFontSize)
          .fontColor('#666666')
          .textAlign(TextAlign.Center)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width(this.geom.statusBarWidth)
      .height(this.geom.statusBarHeight)
      .padding({ left: 8, right: 8 })
      .backgroundColor('#FAFAFA')

      Stack({ alignContent: Alignment.Bottom }) {
        List({ space: this.geom.itemSpace, scroller: this.scroller }) {
          ForEach(this.items, (item: string, idx: number) => {
            ListItem() {
              Row({ space: this.geom.itemRowSpace }) {
                Text(`${idx + 1}`)
                  .fontSize(this.geom.badgeFontSize)
                  .fontColor('#FFFFFF')
                  .textAlign(TextAlign.Center)
                  .width(this.geom.badgeSize)
                  .height(this.geom.badgeSize)
                  .borderRadius(this.geom.badgeSize / 2)
                  .backgroundColor(idx === this.items.length - 1 ? '#E64545' : '#8C8C8C')
                Text(item)
                  .fontSize(this.geom.itemFontSize)
                  .fontColor('#333333')
                  .layoutWeight(1)
                  .maxLines(1)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                if (idx === this.items.length - 1) {
                  Text('末条')
                    .fontSize(this.geom.tagFontSize)
                    .fontColor('#E64545')
                }
              }
              .width('100%')
              .height(this.geom.itemHeight)
              .padding({ left: this.geom.itemPadding, right: this.geom.itemPadding })
              .backgroundColor(idx === this.items.length - 1 ? '#FFF1F0' : '#F2F3F5')
              .borderRadius(8)
              .border({
                width: idx === this.items.length - 1 ? 1 : 0,
                color: '#E64545'
              })
            }
            // 末条单独一个 id，供 getRectangleById 实测位置
            .id(idx === this.items.length - 1 ? LAST_ITEM_ID : `offset_item_${idx}`)
          }, (item: string) => item)
        }
        .width('100%')
        .height('100%')
        // 圆屏：整行内缩，避免行首行尾被圆边裁掉（手机为 0）
        .padding({ left: this.geom.listPadding, right: this.geom.listPadding })
        .id(LIST_ID)
        // 内容区起始偏移量：列表滚到顶部时内容与显示区边界保留一段间距
        .contentStartOffset(this.geom.contentStartOffset)
        // 内容区末尾偏移量：故意很大，用来验证"有效底部"的自动回滚
        .contentEndOffset(this.geom.contentEndOffset)
        .scrollBar(BarState.Auto)
        .edgeEffect(EdgeEffect.Spring, { alwaysEnabled: true })
        .onAppear(() => {
          // 首屏拉到框架意义上的"真正底部"。
          // 这里必须用带动画的 scrollTo，而不是 scrollEdge：
          // scrollEdge 是瞬时跳转，不产生滚动停止事件，收不到 onScrollStop，
          // 结果就会一直停在"真正底部"（末条离按钮还有 contentEndOffset 那么远）。
          // scrollTo 的动画结束一定会触发 onScrollStop，在那里再收回到"有效底部"。
          if (this.initialScrolled) {
            return;
          }
          this.initialScrolled = true;
          this.pendingTarget = -1;
          this.awaitingInitialSettle = true;
          this.startShimmer();
          hilog.info(DBG_DOMAIN, DBG_TAG, 'onAppear: scrollTo(100000) 拉真正底部');
          try {
            this.scroller.scrollTo({
              xOffset: 0,
              yOffset: INITIAL_SCROLL_TARGET,
              animation: { duration: INITIAL_SCROLL_DURATION, curve: Curve.EaseOut }
            });
          } catch (err) {
            this.initialScrolled = false;
            this.awaitingInitialSettle = false;
          }
        })
        // 到达框架意义上的底部：先记标记，等滚动停下来再统一修正
        .onReachEnd(() => {
          this.reachedRealEnd = true;
        })
        // 滚动事件：不用 onTouch，表冠 / 触摸拖动 / 惯性甩动都会走到这里
        .onDidScroll((scrollOffset: number, _scrollState: ScrollState) => {
          if (this.scroller.isAtEnd()) {
            this.reachedRealEnd = true;
          } else {
            // 离开末尾就清掉，避免陈旧标记把中段的用户一把拽到末尾
            this.reachedRealEnd = false;
          }
          // 每帧自证"是否已经到过底部"，不依赖 onReachEnd / isAtEnd() 的时序
          this.markSafeLineTouched();
          // 每帧按末条位置刷新提示条显隐（表冠 / 触摸 / 惯性都会走到这里）
          this.updateDisclaimerVisibility();
          if (scrollOffset < 0 && this.isAtTop()) {
            // 已经在顶部还继续往回滚 -> 累加下拉距离
            this.pullAccum -= scrollOffset;
            if (this.pullAccum >= PULL_DOWN_THRESHOLD) {
              this.pullAccum = 0;
              this.loadMore();
            }
          } else if (scrollOffset > 0) {
            // 往回滚了，本次下拉作废
            this.pullAccum = 0;
          }
        })
        // 滚动停止（含 Scroller 触发的滚动动画结束、惯性甩动结束）：
        // 只要落点越过"有效底部"就自动收回来。放在这里而不是 onReachEnd，
        // 是为了不打断进行中的甩动动画。
        .onScrollStop(() => {
          // 刚发起的修正动画还没跑完时，框架会补发一次 onScrollStop。
          // 判据必须是"**偏移一点没动**"（补发事件的特征），不能只看时间：
          // 只看时间会把用户手势真正结束的那次事件也吞掉，那次手势就完全没结算——
          // 真机表现就是"有时候划到底没有回弹"。
          if (this.pendingTarget >= 0
            && Date.now() - this.correctionStartedAt < CORRECTION_GUARD_MS
            && Math.abs(this.currentOffsetOrNaN() - this.correctionFromOffset) <= OVERFLOW_TOLERANCE) {
            hilog.info(DBG_DOMAIN, DBG_TAG, 'onScrollStop: 修正动画在飞（偏移未变），忽略补发事件');
            return;
          }
          // 要不要按配置值兜底修正（末条已经在屏外时）？
          //   1) 首屏那次是我们自己拉到"真正底部"的；
          //   2) 框架报了到达末尾；
          //   3) 本次手势里末条到过安全线（自证滚到底了，不依赖 onReachEnd/isAtEnd 的时序）。
          // 注意：末条**在屏上**时根本不看 force——见 settleAtEffectiveBottom。
          const force: boolean = this.reachedRealEnd || this.awaitingInitialSettle
            || this.touchedSafeLine;
          hilog.info(DBG_DOMAIN, DBG_TAG,
            `onScrollStop: reachedEnd=${this.reachedRealEnd} awaitingInitial=${this.awaitingInitialSettle} ` +
            `touchedSafeLine=${this.touchedSafeLine} -> force=${force}`);
          this.reachedRealEnd = false;
          this.awaitingInitialSettle = false;
          this.touchedSafeLine = false;
          this.pullAccum = 0;
          // 停下来了，重新量一次锚点：首帧量到的位置不可信（见 ensureAnchor 注释）
          this.buttonTopPx = -1;
          this.settleAtEffectiveBottom(true, force);
          this.updateDisclaimerVisibility();
        })

        // "内容由AI生成"提示条：位于底部按钮正上方。
        // 末条升到它跟前时整条淡出+下移让位，末条被拉上去之后再回来。
        if (this.disclaimerVisible) {
          Row() {
            Text($r('app.string.ai_generated_disclaimer'))
              .fontSize(this.geom.badgeFontSize)
              .fontColor('#6D28D9')
              .textAlign(TextAlign.Center)
              .height(this.geom.disclaimerHeight - this.geom.disclaimerBorder * 2)
              .padding({ left: 8, right: 8 })
              .backgroundColor('#FFFFFF')
              .borderRadius(this.geom.disclaimerRadius - this.geom.disclaimerBorder)
          }
          .padding(this.geom.disclaimerBorder)
          .borderRadius(this.geom.disclaimerRadius)
          // 边框底色：多色线性渐变
          .linearGradient({
            angle: 90,
            colors: [['#60A5FA', 0.0], ['#A78BFA', 0.33], ['#F472B6', 0.66], ['#FBBF24', 1.0]]
          })
          .clip(true)
          // 叠一层扫动的白色高光 -> 渐变边框流光
          .overlay(this.disclaimerShine())
          .id(DISCLAIMER_ID)
          .margin({ bottom: this.disclaimerMargin() })
          .transition(
            TransitionEffect.OPACITY
              .animation({ duration: DISCLAIMER_ANIM_DURATION, curve: Curve.EaseOut })
              .combine(
                TransitionEffect.translate({ y: 12 })
                  .animation({ duration: DISCLAIMER_ANIM_DURATION, curve: Curve.EaseOut })
              )
          )
        }

        // 底部固定按钮：悬浮在 List 之上，不随列表滑动
        Button($r('app.string.offset_bottom_button'))
          .id(BUTTON_ID)
          .width(this.geom.buttonWidth)
          .height(this.geom.buttonHeight)
          .fontSize(this.geom.buttonFontSize)
          .margin({ bottom: this.geom.buttonBottomMargin })
          .onClick(() => {
            router.back();
          })
      }
      .width('100%')
      .layoutWeight(1)
    }
    .width('100%')
    .height('100%')
  }
}
