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
//   5. 底部一排三个按钮：左返回（原功能），中每点一次从列表**底部**追加 5 条，
//      右模拟"流式文本"——每点一次在"开始写入 / 停止写入"之间切换。
//      追加后滚动偏移没变、末尾又长出内容 -> 列表已不在有效底部，
//      "内容由AI生成"立即让位隐藏；滚到新的底部（末条重新升到提示条上方）自动恢复显示。
//   6. 流式写入（右按钮）：数据持续写进**同一个 ListItem**（末条，文本折行、控件跟着长高），
//      写入过程中用户一直往上滑。内容是"边长边滚"的，所以：
//        - 每长一行，末条底边就被顶到安全线下方（这是数据变更，不是滚动，不会自动结算）；
//        - 用户每次滚动停下时仍按硬约束把末条拉回安全线（回弹不被内容增长打断）；
//        - 再点一次「停止写入」立即结算一次，把末条拉回安全线（这就是要验证的"还能正常回弹"）。
//      停止时用 force=false：用户要是已经翻到列表中段（末条不在屏上），不该被拽回底部。
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
  /** 底部按钮之间的水平间距（并排放置，整体宽度与原来的单按钮一致） */
  buttonGap: number;
  /**
   * 底部按钮文字的左右内边距。
   *
   * 必须显式给一个小值：Button（Capsule）默认左右各 16vp，
   * 一行并排三个按钮后每个只剩 ~46vp，减去默认内边距就只剩 ~14vp 的文案宽度，
   * 真机表现为"文字折成两行、第二行被按钮底边裁掉"（实测：文案区仅 68px 宽、
   * 第二行 glyph 画到按钮外）。给 2vp 后 4 个汉字（36vp）才放得下。
   */
  buttonPadding: number;
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
  buttonGap: 12,
  buttonPadding: 8,
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
 *   按钮    y 173~203，底边 d=86.5 处可用 156vp，取 66%=154vp，
 *          一行并排三个（每个 ~46vp，文案区 42vp，4 个字 36vp 刚好放得下）
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
  // 一行并排三个按钮，每个只剩 (66% - 2*8) / 3 ≈ 46vp；
  // 字号 9 + 内边距 2 => 文案区 42vp，4 个汉字 36vp 放得下（字号 10 时 40vp 太挤）
  buttonFontSize: 9,
  buttonGap: 8,
  buttonPadding: 2,
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
/** 底部中间按钮（每点一次从列表底部插入 5 条） */
const APPEND_BUTTON_ID: string = 'offset_append_button';
/** 底部最右按钮（模拟流式文本：点一次开始写入，再点一次停止并回弹） */
const STREAM_BUTTON_ID: string = 'offset_stream_button';
/** 底部按钮文案固定单行：一行并排三个本来就没有折行的空间，
 *  一旦折行第二行会被 30vp 高的按钮底边裁掉（真机修过的样式问题） */
const BUTTON_LABEL_MAX_LINES: number = 1;
/** 提示条不参与几何换算（位置由按钮锚点推出），id 仅用于调试定位 */
const DISCLAIMER_ID: string = 'offset_disclaimer';
/** 点击底部中间按钮时插入的条数 */
const APPEND_SIZE: number = 5;
/**
 * 底部插入后的"布局保护窗"（ms）。
 * ForEach 重渲染之前，旧末条仍然带着 LAST_ITEM_ID，getRectangleById 量到的还是插入前的位置；
 * 这期间不允许"末条回到提示条上方"这个条件解锁提示条，否则插入后会被旧布局立刻弹回来。
 */
const APPEND_LAYOUT_GUARD_MS: number = 500;

// ---- 流式文本（底部最右按钮）----
/** 每两次写入之间的间隔（ms）：模拟逐字下发的流式输出。
 *
 *  取 350ms（约 5.7 字/s、约 2s 长一行）而不是更快的 150ms：
 *  真机验证全程约 15~20s 都要处在"内容还在长"的状态里，
 *  这样"停止写入"那一下才一定还有内容增长留下的落差可以回弹（见 AGENTS.md）。
 *  写太快会在几秒内把行数顶到上限、之后就没有新落差了，验证会变成空跑。 */
const STREAM_INTERVAL_MS: number = 350;
/** 每次写入的字符数 */
const STREAM_CHARS_PER_TICK: number = 2;
/** 一轮最多写多少字：写满就在**同一条**里重开一轮（否则末条会无限变高） */
const STREAM_MAX_CHARS: number = 600;
/**
 * 流式写入时这一条允许的最大行数（内容长高的上限）。
 * 取 20 行（约 336vp）：写入约 5~10 字/s，长满要二三十秒，
 * 手动演示时"一边写一边滑"能一直保持内容在长；
 * 长满之后只是不再长高，回弹逻辑照常（末条底边仍在安全线上）。
 */
const STREAM_MAX_LINES: number = 20;
/** 流式写入的语料（循环取字符，模拟模型逐字输出） */
const STREAM_SENTENCE: string =
  '这是一段模拟流式输出的文本，用来验证列表内容在持续增长的过程中还能不能正常回弹。';

/**
 * 列表项数据。
 *
 * 必须带一个**稳定 id**：流式写入每 150ms 改一次末条的文本，
 * 如果还像以前那样用文本本身当 ForEach 的 key，每写一次都会被当成"新数据"，
 * 整条 ListItem 重建（id 会飘、无法验证"一直在同一个 listitem 里写"）。
 *
 * 但**只有稳定 key 还不够**：ForEach 的键值不变时会复用旧组件、**不再执行 itemGenerator**，
 * 所以"键值稳定 + 把 Text(item.text) 写在 itemGenerator 里"这个组合在真机上根本不刷新
 * ——实测写入到 492 字，控件树里那条还是 15 字、项高停在 40vp 一直没长高。
 * 正确做法是 @Observed + 子组件 @ObjectLink：改的是同一个对象的属性，
 * 由框架只通知这一行重渲染（父组件和 ForEach 都不用重跑，反而更省）。
 */
@Observed
class OffsetItem {
  id: number;
  text: string;
  /** 是否做过流式写入目标：目标项允许折行、允许随文本长高；停写后保持长文本不回收 */
  growing: boolean;

  constructor(id: number, text: string) {
    this.id = id;
    this.text = text;
    this.growing = false;
  }
}

/**
 * 列表行。
 *
 * 拆成子组件就是为了用 @ObjectLink 盯住**这一条数据对象**：
 * 流式写入改 `item.text` / `item.growing` 时，只有这一行重渲染。
 */
@Component
struct OffsetRow {
  @ObjectLink item: OffsetItem;
  /** 序号（1 起） */
  @Prop index: number = 1;
  /** 是不是末条：红色徽标 + 浅红底 + 「末条」标签 */
  @Prop isLast: boolean = false;
  /** 几何参数（常量，构造时由父组件按设备给一次） */
  geom: OffsetGeometry = PHONE_GEOMETRY;

  build() {
    Row({ space: this.geom.itemRowSpace }) {
      Text(`${this.index}`)
        .fontSize(this.geom.badgeFontSize)
        .fontColor('#FFFFFF')
        .textAlign(TextAlign.Center)
        .width(this.geom.badgeSize)
        .height(this.geom.badgeSize)
        .borderRadius(this.geom.badgeSize / 2)
        .backgroundColor(this.isLast ? '#E64545' : '#8C8C8C')
      Text(this.item.text)
        .fontSize(this.geom.itemFontSize)
        .fontColor('#333333')
        .layoutWeight(1)
        .maxLines(this.item.growing ? STREAM_MAX_LINES : 1)
        .textOverflow({
          overflow: this.item.growing ? TextOverflow.Clip : TextOverflow.Ellipsis
        })
      if (this.isLast) {
        Text('末条')
          .fontSize(this.geom.tagFontSize)
          .fontColor('#E64545')
      }
    }
    .width('100%')
    // 普通项：内容撑不到项高，用 minHeight 兜住；
    // 流式写入那一项：文本一行行长出来，整条跟着变高（minHeight 不再起作用）
    .constraintSize({ minHeight: this.geom.itemHeight })
    .padding({ left: this.geom.itemPadding, right: this.geom.itemPadding })
    .backgroundColor(this.isLast ? '#FFF1F0' : '#F2F3F5')
    .borderRadius(8)
    .border({
      width: this.isLast ? 1 : 0,
      color: '#E64545'
    })
  }
}

@Entry
@Component
struct OffsetListPage {
  @State items: OffsetItem[] = [];
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
  /** 从底部插入数据的波次（排障用） */
  private appendNo: number = 0;
  /** 最近一次"从底部插入"的时刻（Date.now()），配合 APPEND_LAYOUT_GUARD_MS 挡旧布局 */
  private appendAtMs: number = 0;
  /** 列表项自增 id（流式写入需要稳定的 key，不能用文本） */
  private nextItemId: number = 0;

  /** 流式写入是否进行中（决定最右按钮的文案/颜色，以及末条是否允许折行长高） */
  @State streaming: boolean = false;
  /** 流式写入的目标列表项 id：锁定之后一直写这**同一条** */
  private streamItemId: number = -1;
  /** 第几轮（一轮写满 STREAM_MAX_CHARS 字就在同一条里重开一轮） */
  private streamRound: number = 0;
  /** 本轮已写入的字数 */
  private streamWritten: number = 0;
  /** 语料游标 */
  private streamCursor: number = 0;
  /** 流式写入定时器句柄（-1 = 没在跑） */
  private streamTimer: number = -1;

  /** 建一条列表项（自动分配稳定 id） */
  private newItem(text: string): OffsetItem {
    this.nextItemId++;
    return new OffsetItem(this.nextItemId, text);
  }

  aboutToAppear(): void {
    const arr: OffsetItem[] = [];
    for (let i = 1; i <= INITIAL_COUNT; i++) {
      arr.push(this.newItem(`初始 Mock 数据 #${i}`));
    }
    this.items = arr;
    this.statusText = `共 ${arr.length} 条 · contentEndOffset=${this.geom.contentEndOffset}vp`;
  }

  /** 页面销毁（返回上一页）时一定要停掉定时器，否则离开页面后还在写 */
  aboutToDisappear(): void {
    this.clearStreamTimer();
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
      // 滚动过程中不用在这儿处理：末条往下走会先经过让位窗口、上闩隐藏。
      // "底部插入新数据"是特例（末尾直接长出内容、末条换人），由 appendAtBottom 主动让位。
      return;
    }
    const ui = this.getUIContext();
    const disclaimerBottomPx: number = this.buttonTopPx - ui.vp2px(this.geom.disclaimerBottomGap);
    const heightPx: number = ui.vp2px(this.geom.disclaimerHeight);
    // d > 0：末条底边还在提示条底边下方；d < 0：已经升到提示条底边之上
    const d: number = itemBottomPx - disclaimerBottomPx;
    if (d <= -(heightPx + ui.vp2px(this.geom.disclaimerClear))) {
      // 末条已经回到提示条上方 -> 恢复显示，并解除"让位"状态。
      // 例外：刚在底部插过数据时，量到的可能还是"插入前的旧末条"（还挂在安全线上），
      // 这期间不许解锁，否则插入后刚隐藏的提示条会被旧布局立刻弹回来。
      if (Date.now() - this.appendAtMs < APPEND_LAYOUT_GUARD_MS) {
        hilog.info(DBG_DOMAIN, DBG_TAG, 'disclaimer: 插入后保护窗内，忽略旧布局的解锁判定');
        return;
      }
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
    // 流式写入期间必须用**瞬移**修正（动画版回弹在真机上根本落不了地）：
    // 内容持续增长时列表被框架钉在"真正底部"，还在飞的回弹动画会被下一次内容变更取消、
    // 偏移又被打回末尾，于是修正永远不生效、还会以 4Hz 反复重发（真机日志：
    // "发起修正 delta=-71.6 target=1359 animate=true" 每 250ms 一条，末条始终停在 134px）。
    // 瞬移在下一帧就落地，落地后偏移已经离开末尾（不再是 isAtEnd），
    // 后续增长不会再把它拽回真底——回弹才算真的发生。
    const useAnimation: boolean = animate && !this.streaming;
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `settle: 发起修正 delta=${delta.toFixed(1)} target=${target.toFixed(1)} ` +
      `animate=${useAnimation}${useAnimation === animate ? '' : '（流式写入中，改用瞬移）'}`);
    this.scroller.scrollTo({
      xOffset: 0,
      yOffset: target,
      animation: useAnimation ? { duration: BOUNCE_DURATION, curve: Curve.EaseOut } : false
    });
  }

  /** 加载下一页：插入到列表开头（同步插入，不做假延时） */
  private loadMore(): void {
    this.batchNo++;
    const fresh: OffsetItem[] = [];
    for (let i = 1; i <= PAGE_SIZE; i++) {
      fresh.push(this.newItem(`下拉加载第${this.batchNo}批 · ${i}/${PAGE_SIZE}`));
    }
    this.items = fresh.concat(this.items);
    this.pendingTarget = -1;
    this.statusText = `已加载 ${this.batchNo * PAGE_SIZE} 条 · 当前共 ${this.items.length} 条`;
    hilog.info(DBG_DOMAIN, DBG_TAG, `loadMore: 第${this.batchNo}批，共 ${this.items.length} 条`);
  }

  /**
   * 从列表**底部**插入一批数据（底部右边按钮的功能）。
   *
   * 与 loadMore（顶部下拉、插到列表开头）相对：这里是往末尾追加，
   * 所以插入后"末条"会换成新数据里的最后一条，而它此刻还在屏幕下方。
   * 数据一变就必须重判提示条：新末条不在显示区里，说明列表已经**不在有效底部**，
   * "内容由AI生成"不该继续挂在屏上。
   */
  private appendAtBottom(): void {
    this.appendNo++;
    const fresh: OffsetItem[] = [];
    for (let i = 1; i <= APPEND_SIZE; i++) {
      fresh.push(this.newItem(`底部插入第${this.appendNo}波 · ${i}/${APPEND_SIZE}`));
    }
    this.items = this.items.concat(fresh);
    this.statusText = `已从底部插入 ${this.appendNo * APPEND_SIZE} 条 · 当前共 ${this.items.length} 条`;
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `appendAtBottom: 第${this.appendNo}波 +${APPEND_SIZE} 条，共 ${this.items.length} 条`);
    // 往末尾追加数据、滚动偏移没动 -> 这一刻起列表**一定不在有效底部**了
    // （末尾又长出 5 条，全在屏幕下方），所以不用等测量：直接让位 + 上闩。
    // 等用户滚到新的底部、末条重新升到提示条上方，位置判定会自动解锁重新显示。
    this.appendAtMs = Date.now();
    this.disclaimerVisible = false;
    this.disclaimerLatched = true;
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `disclaimer: 底部插入新数据（共 ${this.items.length} 条）-> 立即让位隐藏`);
  }

  // ---- 流式文本（底部最右按钮）----

  /** 最右按钮：不在写 -> 开始写；正在写 -> 停止并立即结算（回弹） */
  private toggleStream(): void {
    if (this.streaming) {
      this.stopStream();
    } else {
      this.startStream();
    }
  }

  private clearStreamTimer(): void {
    if (this.streamTimer >= 0) {
      clearInterval(this.streamTimer);
      this.streamTimer = -1;
    }
  }

  /**
   * 开始流式写入：目标锁定为**当前末条**，此后 150ms 往这一条追加一次字符。
   *
   * 锁定 id 而不是"每次都取最后一条"：这样即使用户中途点了"插入5条"，
   * 写入对象仍然是同一个 listitem（诉求就是"一直往一个 listitem 中写入数据"）。
   */
  private startStream(): void {
    if (this.items.length === 0) {
      return;
    }
    this.clearStreamTimer();
    this.streaming = true;
    this.streamRound++;
    this.streamWritten = 0;
    this.streamCursor = 0;
    const last: number = this.items.length - 1;
    this.streamItemId = this.items[last].id;
    // growing 一旦置上就不再回收：停写后这一条仍然保持多行长文本（真实流式输出的样子），
    // 否则停止瞬间项高缩回 40vp，屏上内容会突然消失、几何也会跳一下。
    this.items[last].growing = true;
    this.items[last].text = `流式输出 ${this.streamRound}：`;
    this.statusText = `流式写入中 · 第${this.streamRound}轮 · 0 字`;
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `stream: 开始写入 itemId=${this.streamItemId}（第${this.streamRound}轮）`);
    // 写入不改滚动偏移 -> 不会触发 onScrollStop，所以每写一次要自己刷一次提示条
    this.streamTimer = setInterval(() => {
      this.streamTick();
    }, STREAM_INTERVAL_MS);
  }

  /**
   * 停止流式写入并**立即结算一次**。
   *
   * 这是本次要验证的关键动作：内容一直在长，末条被一行行顶到安全线下方
   * （数据变更不产生滚动事件，所以期间没有任何一次自动回弹）；
   * 停止时按当前实测结算 -> 末条立刻回弹到安全线。
   *
   * force=false：如果用户已经翻到列表中段（末条不在屏上），不要把他拽回底部。
   */
  private stopStream(): void {
    this.clearStreamTimer();
    this.streaming = false;
    this.statusText = `流式写入已停止 · ${this.streamRound} 轮 · 末条 ${this.streamWritten} 字`;
    hilog.info(DBG_DOMAIN, DBG_TAG,
      `stream: 停止写入（第${this.streamRound}轮，本轮 ${this.streamWritten} 字）-> 立即结算`);
    // 停下来了，重新量一次锚点（内容长高可能已经改过布局）
    this.buttonTopPx = -1;
    this.settleAtEffectiveBottom(true, false);
    this.updateDisclaimerVisibility();
  }

  /** 一次写入：往锁定项追加 STREAM_CHARS_PER_TICK 个字；写满一轮就在同一条里重开 */
  private streamTick(): void {
    if (this.streamItemId < 0) {
      this.clearStreamTimer();
      return;
    }
    const idx: number = this.findStreamIndex();
    if (idx < 0) {
      hilog.info(DBG_DOMAIN, DBG_TAG, 'stream: 目标列表项已不在，停止写入');
      this.stopStream();
      return;
    }
    if (this.streamWritten >= STREAM_MAX_CHARS) {
      // 写满一轮：同一条重新开始，否则末条会一直长高
      this.streamRound++;
      this.streamWritten = 0;
      this.streamCursor = 0;
      this.items[idx].text = `流式输出 ${this.streamRound}：`;
      this.statusText = `流式写入中 · 第${this.streamRound}轮（上一轮写满 ${STREAM_MAX_CHARS} 字，同一条重开）`;
      hilog.info(DBG_DOMAIN, DBG_TAG, `stream: 第${this.streamRound}轮（同一条重开）`);
      return;
    }
    let chunk: string = '';
    for (let i = 0; i < STREAM_CHARS_PER_TICK; i++) {
      chunk += STREAM_SENTENCE.charAt(this.streamCursor % STREAM_SENTENCE.length);
      this.streamCursor++;
    }
    this.streamWritten += STREAM_CHARS_PER_TICK;
    // 改的是同一个 @Observed 对象的属性 -> 框架只通知对应那一行（@ObjectLink）重渲染。
    // 不换数组、不换对象，所以父组件和 ForEach 都不用重跑（真机上这点很要紧：
    // 每 150ms 重跑一次带 70 项的 ForEach 会让 dumpLayout 都变得不可靠）。
    this.items[idx].text = this.items[idx].text + chunk;
    this.statusText = `流式写入中 · 第${this.streamRound}轮 · ${this.streamWritten} 字`;
    // 末条被顶到提示条跟前时提示条要让位；这里量到的可能是"这一次增长之前"的布局，
    // 但让位判定是上闩的（只会更早隐藏、不会来回抖），下一帧/下一次滚动会自然对齐。
    this.updateDisclaimerVisibility();
  }

  /** 锁定项在 items 里的下标（-1 = 找不到） */
  private findStreamIndex(): number {
    for (let i = 0; i < this.items.length; i++) {
      if (this.items[i].id === this.streamItemId) {
        return i;
      }
    }
    return -1;
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
          ForEach(this.items, (item: OffsetItem, idx: number) => {
            ListItem() {
              OffsetRow({
                item: item,
                index: idx + 1,
                isLast: idx === this.items.length - 1,
                geom: this.geom
              })
            }
            // 末条单独一个 id，供 getRectangleById 实测位置
            .id(idx === this.items.length - 1 ? LAST_ITEM_ID : `offset_item_${idx}`)
          }, (item: OffsetItem) => `${item.id}`)
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

        // 底部固定按钮：一行并排三个，悬浮在 List 之上，不随列表滑动。
        //   左：保持原功能（返回上一页），id 仍是 offset_bottom_button——
        //       它是安全线计算的几何锚点（安全线 = 这排按钮的顶边 - 34vp）。
        //   中：每点一次从列表**底部**追加 5 条数据。
        //   右：模拟流式文本——点一次在"开始写入 / 停止写入"之间切换，
        //       写入目标是**同一个 listitem**（末条），停止时立即结算回弹。
        // 整排宽度与原来的单按钮一致（原来的 buttonWidth），每个按钮用 layoutWeight 均分。
        //
        // 样式注意（真机修过）：一行三个后每个只剩 ~46vp，而 Button(Capsule) 默认左右
        // 各 16vp 内边距 -> 文案区只剩 ~14vp，真机表现为"文案折成两行、第二行被 30vp 高的
        // 按钮底边裁掉"。所以这里显式压小内边距 + 文案固定单行（标签也相应改短）。
        Row({ space: this.geom.buttonGap }) {
          Button($r('app.string.offset_bottom_button'))
            .id(BUTTON_ID)
            .layoutWeight(1)
            .height(this.geom.buttonHeight)
            .fontSize(this.geom.buttonFontSize)
            .padding({ left: this.geom.buttonPadding, right: this.geom.buttonPadding })
            .labelStyle({ maxLines: BUTTON_LABEL_MAX_LINES, overflow: TextOverflow.Ellipsis })
            .onClick(() => {
              router.back();
            })
          Button($r('app.string.offset_append_button'))
            .id(APPEND_BUTTON_ID)
            .layoutWeight(1)
            .height(this.geom.buttonHeight)
            .fontSize(this.geom.buttonFontSize)
            .padding({ left: this.geom.buttonPadding, right: this.geom.buttonPadding })
            .labelStyle({ maxLines: BUTTON_LABEL_MAX_LINES, overflow: TextOverflow.Ellipsis })
            .onClick(() => {
              this.appendAtBottom();
            })
          Button(this.streaming ? $r('app.string.offset_stream_stop_button')
            : $r('app.string.offset_stream_button'))
            .id(STREAM_BUTTON_ID)
            .layoutWeight(1)
            .height(this.geom.buttonHeight)
            .fontSize(this.geom.buttonFontSize)
            .padding({ left: this.geom.buttonPadding, right: this.geom.buttonPadding })
            .labelStyle({ maxLines: BUTTON_LABEL_MAX_LINES, overflow: TextOverflow.Ellipsis })
            .onClick(() => {
              this.toggleStream();
            })
        }
        .width(this.geom.buttonWidth)
        .margin({ bottom: this.geom.buttonBottomMargin })
      }
      .width('100%')
      .layoutWeight(1)
    }
    .width('100%')
    .height('100%')
  }
}
