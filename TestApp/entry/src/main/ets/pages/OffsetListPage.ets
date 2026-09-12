// 偏移列表页：List 配置 contentStartOffset / contentEndOffset + 底部固定按钮
//
// 场景：contentEndOffset 被**故意设置得很大**（见 CONTENT_END_OFFSET）。
// 因此框架意义上的"真正底部"（onReachEnd / 直接滚到最大偏移所到达的位置）
// 并不是我们要的底部——停在那个位置时，末条离按钮还有一大截空白。
//
// 本页要验证的"底部" = 最后一条记录刚好处在底部按钮上方。
//   1. List 底部固定一个按钮，不跟随列表滑动（Stack 悬浮）
//   2. 初始加载自动滚到"真正底部"，再自动滚回"有效底部"（末条贴着按钮上方）
//   3. 到顶部后继续往回滚一次，加载 10 条插到列表开头
//   4. 只要滚到底部（拖动、惯性甩动、表冠滚动，速度快慢都一样），都自动停到"有效底部"：
//        末条被按钮压住 -> 往前滚，把它顶上来
//        末条离按钮太远 -> 往回滚，把多余空白收掉
//
// 兼容穿戴设备：全程不用 onTouch（手表上主要是旋转表冠），
// 滚动一律通过 onDidScroll / onScrollStop 这类滚动事件驱动。
import { router } from '@kit.ArkUI';

/** 列表项固定高度（vp），固定高度便于按几何关系精确计算修正距离 */
const ITEM_HEIGHT: number = 64;
/** 列表项主轴间距（vp） */
const ITEM_SPACE: number = 10;

/** 底部固定按钮高度（vp） */
const BUTTON_HEIGHT: number = 44;
/** 按钮距列表底边的 margin（vp） */
const BUTTON_BOTTOM_MARGIN: number = 24;

/** "内容由AI生成"提示条高度（vp，含流光边框） */
const DISCLAIMER_HEIGHT: number = 30;
/** 提示条圆角（vp） */
const DISCLAIMER_RADIUS: number = 15;
/** 流光边框宽度（vp） */
const DISCLAIMER_BORDER: number = 1.5;
/** 提示条底边与按钮顶边之间的间隙（vp） */
const DISCLAIMER_BOTTOM_GAP: number = 10;
/** 提示条相对列表底边的 margin：正好落在按钮正上方 */
const DISCLAIMER_MARGIN: number = BUTTON_HEIGHT + BUTTON_BOTTOM_MARGIN + DISCLAIMER_BOTTOM_GAP;
/** 末条底边与提示条顶边之间保留的间隙（vp） */
const LAST_ITEM_GAP: number = 24;

/**
 * 末条量不到时的兜底内缩量（vp）。正常路径不需要它：
 * 修正距离由"末条实测坐标"和"按钮实测坐标"相减得到，与列表高度无关。
 * 安全线 = 提示条顶边 - LAST_ITEM_GAP，展开就是下面这个式子。
 */
const FALLBACK_INSET: number = DISCLAIMER_MARGIN + DISCLAIMER_HEIGHT + LAST_ITEM_GAP;

/**
 * 提示条显隐判定，只看"末条底边"和提示条的位置关系：
 *   末条底边还在提示条底边下方超过 APPROACH  -> 还没滚到，显示
 *   末条底边高出提示条顶边 CLEAR 之上         -> 已经被拉上去了，显示
 *   两者之间（正经过 / 停在提示条上）         -> 隐藏
 * 两个阈值留出滞回区间，避免甩动时反复闪。
 */
const DISCLAIMER_APPROACH: number = 24;
const DISCLAIMER_CLEAR: number = 12;
/** 提示条显隐动效时长（ms） */
const DISCLAIMER_ANIM_DURATION: number = 220;

/** 流光扫光单圈时长（ms）与起止位移 */
const SHIMMER_DURATION: number = 1600;
const SHINE_START: string = '-100%';
const SHINE_END: string = '100%';

/** 内容区起始偏移量（vp） */
const CONTENT_START_OFFSET: number = 24;
/**
 * 内容区末尾偏移量（vp）——故意设置得很大，用来验证框架"真正底部"远离"有效底部"时，
 * 如何自动把多余空白收回来。
 *
 * 这个值同时决定了回弹距离：
 *     回弹距离 = CONTENT_END_OFFSET - FALLBACK_INSET
 * 当前 = 520 - 132 = 388vp，滚到"真正底部"后会被一次性收回这么多，肉眼很容易看出来。
 * 想调回弹幅度改这里即可（调小会让回弹变得不明显）。
 */
const CONTENT_END_OFFSET: number = 520;

/** 初始 Mock 数据条数 */
const INITIAL_COUNT: number = 30;
/** 每次加载条数 */
const PAGE_SIZE: number = 10;
/** 在顶部继续往回滚的累加阈值（vp），超过就加载下一页 */
const PULL_DOWN_THRESHOLD: number = 20;
/** 回弹动画时长（ms）：太短会看成瞬间跳变，看不出"回弹"过程 */
const BOUNCE_DURATION: number = 420;
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
   * 下一次滚动停下来时拿实际偏移跟它比对：对上了说明修正生效；对不上说明被滚动范围夹住，
   * 本次就不再重试（同一手势内不叠加修正，杜绝"修正->滚动->再修正"死循环）。
   */
  private pendingTarget: number = -1;
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

  aboutToAppear(): void {
    const arr: string[] = [];
    for (let i = 1; i <= INITIAL_COUNT; i++) {
      arr.push(`初始 Mock 数据 #${i}`);
    }
    this.items = arr;
    this.statusText = `共 ${arr.length} 条 · contentEndOffset=${CONTENT_END_OFFSET}vp`;
  }

  /**
   * 量一次底部按钮的位置并缓存为几何锚点。
   * 按钮一直在（不像提示条会被 if 摘掉），所以任何时候都能量到；
   * 提示条和安全线的位置都由它换算出来，也就不用再维护列表高度。
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
      return true;
    } catch (err) {
      return false;
    }
  }

  /** 实测末条底边的窗口坐标（px），量不到返回 NaN */
  private measureLastItemBottomPx(): number {
    try {
      const item = this.getUIContext().getComponentUtils().getRectangleById(LAST_ITEM_ID);
      if (item.size.height <= 0) {
        return Number.NaN;
      }
      return item.windowOffset.y + item.size.height;
    } catch (err) {
      return Number.NaN;
    }
  }

  /**
   * 实测末条底边越过"安全线"的距离（vp）。
   * 安全线 = 提示条顶边 - LAST_ITEM_GAP，也就是末条刚好停在提示条上方时底边的位置。
   * 返回 NaN 表示量不到（未布局 / 已滚出显示区）。
   */
  private measureOverflow(): number {
    if (!this.ensureAnchor()) {
      return Number.NaN;
    }
    const itemBottomPx: number = this.measureLastItemBottomPx();
    if (Number.isNaN(itemBottomPx)) {
      return Number.NaN;
    }
    const ui = this.getUIContext();
    const safeBottomPx: number = this.buttonTopPx
      - ui.vp2px(DISCLAIMER_MARGIN)
      - ui.vp2px(LAST_ITEM_GAP);
    return ui.px2vp(itemBottomPx - safeBottomPx);
  }

  /**
   * 根据末条位置更新提示条显隐（带"让位"闩锁）。
   *
   * 判定只看末条底边 d 相对提示条底边的位置：
   *   d 高出提示条顶边 CLEAR 之上（末条在提示条上方）  -> 显示，解锁
   *   d 落在 APPROACH 以内（接近/经过/停在提示条上）    -> 隐藏，上闩
   *   d 还在更下方                                     -> 保持隐藏状态（上过闩就不再冒头）
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
    const disclaimerBottomPx: number = this.buttonTopPx - ui.vp2px(DISCLAIMER_BOTTOM_GAP);
    const heightPx: number = ui.vp2px(DISCLAIMER_HEIGHT);
    // d > 0：末条底边还在提示条底边下方；d < 0：已经升到提示条底边之上
    const d: number = itemBottomPx - disclaimerBottomPx;
    if (d <= -(heightPx + ui.vp2px(DISCLAIMER_CLEAR))) {
      // 末条已经回到提示条上方 -> 恢复显示，并解除"让位"状态
      this.disclaimerLatched = false;
      this.disclaimerVisible = true;
    } else if (d <= ui.vp2px(DISCLAIMER_APPROACH)) {
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
   * 把列表停到"有效底部"：末条底边刚好落在提示条上方（留 LAST_ITEM_GAP 间隙）。
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
      return;
    }

    // 先给上一次修正结账：落位了才允许发起新的修正。
    // 关键点：这个判断只看"本次实际偏移有没有到达上次的目标"，
    // 不做跨手势的位置比对——否则用户再次滚到同一个底部位置时会被误判成"没进展"而不再回弹。
    if (this.pendingTarget >= 0) {
      const landed: boolean = Math.abs(current - this.pendingTarget) <= OVERFLOW_TOLERANCE;
      this.pendingTarget = -1;
      if (!landed) {
        // 上一次修正没有落位 -> 被滚动范围夹住，本次不再重试
        this.statusText = '已到滚动极限，无法再往上收回';
        return;
      }
    }

    const overflow: number = this.measureOverflow();
    let delta: number = 0;
    if (Number.isNaN(overflow)) {
      // 末条量不到（contentEndOffset 太大，已经把它推出显示区）时，
      // 只有确认到达框架底部，才按配置值推算需要收回的距离
      if (!force) {
        return;
      }
      delta = -(CONTENT_END_OFFSET - FALLBACK_INSET);
    } else {
      if (!force && overflow > OVERFLOW_TOLERANCE) {
        // 末条还在按钮下方且是用户自己停在这里的，说明没滚到"有效底部"，不干预
        return;
      }
      if (Math.abs(overflow) <= OVERFLOW_TOLERANCE) {
        // 末条已经在按钮上方刚好位置
        return;
      }
      delta = overflow;
    }

    let target: number = current + delta;
    if (target < 0) {
      target = 0;
    }
    if (Math.abs(target - current) <= OVERFLOW_TOLERANCE) {
      // 目标位置就是当前位置（已被内容边界夹住），无需滚动
      return;
    }
    this.pendingTarget = target;
    this.statusText = `自动滚回底部 · 修正 ${delta.toFixed(0)}vp`;
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
      // 顶部状态条：方便真机核对当前滚动 / 修正结果
      Row() {
        Text(this.statusText)
          .fontSize(13)
          .fontColor('#666666')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .width('100%')
      .height(36)
      .padding({ left: 16, right: 16 })
      .backgroundColor('#FAFAFA')

      Stack({ alignContent: Alignment.Bottom }) {
        List({ space: ITEM_SPACE, scroller: this.scroller }) {
          ForEach(this.items, (item: string, idx: number) => {
            ListItem() {
              Row({ space: 12 }) {
                Text(`${idx + 1}`)
                  .fontSize(13)
                  .fontColor('#FFFFFF')
                  .textAlign(TextAlign.Center)
                  .width(28)
                  .height(28)
                  .borderRadius(14)
                  .backgroundColor(idx === this.items.length - 1 ? '#E64545' : '#8C8C8C')
                Text(item)
                  .fontSize(16)
                  .fontColor('#333333')
                  .layoutWeight(1)
                  .maxLines(1)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                if (idx === this.items.length - 1) {
                  Text('末条')
                    .fontSize(12)
                    .fontColor('#E64545')
                }
              }
              .width('100%')
              .height(ITEM_HEIGHT)
              .padding({ left: 12, right: 12 })
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
        .id(LIST_ID)
        // 内容区起始偏移量：列表滚到顶部时内容与显示区边界保留 24vp
        .contentStartOffset(CONTENT_START_OFFSET)
        // 内容区末尾偏移量：故意很大，用来验证"有效底部"的自动回滚
        .contentEndOffset(CONTENT_END_OFFSET)
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
          }
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
          // 首屏那次是我们自己发起的，必然停在"真正底部"，强制修正
          const force: boolean = this.reachedRealEnd || this.awaitingInitialSettle;
          this.reachedRealEnd = false;
          this.awaitingInitialSettle = false;
          this.pullAccum = 0;
          this.settleAtEffectiveBottom(true, force);
          this.updateDisclaimerVisibility();
        })

        // "内容由AI生成"提示条：位于底部按钮正上方。
        // 末条升到它跟前时整条淡出+下移让位，末条被拉上去之后再回来。
        if (this.disclaimerVisible) {
          Row() {
            Text($r('app.string.ai_generated_disclaimer'))
              .fontSize(12)
              .fontColor('#6D28D9')
              .textAlign(TextAlign.Center)
              .height(DISCLAIMER_HEIGHT - DISCLAIMER_BORDER * 2)
              .padding({ left: 12, right: 12 })
              .backgroundColor('#FFFFFF')
              .borderRadius(DISCLAIMER_RADIUS - DISCLAIMER_BORDER)
          }
          .padding(DISCLAIMER_BORDER)
          .borderRadius(DISCLAIMER_RADIUS)
          // 边框底色：多色线性渐变
          .linearGradient({
            angle: 90,
            colors: [['#60A5FA', 0.0], ['#A78BFA', 0.33], ['#F472B6', 0.66], ['#FBBF24', 1.0]]
          })
          .clip(true)
          // 叠一层扫动的白色高光 -> 渐变边框流光
          .overlay(this.disclaimerShine())
          .id(DISCLAIMER_ID)
          .margin({ bottom: DISCLAIMER_MARGIN })
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
          .width('90%')
          .height(BUTTON_HEIGHT)
          .fontSize(16)
          .margin({ bottom: BUTTON_BOTTOM_MARGIN })
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
