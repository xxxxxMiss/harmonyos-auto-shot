// 偏移列表页：List 配置 contentStartOffset / contentEndOffset + 底部固定按钮
//
// 场景：contentEndOffset 被**故意设置得很大**（见 CONTENT_END_OFFSET）。
// 因此框架意义上的"真正底部"（scrollEdge(Edge.Bottom) / onReachEnd 到达的位置）
// 并不是我们要的底部——停在那个位置时，末条离按钮还有一大截空白。
//
// 本页要验证的"底部" = 最后一条记录刚好处在底部按钮上方。
//   1. List 底部固定一个按钮，不跟随列表滑动（Stack 悬浮）
//   2. 初始加载自动滚到"真正底部"，再自动滚回"有效底部"（末条贴着按钮上方）
//   3. 顶部下拉一次加载 10 条（插入到列表开头）
//   4. 只要滚到底部（拖动或惯性甩动、速度快慢都一样），都自动停到"有效底部"：
//        末条被按钮压住 -> 往前滚，把它顶上来
//        末条离按钮太远 -> 往回滚，把多余空白收掉
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

/** 按钮占据的高度：列表底边 -> 按钮顶边（vp） */
const BOTTOM_BAR_AREA: number = BUTTON_HEIGHT + BUTTON_BOTTOM_MARGIN;
/**
 * 安全线：末条底边允许到达的最低位置（相对列表底边的内缩量，vp）。
 * 末条底边 == 列表高度 - SAFE_BOTTOM_INSET 时，它刚好停在按钮上方。
 * 注意这里只跟按钮几何有关，与 contentEndOffset 无关。
 */
const SAFE_BOTTOM_INSET: number = BOTTOM_BAR_AREA + LAST_ITEM_GAP;

/** 内容区起始偏移量（vp） */
const CONTENT_START_OFFSET: number = 24;
/**
 * 内容区末尾偏移量（vp）——故意设置得很大（是 SAFE_BOTTOM_INSET 的 3 倍多），
 * 用来验证框架"真正底部"远离"有效底部"时，如何自动把多余空白收回来。
 */
const CONTENT_END_OFFSET: number = 270;

/** 初始 Mock 数据条数 */
const INITIAL_COUNT: number = 30;
/** 每次下拉加载条数 */
const PAGE_SIZE: number = 10;
/** 顶部下拉触发阈值（vp） */
const PULL_DOWN_THRESHOLD: number = 56;
/** 自动滚回"有效底部"的动画时长（ms） */
const BOUNCE_DURATION: number = 260;
/** 判定"已经在正确位置"的容差（vp） */
const OVERFLOW_TOLERANCE: number = 0.5;

@Entry
@Component
struct OffsetListPage {
  @State items: string[] = [];
  @State statusText: string = '';
  private scroller: Scroller = new Scroller();
  /** 列表可视区高度（vp），用于换算"安全线" */
  private listHeight: number = 0;
  /** 下拉批次计数 */
  private batchNo: number = 0;
  /** 是否正在加载 */
  private loading: boolean = false;
  /** 首屏是否已经自动滚到底部 */
  private initialScrolled: boolean = false;
  /** 本次滚动过程中是否到达过框架意义上的底部（onReachEnd） */
  private reachedRealEnd: boolean = false;
  /** 上一次发起修正时的偏移 / 目标偏移，用于识别"被滚动范围夹住"并终止重试 */
  private lastSnapFrom: number = -1;
  private lastSnapTo: number = -1;
  /** 下拉手势起点（vp），-1 表示无有效手势 */
  private touchStartY: number = -1;
  /** 本次手势是否已经触发过加载 */
  private pulledDown: boolean = false;

  aboutToAppear(): void {
    const arr: string[] = [];
    for (let i = 1; i <= INITIAL_COUNT; i++) {
      arr.push(`初始 Mock 数据 #${i}`);
    }
    this.items = arr;
    this.statusText = `共 ${arr.length} 条 · contentEndOffset=${CONTENT_END_OFFSET}vp`;
  }

  /** Length（可能是 number / string）转 vp 数值 */
  private toVp(value: Length): number {
    if (typeof value === 'number') {
      return value;
    }
    if (typeof value === 'string') {
      const parsed: number = Number.parseFloat(value);
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    return 0;
  }

  /**
   * 把列表停到"有效底部"：末条底边刚好落在安全线上（按钮上方）。
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
    const count: number = this.items.length;
    if (count === 0 || this.listHeight <= 0) {
      return;
    }
    const safeBottom: number = this.listHeight - SAFE_BOTTOM_INSET;
    let overflow: number = 0;
    let measured: boolean = false;
    try {
      // getItemRect 只对显示区内的子项有效，取不到时会抛异常
      const rect: RectResult = this.scroller.getItemRect(count - 1);
      const itemTop: number = rect.y;
      const itemBottom: number = rect.y + rect.height;
      if (rect.height > 0 && itemBottom > 0 && itemTop < this.listHeight) {
        overflow = itemBottom - safeBottom;
        measured = true;
      }
    } catch (err) {
      measured = false;
    }

    if (!measured) {
      // 末条量不到（contentEndOffset 太大，已经把它推出显示区）时，
      // 只有确认到达框架底部，才按配置值推算需要收回的距离
      if (!force) {
        return;
      }
      overflow = -(CONTENT_END_OFFSET - SAFE_BOTTOM_INSET);
    } else if (!force && overflow > OVERFLOW_TOLERANCE) {
      // 末条还在安全线下方且是用户自己停在这里的，说明没滚到"有效底部"，不干预
      return;
    }
    if (Math.abs(overflow) <= OVERFLOW_TOLERANCE) {
      // 末条已经在按钮上方刚好位置
      return;
    }

    let current: number = 0;
    try {
      current = this.scroller.currentOffset().yOffset;
    } catch (err) {
      return;
    }
    let target: number = current + overflow;
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
      this.statusText = `已到滚动极限 · 末条底边 ${(safeBottom + overflow).toFixed(0)}vp / 安全线 ${safeBottom.toFixed(0)}vp`;
      return;
    }
    this.lastSnapFrom = current;
    this.lastSnapTo = target;
    this.statusText = `自动滚回底部 · 修正 ${overflow.toFixed(0)}vp · 安全线 ${safeBottom.toFixed(0)}vp`;
    this.scroller.scrollTo({
      xOffset: 0,
      yOffset: target,
      animation: animate ? { duration: BOUNCE_DURATION, curve: Curve.EaseOut } : false
    });
  }

  /** 首屏：先拉到框架意义上的"真正底部"，再自动滚回"有效底部" */
  private autoScrollToBottom(): void {
    if (this.initialScrolled || this.items.length === 0) {
      return;
    }
    this.initialScrolled = true;
    // onAppear 时可能还没完成首帧布局，延后一帧再滚
    setTimeout(() => {
      try {
        this.scroller.scrollEdge(Edge.Bottom);
      } catch (err) {
        return;
      }
      this.lastSnapFrom = -1;
      this.lastSnapTo = -1;
      // 布局稳定后量一次末条位置，带动画把多余空白收回来
      setTimeout(() => {
        this.settleAtEffectiveBottom(true, true);
      }, 80);
    }, 80);
  }

  /** 顶部下拉：每次插入 10 条到列表开头 */
  private loadMoreByPullDown(): void {
    if (this.loading) {
      return;
    }
    this.loading = true;
    this.batchNo++;
    this.statusText = `下拉加载中… 第 ${this.batchNo} 批`;
    setTimeout(() => {
      const fresh: string[] = [];
      for (let i = 1; i <= PAGE_SIZE; i++) {
        fresh.push(`下拉加载第${this.batchNo}批 · ${i}/${PAGE_SIZE}`);
      }
      // 新数据插到开头
      this.items = fresh.concat(this.items);
      this.lastSnapFrom = -1;
      this.lastSnapTo = -1;
      this.loading = false;
      this.statusText = `已加载 ${this.batchNo * PAGE_SIZE} 条 · 当前共 ${this.items.length} 条`;
    }, 300);
  }

  /** 下拉手势识别：仅在列表处于顶部且继续向下拖拽时触发 */
  private handleTouch(event: TouchEvent): void {
    if (event.type === TouchType.Up || event.type === TouchType.Cancel) {
      this.touchStartY = -1;
      this.pulledDown = false;
      return;
    }
    if (event.touches.length === 0) {
      return;
    }
    const y: number = event.touches[0].y;
    if (event.type === TouchType.Down) {
      this.touchStartY = y;
      this.pulledDown = false;
      // 新手势：允许重新尝试把列表收回"有效底部"
      this.lastSnapFrom = -1;
      this.lastSnapTo = -1;
      return;
    }
    if (event.type !== TouchType.Move) {
      return;
    }
    if (this.pulledDown || this.loading || this.touchStartY < 0) {
      return;
    }
    let atTop: boolean = false;
    try {
      atTop = this.scroller.currentOffset().yOffset <= 1;
    } catch (err) {
      return;
    }
    if (atTop && (y - this.touchStartY) >= PULL_DOWN_THRESHOLD) {
      this.pulledDown = true;
      this.loadMoreByPullDown();
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
          }, (item: string) => item)
        }
        .width('100%')
        .height('100%')
        .id('offset_list')
        // 内容区起始偏移量：列表滚到顶部时内容与显示区边界保留 24vp
        .contentStartOffset(CONTENT_START_OFFSET)
        // 内容区末尾偏移量：故意很大，用来验证"有效底部"的自动回滚
        .contentEndOffset(CONTENT_END_OFFSET)
        .scrollBar(BarState.Auto)
        .edgeEffect(EdgeEffect.Spring, { alwaysEnabled: true })
        .onAreaChange((_oldValue: Area, newValue: Area) => {
          this.listHeight = this.toVp(newValue.height);
        })
        .onAppear(() => {
          this.autoScrollToBottom();
        })
        // 到达框架意义上的底部：先记标记，等滚动停下来再统一修正
        .onReachEnd(() => {
          this.reachedRealEnd = true;
        })
        // 滚动停止（含惯性甩动结束）：只要落点越过"有效底部"就自动收回。
        // 放在这里而不是 onReachEnd，是为了不打断进行中的甩动动画；
        // 不管用户拖动还是甩动、速度快慢，最终都会走到这里。
        .onScrollStop(() => {
          const force: boolean = this.reachedRealEnd;
          this.reachedRealEnd = false;
          this.settleAtEffectiveBottom(true, force);
        })
        .onTouch((event: TouchEvent) => {
          this.handleTouch(event);
        })

        // 底部固定按钮：悬浮在 List 之上，不随列表滑动
        Button($r('app.string.offset_bottom_button'))
          .id('offset_list_bottom_button')
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
