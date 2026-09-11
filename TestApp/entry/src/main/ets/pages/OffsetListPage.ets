// 偏移列表页：List 配置 contentStartOffset / contentEndOffset + 底部固定按钮
//
// 场景：contentEndOffset 被**故意设置得很大**（见 CONTENT_END_OFFSET）。
// 因此框架意义上的"真正底部"（scrollEdge(Edge.Bottom) / onReachEnd 到达的位置）
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
/** 末条底边与按钮顶边之间保留的间隙（vp） */
const LAST_ITEM_GAP: number = 12;

/**
 * 末条量不到时的兜底内缩量（vp）。正常路径不需要它：
 * 修正距离由"末条实测坐标"和"按钮实测坐标"直接相减得到，与列表高度无关。
 */
const FALLBACK_INSET: number = BUTTON_HEIGHT + BUTTON_BOTTOM_MARGIN + LAST_ITEM_GAP;

/** 内容区起始偏移量（vp） */
const CONTENT_START_OFFSET: number = 24;
/**
 * 内容区末尾偏移量（vp）——故意设置得很大（是兜底内缩量的 3 倍多），
 * 用来验证框架"真正底部"远离"有效底部"时，如何自动把多余空白收回来。
 */
const CONTENT_END_OFFSET: number = 270;

/** 初始 Mock 数据条数 */
const INITIAL_COUNT: number = 30;
/** 每次加载条数 */
const PAGE_SIZE: number = 10;
/** 在顶部继续往回滚的累加阈值（vp），超过就加载下一页 */
const PULL_DOWN_THRESHOLD: number = 20;
/** 自动滚回"有效底部"的动画时长（ms） */
const BOUNCE_DURATION: number = 260;
/** 判定"已经在正确位置"的容差（vp） */
const OVERFLOW_TOLERANCE: number = 0.5;

/** 列表 / 末条 / 底部按钮的 id，用 getRectangleById 实测位置 */
const LIST_ID: string = 'offset_list';
const LAST_ITEM_ID: string = 'offset_last_item';
const BUTTON_ID: string = 'offset_bottom_button';

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
  /** 上一次发起修正时的偏移 / 目标偏移，用于识别"被滚动范围夹住"并终止重试 */
  private lastSnapFrom: number = -1;
  private lastSnapTo: number = -1;

  aboutToAppear(): void {
    const arr: string[] = [];
    for (let i = 1; i <= INITIAL_COUNT; i++) {
      arr.push(`初始 Mock 数据 #${i}`);
    }
    this.items = arr;
    this.statusText = `共 ${arr.length} 条 · contentEndOffset=${CONTENT_END_OFFSET}vp`;
  }

  /**
   * 实测末条底边越过"按钮顶边 - 间隙"的距离（vp）。
   *
   * 直接拿两个组件的实测坐标相减，不依赖列表高度：
   * getRectangleById 拿到的是组件当前所在位置（px，窗口坐标系），
   * 相减得到需要滚动的像素量，再用 px2vp 换成 scrollTo 需要的 vp。
   * 返回 NaN 表示量不到（未布局 / 已滚出显示区）。
   */
  private measureOverflow(): number {
    try {
      const ui = this.getUIContext();
      const utils = ui.getComponentUtils();
      const lastItem = utils.getRectangleById(LAST_ITEM_ID);
      const button = utils.getRectangleById(BUTTON_ID);
      if (lastItem.size.height <= 0 || button.size.height <= 0) {
        return Number.NaN;
      }
      const itemBottomPx: number = lastItem.windowOffset.y + lastItem.size.height;
      const safeBottomPx: number = button.windowOffset.y - ui.vp2px(LAST_ITEM_GAP);
      return ui.px2vp(itemBottomPx - safeBottomPx);
    } catch (err) {
      return Number.NaN;
    }
  }

  /**
   * 把列表停到"有效底部"：末条底边刚好落在按钮上方（留 LAST_ITEM_GAP 间隙）。
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

    let current: number = 0;
    try {
      current = this.scroller.currentOffset().yOffset;
    } catch (err) {
      return;
    }
    let target: number = current + delta;
    if (target < 0) {
      target = 0;
    }
    if (Math.abs(target - current) <= OVERFLOW_TOLERANCE) {
      // 目标位置就是当前位置（已被内容边界夹住），无需滚动
      return;
    }
    if (this.lastSnapFrom >= 0
      && Math.abs(current - this.lastSnapFrom) <= OVERFLOW_TOLERANCE
      && Math.abs(current - this.lastSnapTo) > OVERFLOW_TOLERANCE) {
      // 上一次从同一位置发起的修正没有产生任何位移 -> 被滚动范围夹住，不再重试
      this.statusText = `已到滚动极限 · 还需修正 ${delta.toFixed(0)}vp`;
      return;
    }
    this.lastSnapFrom = current;
    this.lastSnapTo = target;
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
    this.lastSnapFrom = -1;
    this.lastSnapTo = -1;
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
          // 首屏直接拉到框架意义上的"真正底部"；
          // 滚动停下来后由 onScrollStop 自动收回到"有效底部"，不靠定时器
          if (this.initialScrolled) {
            return;
          }
          this.initialScrolled = true;
          this.lastSnapFrom = -1;
          this.lastSnapTo = -1;
          try {
            this.scroller.scrollEdge(Edge.Bottom);
          } catch (err) {
            this.initialScrolled = false;
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
          const force: boolean = this.reachedRealEnd;
          this.reachedRealEnd = false;
          this.pullAccum = 0;
          this.settleAtEffectiveBottom(true, force);
        })

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
