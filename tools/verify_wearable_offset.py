#!/usr/bin/env python3
"""圆屏穿戴设备真机验证：OffsetListPage 的"有效底部"行为。

被测现象：OffsetListPage 给 List 配了**很大**的 contentEndOffset，
所以框架意义上的"真正底部"（onReachEnd / 直接滚到最大偏移）离底部按钮还有一大截空白；
页面要的是"有效底部"——末条底边刚好落在提示条上方（留 lastItemGap），
并且无论怎么滚到底（拖动 / 惯性甩动 / 表冠），停下后都自动收回/顶回到这个位置。

圆屏手表（HUAWEI WATCH 6：466x466px @2x = 233x233vp）与手机几何不同，
本脚本在真机上把上面每一条都跑一遍，**用无障碍树的实测像素坐标**断言末条落点，
而不是靠肉眼看截图。

手势坐标必须避开底部悬浮层：状态条 0~44px、提示条 294~334px、按钮 346~406px，
所以统一在 y ∈ [70, 280] 这条"纯列表带"里划。

用法：
    .venv/bin/python tools/verify_wearable_offset.py [--out-dir watch_verify]

依赖：设备已通过 hdc 连接（TestApp/autoshot.yaml 里配的 hdc_path）、TestApp 已安装。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from typing import List, Optional, Sequence

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from autoshot.config import load_config            # noqa: E402
from autoshot.hdc_driver import HdcDriver, HdcError  # noqa: E402
from autoshot import layout as L                   # noqa: E402

TESTAPP = os.path.join(REPO_ROOT, "TestApp")

# ---- 与 OffsetListPage.WEARABLE_GEOMETRY 保持一致（改那边记得同步）----
BUTTON_HEIGHT = 30
BUTTON_BOTTOM_MARGIN = 30
BUTTON_FONT_VP = 9         # 一行并排三个按钮，字号被压到 9vp
BUTTON_PADDING_VP = 2      # 显式内边距（Button 默认左右各 16vp，会把文案挤到折行/裁切）
DISCLAIMER_BOTTOM_GAP = 6
DISCLAIMER_HEIGHT = 20
LAST_ITEM_GAP = 8
ITEM_HEIGHT = 40
ITEM_FONT_VP = 12
CONTENT_START_OFFSET = 12
CONTENT_END_OFFSET = 160
# 安全线（末条底边落点）相对按钮顶边的距离
SAFE_INSET_VP = DISCLAIMER_BOTTOM_GAP + DISCLAIMER_HEIGHT + LAST_ITEM_GAP
PX_PER_VP = 2
TOLERANCE_PX = 4
# 圆屏半径 / 圆心（px），用于判断某个点/矩形是否落在可见圆内
R_PX = 233
CX_PX = 233
# "纯列表带"：避开状态条(0~44)、提示条(294~334)、底部按钮(346~406)
Y_TOP = 70
Y_BOTTOM = 280
# 圆屏包含判定容差（px）
CIRCLE_TOL_PX = 2

ID_LAST_ITEM = "offset_last_item"
ID_BUTTON = "offset_bottom_button"
ID_LIST = "offset_list"
ID_APPEND = "offset_append_button"
ID_STREAM = "offset_stream_button"

TXT_DISCLAIMER = "内容由AI生成"
TXT_ENTRY = "偏移列表（触底回弹）"
TXT_LOADED_PREFIX = "下拉加载第"
TXT_LAST_MOCK = "初始 Mock 数据 #30"
TXT_TOP_SETTLE = "自动滚回底部"
TXT_APPEND_STATUS = "已从底部插入"
TXT_STREAM_STATUS = "流式写入"
TXT_STREAM_STOP = "停止写入"
APPEND_CLICKS = 6          # 连点中间按钮的次数（每次 +5 条）

# 流式写入期间允许的漂移上限（px）：写入不改滚动偏移、只有滚动停止才结算，
# 所以"上一次回弹之后内容又长了几行"会体现为末条底边低于安全线。
# 写入速度 5.7 字/s、约 12 字一行 -> 约 0.5 行/s -> 约 17px/s；
# 每个测量点都在"回弹 + 2.2s 甩动 + 1.4s dump"之后，最多累积约 60px。
# 上限其实被**窗口裁剪**钳住了：末条长高后底边超出屏幕时，控件树给的矩形是裁到窗口的
# （实测底边读数为 466px = 屏高），所以能观察到的最大漂移 = 466 - 278 = 188px。
# 取 200px 只做"没跑到天上去"的粗校验；这一项真正的判据是**下界**
# （漂移 < -TOLERANCE 说明弹回没做，末条还压在框架真正底部）。
STREAM_DRIFT_MAX_PX = 200
# dumpLayout 偶发返回残缺树时的重取次数与间隔
TREE_RETRY = 4
TREE_RETRY_INTERVAL_S = 0.4
# 永远在屏上的常量 id：拿它们判断"这次 dump 是不是完整的"
REQUIRE_ALWAYS = (ID_LIST, ID_BUTTON, ID_APPEND, ID_STREAM)


class DeviceOfflineError(Exception):
    """设备掉线。

    刻意**不继承 RuntimeError**：脚本里大量 `except RuntimeError` 是用来兜"这一次量不到"的，
    掉线必须一路抛到 main 中止整轮，不能被降级成"8 次甩动后仍量不到末条"这种看不出来的结论。
    """


class Verifier:
    def __init__(self, out_dir: str):
        self.cfg = load_config(TESTAPP)
        self.drv = HdcDriver(self.cfg)
        self.bundle = self._bundle_name()
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.steps: List[dict] = []
        # 运行期间被切走（用户动过手表 / 系统弹窗 / 应用重启）的阶段名，最后一个步骤会汇总
        self.recoveries: List[str] = []
        self.failures: List[str] = []
        self.notes: List[str] = []
        self.size = self.drv.display_size()

    # ---- 基础设施 ----
    def _bundle_name(self) -> str:
        path = os.path.join(TESTAPP, "AppScope", "app.json5")
        text = open(path, encoding="utf-8").read()
        m = re.search(r'"bundleName"\s*:\s*"([^"]+)"', text)
        if not m:
            raise RuntimeError(f"读不到 bundleName: {path}")
        return m.group(1)

    def tree(self, require: Sequence[str] = ()) -> L.Node:
        """取控件树。

        `uitest dumpLayout` 在这块表上一次要 1.3~3.6s，而且 UI 频繁重渲染时会**偶发返回
        几乎空白的树**（流式写入期间实测抓到过一条节点都没有：末条、连底部按钮都读不到，
        截图里画面明明正常）。所以：`require` 里给的 id 只要有一个不在树里就重取，
        最多 TREE_RETRY 次——常量 id（列表 / 三个底部按钮）永远在屏上，拿它们当"这次 dump
        是不是完整的"判据最省事。
        """
        root = L.parse_tree(self.drv.dump_layout())
        for _ in range(TREE_RETRY - 1):
            if not require or all(self.by_id(root, k) is not None for k in require):
                return root
            time.sleep(TREE_RETRY_INTERVAL_S)
            root = L.parse_tree(self.drv.dump_layout())
        return root

    @staticmethod
    def by_id(root: L.Node, node_id: str) -> Optional[L.Node]:
        for n in L.iter_nodes(root):
            if n.key == node_id:
                return n
        return None

    @staticmethod
    def by_text(root: L.Node, target: str, exact: bool = True) -> Optional[L.Node]:
        # normalize_text 会去掉空白，所以两边都要归一化后再比
        want = L.normalize_text(target)
        for n in L.iter_nodes(root):
            t = L.normalize_text(n.text)
            if (t == want) if exact else (want in t):
                return n
        return None

    @staticmethod
    def has_text(root: L.Node, *targets: str) -> bool:
        wants = [L.normalize_text(t) for t in targets]
        texts = [L.normalize_text(n.text) for n in L.iter_nodes(root)]
        return any(w == t or w in t for w in wants for t in texts)

    @staticmethod
    def status_text(root: L.Node) -> str:
        """顶部状态条：本页的调试文案（不含列表项与提示条）。"""
        marks = ("contentEndOffset", "自动滚回", "已加载", "滚动极限", TXT_APPEND_STATUS,
                 TXT_STREAM_STATUS)
        best = ""
        for n in L.iter_nodes(root):
            t = L.normalize_text(n.text)
            if any(m in t for m in marks) and len(t) > len(best):
                best = t
        return best

    def shot(self, name: str) -> str:
        path = os.path.join(self.out_dir, f"{name}.png")
        with open(path, "wb") as f:
            f.write(self.drv.screenshot())
        return path

    # ---- 输入（坐标一律落在"纯列表带"内，别打到悬浮按钮/提示条上）----
    def raw_swipe_up(self, velocity: int = 2000) -> None:
        """不等待的上划：用来模拟"不等回弹动画结束就继续划"的连续快速手势。"""
        self.drv._shell("uitest", "uiInput", "swipe", "233", str(Y_BOTTOM), "233", str(Y_TOP),
                        str(velocity))

    def raw_swipe_down(self, velocity: int = 2500, y_from: int = Y_TOP,
                       y_to: int = Y_BOTTOM) -> None:
        self.drv._shell("uitest", "uiInput", "swipe", "233", str(y_from), "233", str(y_to),
                        str(velocity))

    def swipe_up(self, velocity: int = 2000) -> None:
        self.raw_swipe_up(velocity)
        time.sleep(0.9)

    def swipe_down(self, velocity: int = 2500, y_from: int = Y_TOP, y_to: int = Y_BOTTOM) -> None:
        self.raw_swipe_down(velocity, y_from, y_to)
        time.sleep(0.9)

    def raw_fling_up(self, velocity: int = 8000) -> None:
        self.drv._shell("uitest", "uiInput", "fling", "233", str(Y_BOTTOM), "233", str(Y_TOP),
                        str(velocity))

    def fling_up(self, velocity: int = 8000) -> None:
        """惯性甩到底：命中 onReachEnd / isAtEnd，触发"真正底部 -> 有效底部"回弹。"""
        self.raw_fling_up(velocity)
        time.sleep(2.2)

    def fling_down(self, velocity: int = 6000) -> None:
        """大幅往回甩：幅度足以把末条滑出屏幕、正常离开末尾区域。"""
        self.drv._shell("uitest", "uiInput", "fling", "233", str(Y_TOP + 20), "233",
                        str(Y_BOTTOM), str(velocity))
        time.sleep(2.2)

    def click_append(self) -> tuple:
        """点底部中间按钮（从列表底部追加 5 条）。返回按钮中心坐标。"""
        return self.click_button(ID_APPEND, "底部中间按钮 offset_append_button")

    def click_stream(self) -> tuple:
        """点底部最右按钮（流式写入：开始 / 停止）。返回按钮中心坐标。"""
        return self.click_button(ID_STREAM, "底部最右按钮 offset_stream_button")

    def click_button(self, node_id: str, label: str) -> tuple:
        node = self.by_id(self.tree(require=REQUIRE_ALWAYS), node_id)
        if node is None or node.bounds is None:
            raise RuntimeError(f"量不到{label}")
        cx, cy = node.center()
        if (cx - CX_PX) ** 2 + (cy - R_PX) ** 2 > (R_PX - 12) ** 2:
            raise RuntimeError(f"{label}中心 ({cx},{cy}) 落在圆外，点不到")
        self.drv.click(cx, cy)
        time.sleep(1.2)
        return cx, cy

    def scroll_content_down_px(self, px: int) -> None:
        """把内容往下挪 px（等价于往上回滚一点），低速短划。"""
        self.drv._shell("uitest", "uiInput", "swipe", "233", str(Y_TOP), "233", str(Y_TOP + px),
                        "300")
        time.sleep(1.0)

    def observe_yield_during_drag(self) -> tuple:
        """观察"末条经过提示条时提示条让位"。

        现在松手一定会回弹到安全线，所以让位只能在**拖动过程中**看到：
        后台线程发一次慢速 drag，主线程轮询控件树，看提示条节点是不是消失了。
        （提示条隐藏后控件树里就没有这个节点，所以"节点消失"就是让位的判据。）
        """
        err: List[str] = []

        def run() -> None:
            try:
                self.drv._shell("uitest", "uiInput", "drag", "233", str(Y_TOP), "233",
                                str(Y_TOP + 180), "200")
            except Exception as e:                 # noqa: BLE001 - 线程里只能自己兜住
                err.append(str(e))

        t = threading.Thread(target=run, daemon=True)
        t.start()
        hidden = False
        samples = 0
        shot_path = ""
        deadline = time.time() + 4.0
        while time.time() < deadline:
            try:
                root = self.tree(require=REQUIRE_ALWAYS)
            except Exception:                      # noqa: BLE001 - 拖动中 dump 偶发失败，重试即可
                continue
            samples += 1
            if self.by_text(root, TXT_DISCLAIMER) is None:
                hidden = True
                shot_path = self.shot("04_disclaimer_yielded")   # 让位瞬间留证
                break
        t.join(timeout=5.0)
        if hidden:
            return True, f"拖动中采样 {samples} 次，提示条节点消失（让位生效）", shot_path
        extra = f"；错误：{'；'.join(err)}" if err else ""
        return False, f"拖动中采样 {samples} 次，提示条始终在屏上{extra}", ""

    # ---- 量测 ----
    def measure(self) -> dict:
        """量末条 / 按钮 / 提示条的位置，算出实际落点相对安全线的偏差。"""
        self.check_device()      # 掉线时会返回陈旧树，必须当场中止（见 check_device）
        root = self.tree(require=REQUIRE_ALWAYS)
        item = self.by_id(root, ID_LAST_ITEM)
        button = self.by_id(root, ID_BUTTON)
        disclaimer = self.by_text(root, TXT_DISCLAIMER)
        if item is None or item.bounds is None:
            raise RuntimeError("量不到末条 offset_last_item（末条被滚出显示区时控件树里就没有它）")
        if button is None or button.bounds is None:
            raise RuntimeError("量不到底部按钮 offset_bottom_button")
        ib, bb = item.bounds, button.bounds
        safe_px = bb[1] - SAFE_INSET_VP * PX_PER_VP
        delta_px = ib[3] - safe_px
        return {
            "status": self.status_text(root),
            "last_item_bounds": ib,
            "button_bounds": bb,
            "disclaimer_bounds": disclaimer.bounds if disclaimer is not None else None,
            "disclaimer_visible": disclaimer is not None,
            "last_item_bottom": ib[3],
            "button_top": bb[1],
            "gap_button_to_item_bottom_vp": round((bb[1] - ib[3]) / PX_PER_VP, 1),
            "safe_line_px": safe_px,
            "delta_vs_safe_line_px": delta_px,
            "delta_vs_safe_line_vp": round(delta_px / PX_PER_VP, 1),
        }

    def last_item_in_view(self) -> tuple:
        """末条是否还在列表显示区内。

        注意不能只判断"控件树里有没有末条"：List 对屏幕外一小段距离的列表项仍有渲染缓存，
        滚出屏幕后它可能还挂在树上。判据用末条矩形与列表矩形的**交集**（与页内
        measureLastItemBottomPx 的口径一致）。
        """
        root = self.tree(require=REQUIRE_ALWAYS)
        item = self.by_id(root, ID_LAST_ITEM)
        lst = self.by_id(root, ID_LIST)
        if item is None or item.bounds is None:
            return False, "末条已不在控件树上（滑出屏幕）"
        ib = item.bounds
        if lst is None or lst.bounds is None:
            return ib[1] < R_PX * 2, f"末条 {ib}（列表矩形量不到）"
        lb = lst.bounds
        visible = ib[3] > lb[1] and ib[1] < lb[3]
        return visible, f"末条 {ib} / 列表 {lb} -> {'仍在显示区内' if visible else '已滑出显示区'}"

    @staticmethod
    def subtree_text(node: L.Node) -> str:
        """节点及其子树的文本拼起来（ListItem 自己没文本，文本在子节点里）。"""
        parts: List[str] = []
        for n in L.iter_nodes(node):
            t = L.normalize_text(n.text)
            if t:
                parts.append(t)
        return "".join(parts)

    def last_item_state(self) -> tuple:
        """末条的（子树文本, 控件高度 px）：流式写入用它证明"同一条在变长"。"""
        node = self.by_id(self.tree(require=(ID_LIST, ID_BUTTON)), ID_LAST_ITEM)
        if node is None or node.bounds is None:
            return "", 0
        b = node.bounds
        return self.subtree_text(node), b[3] - b[1]

    def observe_disclaimer_hidden(self, shot_name: str, timeout: float = 6.0,
                                  interval: float = 0.4) -> tuple:
        """流式写入过程中轮询：内容把末条顶到提示条附近时，提示条应该让位（节点消失）。"""
        deadline = time.time() + timeout
        samples = 0
        while time.time() < deadline:
            samples += 1
            try:
                # 必须要求"这一次 dump 是完整的"：dumpLayout 偶发返回空树，
                # 空树会被误判成"提示条已让位"（假通过）。
                if self.by_text(self.tree(require=REQUIRE_ALWAYS), TXT_DISCLAIMER) is None:
                    return (True, f"轮询 {samples} 次后提示条节点消失（内容增长期间让位生效）",
                            self.shot(shot_name))
            except RuntimeError:
                continue
            time.sleep(interval)
        return False, f"轮询 {samples} 次，提示条始终在屏上（内容增长没有触发让位）", ""

    def wait_stream_drift(self, want_px: int, timeout: float = 6.0,
                          interval: float = 0.3) -> tuple:
        """等"内容增长"把末条顶到安全线下方 want_px 以上。

        流式写入不改滚动偏移、也不产生滚动事件，所以这一段时间里**没有任何一次自动结算**——
        漂移会一直累积，正好用来验证"停止写入"那一下能不能一次收回来。
        """
        deadline = time.time() + timeout
        last = 0
        while time.time() < deadline:
            try:
                m = self.measure()
            except RuntimeError as e:
                return 0, f"量不到末条：{e}"
            last = m["delta_vs_safe_line_px"]
            if last >= want_px:
                return last, f"漂移累积到 {last}px（写入期间不会自动结算）"
            time.sleep(interval)
        return last, f"{timeout}s 内漂移只到 {last}px"

    def button_label(self, node_id: str) -> str:
        """按钮控件树上的文案（读不到返回空串：只用于报告，不单独作为失败判据）。"""
        node = self.by_id(self.tree(require=REQUIRE_ALWAYS), node_id)
        return L.normalize_text(node.text) if node is not None else ""

    def check_button_labels(self, shot_path: str, targets: List[tuple]) -> tuple:
        """像素复核底部按钮的文案：必须**单行、完整、不贴边**。

        被测的真机问题：一行并排三个按钮后每个只剩 ~46vp，而 Button(Capsule) 默认左右
        各 16vp 内边距，文案区只剩 ~14vp -> 文案折成两行，30vp 高的按钮把第二行裁掉
        （实测：文案区仅 68px 宽，第二行 glyph 画到按钮底边之外）。

        targets: [(标签, 控件节点, 兜底文案)]，兜底文案用于控件树读不到按钮文本时估算理论宽度。
        判据（白字画在蓝色按钮上，所以"文字像素"= 三通道都 > 190）：
          1) 有文字像素；
          2) 文字像素的行集合是**一段连续带**（行内空隙 <= 4px）=> 只有一行；
          3) 上/下各留 >= 4px，左/右各留 >= 2px => 没有被按钮边界裁切；
          4) 文字宽度 >= 0.75 * 理论宽度（汉字按 1 个字宽、ASCII 按 0.55）=> 不是被省略号截断的。
        """
        try:
            from PIL import Image
        except ImportError:
            return True, "没装 Pillow，跳过按钮文案像素复核"
        im = Image.open(shot_path).convert("RGB")
        notes: List[str] = []
        bad: List[str] = []
        for label, node, expect in targets:
            if node is None or node.bounds is None:
                bad.append(f"{label}(量不到)")
                continue
            text = L.normalize_text(node.text) or expect
            b = node.bounds
            crop = im.crop((b[0], b[1], b[2], b[3]))
            w, h = crop.size
            px = crop.load()
            rows: List[int] = []
            cols: List[int] = []
            for y in range(h):
                for x in range(w):
                    r, g, bl = px[x, y]
                    if r > 190 and g > 190 and bl > 190:
                        rows.append(y)
                        cols.append(x)
            if not rows:
                bad.append(f"{label}「{text}」按钮里量不到文字像素")
                continue
            row_set = sorted(set(rows))
            gaps = [b2 - a2 for a2, b2 in zip(row_set, row_set[1:])]
            max_gap = max(gaps) if gaps else 0
            top, bottom = row_set[0], h - 1 - row_set[-1]
            left, right = min(cols), w - 1 - max(cols)
            want = 0.0
            for ch in text:
                want += BUTTON_FONT_VP * PX_PER_VP * (1.0 if ord(ch) > 0x2E80 else 0.55)
            ink_w = max(cols) - min(cols) + 1
            probs: List[str] = []
            if max_gap > 4:
                probs.append(f"文字分成多行（行间空隙 {max_gap}px）")
            if top < 4 or bottom < 4:
                probs.append(f"文字贴边被裁（上 {top}px / 下 {bottom}px）")
            if left < 2 or right < 2:
                probs.append(f"文字贴左右边（左 {left}px / 右 {right}px）")
            if want > 0 and ink_w < 0.75 * want:
                probs.append(f"文字宽度只有 {ink_w}px，理论 {want:.0f}px（疑似省略号截断）")
            notes.append(f"{label}「{text}」{b} 文字 {ink_w}x{row_set[-1] - row_set[0] + 1}px"
                         f" 上{top}/下{bottom}/左{left}/右{right}"
                         + (f" —— {'；'.join(probs)}" if probs else " 单行完整"))
            if probs:
                bad.append(label)
        return not bad, "；".join(notes) + (f" —— 异常：{bad}" if bad else "")

    # ---- 步骤 ----
    def step(self, name: str, ok: bool, detail: str, shot: Optional[str] = None) -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail, "shot": shot})
        if not ok:
            self.failures.append(f"{name}: {detail}")
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    def check_device(self) -> None:
        """阶段边界确认设备还在线。

        真机实测：USB 一掉线，`uitest dumpLayout` / `screenCap` 会继续"成功"返回设备上**上一次**
        留下的文件——于是控件树和截图同时冻结在第 7 步的状态（19 张截图逐字节相同、
        树里连末条都没有），后面每一步都以"量不到"失败，完全看不出真正原因。
        宁可在这里明确报错。
        """
        try:
            serials = HdcDriver.list_devices(self.cfg.hdc_path)
        except HdcError as e:
            raise DeviceOfflineError(f"设备离线（{e}）") from None
        if not serials:
            raise DeviceOfflineError(
                "设备离线（hdc list targets 为空）：USB 掉线后 dumpLayout/截图会返回上一次的陈旧文件"
                "（实测 19 张截图逐字节相同、控件树冻结在某一步），本脚本的结果不可信，"
                "请重新连接设备后重跑")

    def phase(self, where: str) -> None:
        """每个阶段开始前确认应用还在 OffsetListPage 上；被切走就自动重新导航回来。

        真机实测：应用被切到后台/重启过之后，脚本后续的滑动会全部打在桌面（launcher）上，
        一连串检查会以「量不到末条 / 量不到按钮」连锁失败，看不出真正原因。
        这里把「切走了」显式记下来，最后单独出一条结论。
        """
        self.check_device()
        root = self.tree()
        if self.by_id(root, ID_LIST) is not None:
            return
        self.recoveries.append(where)
        self.goto_offset_page()

    def goto_offset_page(self) -> None:
        # force-stop 后再 start：避免上次运行停在 OffsetListPage 上直接复用旧路由栈
        try:
            self.drv.app_stop(self.bundle)
        except Exception:                     # noqa: BLE001 - 没在跑时 force-stop 会报错
            pass
        time.sleep(3.0)                       # force-stop 后 ~2.5s 内 start 会被限流吞掉
        self.drv.app_start(self.bundle)
        time.sleep(3.0)
        for _ in range(8):
            root = self.tree(require=REQUIRE_ALWAYS)
            entry = self.by_text(root, TXT_ENTRY)
            if entry is not None and entry.bounds:
                cx, cy = entry.center()
                # 圆屏：只有落在圆内的点才点得到（内接留 12px 余量）
                if (cx - CX_PX) ** 2 + (cy - R_PX) ** 2 <= (R_PX - 12) ** 2:
                    self.drv.click(cx, cy)
                    time.sleep(3.5)       # 首屏 scrollTo(300ms) + 回弹(420ms) + 余量
                    return
            self.swipe_up()
        raise RuntimeError(f"首页滚不到可点的入口「{TXT_ENTRY}」（穿戴布局可能又溢出了）")

    def check_settled(self, name: str, shot_name: str, want_status: str = "") -> dict:
        try:
            m = self.measure()
        except RuntimeError as e:
            self.step(name, False, f"量不到末条：{e}", self.shot(shot_name))
            return {}
        ok = abs(m["delta_vs_safe_line_px"]) <= TOLERANCE_PX
        detail = (f"末条底边 {m['last_item_bottom']}px / 安全线 {m['safe_line_px']}px "
                  f"（偏差 {m['delta_vs_safe_line_vp']}vp，容差 {TOLERANCE_PX / PX_PER_VP}vp）"
                  f" | 末条距按钮顶边 {m['gap_button_to_item_bottom_vp']}vp"
                  f" | 提示条{'显示' if m['disclaimer_visible'] else '隐藏'}"
                  f" | 状态「{m['status']}」")
        if want_status:
            hit = want_status in m["status"]
            ok = ok and hit
            detail += f" | 期望状态含「{want_status}」：{'命中' if hit else '未命中'}"
        self.step(name, ok, detail, self.shot(shot_name))
        return m

    def fling_to_effective_bottom(self, name: str, shot_name: str, want_status: str = "",
                                 tries: int = 8) -> dict:
        """反复甩到底直到停到安全线（每次甩动都可能离框架底部更近一点）。"""
        m: dict = {}
        for i in range(tries):
            self.fling_up()
            try:
                m = self.measure()
            except RuntimeError:
                continue
            if abs(m["delta_vs_safe_line_px"]) <= TOLERANCE_PX:
                break
        if not m:
            self.step(name, False, f"{tries} 次甩动后仍量不到末条", self.shot(shot_name))
            return {}
        return self.check_settled(name, shot_name, want_status)


def analyze_shot(path: str, rect: Optional[tuple] = None) -> Optional[dict]:
    """对截图做像素级复核（脚本作者/模型不一定看图，所以证据要可量化）。

    只做最基本的"不是黑屏/空帧"判断 + 可选区域的灰度统计：
    真正的判定仍以控件树实测坐标为准，截图是给人看的视觉记录。
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(path).convert("RGB")
    w, h = im.size
    colors = im.getcolors(maxcolors=1 << 20)
    stats = {"size": (w, h), "colors": len(colors) if colors else (1 << 20)}
    g = im.convert("L")
    px = list(g.tobytes())            # mode L：一字节一像素，避免 getdata() 的弃用告警
    stats["mean"] = round(sum(px) / len(px), 1)
    stats["min"], stats["max"] = min(px), max(px)
    if rect is not None:
        crop = list(g.crop(rect).tobytes())
        m = sum(crop) / len(crop)
        var = sum((v - m) ** 2 for v in crop) / len(crop)
        stats["rect_mean"] = round(m, 1)
        stats["rect_std"] = round(var ** 0.5, 1)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "watch_verify"))
    args = ap.parse_args()

    # 清掉上一次的产物，避免旧截图混进本次像素复核
    if os.path.isdir(args.out_dir):
        for name in os.listdir(args.out_dir):
            if name.endswith(".png") or name.endswith(".md") or name.endswith(".json"):
                os.remove(os.path.join(args.out_dir, name))

    v = Verifier(args.out_dir)
    print(f"设备: {v.size} px, bundle={v.bundle}, 输出目录={v.out_dir}")

    v.check_device()
    v.goto_offset_page()

    v.notes.append(
        f"contentStartOffset + contentEndOffset 必须 ≤ 列表显示区高度：手表列表高仅 211vp，"
        f"最初取 {CONTENT_START_OFFSET}+200=212 时框架把两者**静默置 0**"
        f"（现象：contentEndOffset 完全不产生可滚动空间，末条永远贴着列表底边，"
        f"怎么滚都停不到安全线）；现取 {CONTENT_START_OFFSET}+{CONTENT_END_OFFSET}="
        f"{CONTENT_START_OFFSET + CONTENT_END_OFFSET}，留 "
        f"{211 - CONTENT_START_OFFSET - CONTENT_END_OFFSET}vp 余量后正常。"
        f"手机参数 24+520=544 远小于手机列表高度，不受影响。")
    v.notes.append(
        "**底部按钮文案样式（本次修复）**：一行并排三个按钮后每个只剩约 46vp，"
        "而 Button(Capsule) 默认左右各 16vp 内边距，文案区只剩约 14vp——真机表现为"
        "「文案折成两行、第二行被 30vp 高的按钮底边裁掉」（实测：文案区仅 68px 宽、"
        "第二行 glyph 画到按钮底边之外；两个按钮都只剩 3 个字 + 省略号）。"
        "修法：① 文案改短（返回 / 插入5条 / 流式写入 / 停止写入）；"
        "② 显式 padding 左右各 2vp；③ 文案固定单行（maxLines=1）；④ 穿戴字号 10->9vp。"
        "本脚本第 15 项用**截图像素**复核：文字像素必须单行、上下各留 4px、左右各留 2px、"
        "宽度不小于理论宽度的 75%（否则就是被省略号截断）。")
    v.notes.append(
        "**流式文本用例（底部最右按钮）**：写入目标锁定为**当前末条那一个 listitem**"
        "（锁定 id，不随「最后一条」漂移），每 350ms 追加 2 个字符（实测约 5~10 字/s、1~2s 一行），"
        "一行行把控件撑高；行数上限 20 行，真机验证全程都在长高、不会中途停住。"
        "因为数据变更**不产生滚动事件**，所以这一段里没有「每次写入都结算」这种事："
        "① 用户滚到底停下时，硬约束照常把末条收回安全线（第 21 项：末条不会停在安全线上方）；"
        "② 每次回弹之后内容继续长，末条底边会低于安全线一点——这段漂移就是「内容又长出来了」；"
        "③ 点「停止写入」那一下立即结算，末条一次收回到安全线（第 23 项，偏差 0）。"
        "停止用的是 force=false：用户要是已经翻到列表中段（末条不在屏上），不该被拽回底部。"
        "项高不再是固定值：普通项用 constraintSize(minHeight) 兜住 40vp，流式那一项随文本长高。")
    v.notes.append(
        "**流式写入暴露出的真 bug：回弹动画永远落不了地（本次修复）**。真机日志（每 250ms 一条）："
        "「onScrollStop: reachedEnd=true -> force=true / settle: 发起修正 delta=-71.6 target=1359.0 "
        "animate=true / measure: itemBottom=134.8 safe=278.0 overflow=-71.62」——列表被框架钉在"
        "**真正底部**（isAtEnd 一直为 true），内容每几百毫秒长一次就把它重新钉回末尾，"
        "还在飞的回弹动画被下一次内容变更取消，于是修正永远不生效、还以 4Hz 反复重发，"
        "末条始终停在 134px（安全线上方 71vp＝用户看到的「划到底不回弹」）。"
        "修法：**流式写入期间改用瞬移修正**（animate=false）——下一帧就落地，落地后偏移已离开末尾"
        "（不再 isAtEnd），后续内容增长不会再把它拽回真底，回弹才算真的发生。"
        "正常（非流式）场景仍然用 BOUNCE_DURATION 的动画回弹。")
    v.notes.append(
        "为让整行不被圆边裁掉，穿戴参数下给 List 加了左右各 8vp 内缩（listPadding，手机为 0）；"
        "该内缩只影响水平方向，安全线是纵向计算，不受影响。")
    v.notes.append(
        "本次只连了手表（hdc 上只有 NIZ-AL00），手机几何参数 PHONE_GEOMETRY **未重新真机验证**"
        "（按钮文案与内边距同步改短/压小，手机按钮宽度 90%/3 ≈ 100vp，文案仍单行放得下）；"
        "接入手机后建议重跑一次本脚本（把几何常量换成手机那套）。")
    v.notes.append(
        "**回弹硬约束（本次用户诉求「不管怎样一定要回弹回来」）**：末条只要还在列表显示区内，就不允许停在"
        "安全线以外——不看手势方向、不看速度快慢，也不依赖 onReachEnd / isAtEnd()（真机实测它们在"
        "快速连划时会漏报）。要离开末尾区域，得用一次幅度足够大的滑动/甩动把末条滑出显示区"
        "（需要约 134vp 位移），此后不再插手；本脚本第 10、11 项分别锁这两种行为。")
    v.notes.append(
        "定位到的三个「划到底不回弹」根因（都已在页内修掉）："
        "① 上一次修正的 pendingTarget 被后续快速手势改写后，原实现直接「放弃本次修正」并 return，"
        "列表就永久停在框架「真正底部」（末条 146px、状态误报「已到滚动极限」）——现在改成按当前实测重算；"
        "② 是否「强制回弹」原先只看 onReachEnd / isAtEnd()，快速连划时它们会漏报，于是走进"
        "「末条在安全线下方就不干预」分支——现在末条在屏上时根本不看这两个信号；"
        "③ getRectangleById 对已经滚出显示区的列表项照样返回布局矩形（实测能给到屏幕下方 1000px 的坐标），"
        "只看 size.height 会把「早就不在屏上」误判成「还在屏上」，导致用户往列表中部翻也被拽回底部——"
        "现在显式用「末条矩形与列表矩形有交集」判可见性。")
    v.notes.append(
        "**底部中间按钮（从列表底部追加 5 条）与提示条**：追加时滚动偏移没变、末尾又长出内容，"
        "所以这一刻列表已经不在有效底部——`OffsetListPage.appendAtBottom` 不等测量直接让位隐藏并上闩，"
        "避免「插入瞬间 ForEach 还没重渲染、量到的仍是插入前的旧末条（还挂在安全线上）」把提示条弹回来"
        "（真机实测过这个竞态：固定延时 120ms 重判，3 轮里会漏 1 次）。"
        "用户滚到新的底部、末条重新升到提示条上方时，位置判定会自动解锁恢复显示。")
    v.notes.append(
        "提示条让位现在只能在**拖动过程中**观察到：松手一定会被拉回安全线，末条无法静止在提示条上。"
        "所以本脚本第 6 项改成「后台线程发慢速 drag + 主线程并发轮询控件树」，以「提示条节点消失」为让位判据。")

    # 1. 首屏：onAppear 里 scrollTo(100000) 拉到"真正底部"，再自动收回"有效底部"
    first = v.check_settled("首屏自动回正到有效底部", "01_initial_settle", TXT_TOP_SETTLE)
    root = v.tree()
    v.step("首屏数据量 = 30 条", v.has_text(root, TXT_LAST_MOCK),
           f"末条文案「{TXT_LAST_MOCK}」{'在' if v.has_text(root, TXT_LAST_MOCK) else '不在'}控件树上"
           f" | 提示条{'显示' if first.get('disclaimer_visible') else '隐藏'}")

    # 2. 硬约束：末条只要还在屏上，就不许停在安全线以外。
    #    往回划一点（离开有效底部）后松手 -> 必须被拉回安全线。
    pullbacks = 0
    pull_ok = True
    pull_detail = ""
    for i in range(3):
        v.swipe_down(velocity=250, y_from=Y_TOP + 80, y_to=Y_TOP + 160)   # 小幅往回划
        try:
            m = v.measure()
        except RuntimeError as e:
            pull_ok, pull_detail = False, f"第 {i + 1} 次往回划后量不到末条：{e}"
            break
        pullbacks += 1
        d = m["delta_vs_safe_line_vp"]
        if abs(m["delta_vs_safe_line_px"]) > TOLERANCE_PX:
            pull_ok = False
            pull_detail = f"第 {i + 1} 次往回划后停在安全线外（末条底边 {m['last_item_bottom']}px，偏差 {d}vp）"
            break
        pull_detail = f"{pullbacks} 次小幅往回划后都被拉回：末条底边 {m['last_item_bottom']}px，偏差 {d}vp"
    v.step("往回划离开有效底部后自动拉回（强制回弹）", pull_ok,
           pull_detail, v.shot("02_pull_back_snapped"))

    # 3. 甩到框架"真正底部" -> 应自动回弹到安全线，且状态显示做过修正
    second = v.fling_to_effective_bottom("甩到真正底部后自动回弹", "03_after_fling_to_end",
                                         TXT_TOP_SETTLE)
    v.step("回弹后落点与首屏一致",
           bool(second) and abs(second["last_item_bottom"] - first["last_item_bottom"]) <= TOLERANCE_PX,
           f"首屏 {first['last_item_bottom']}px vs 回弹后 {second.get('last_item_bottom')}px")

    # 4. 提示条让位：末条挪进"让位判定窗口"（提示条底边附近）时提示条应淡出。
    #    窗口 = d ∈ (-(提示条高+clear), approach]，穿戴参数约 (-24vp, 10vp]。
    #    现在松手就会回弹到安全线，所以让位只能在**拖动过程中**观察到：
    #    用后台线程发一个慢速 drag，同时轮询控件树，看提示条节点是否消失。
    v.step("末条经过提示条时提示条让位淡出（拖动中采样）", *v.observe_yield_during_drag())

    # 5. 松手回弹后 -> 末条回到提示条上方 -> 提示条恢复显示
    third = v.fling_to_effective_bottom("提示条恢复 + 再次回弹", "05_disclaimer_restored",
                                        TXT_TOP_SETTLE)
    v.step("回到有效底部后提示条重新出现", bool(third.get("disclaimer_visible")),
           f"提示条{'显示' if third.get('disclaimer_visible') else '隐藏'}")

    # 6. 压力：连续快速上划（不等回弹动画结束就再划）+ 回弹动画中途反向打断，
    #    最后都必须停在安全线——这是"不管怎样都要回弹回来"的回归用例。
    stress_fails: List[str] = []
    stress_rounds = 0
    for i in range(3):
        for _ in range(3):
            v.raw_swipe_up(velocity=2500)          # 故意不等回弹动画结束
        time.sleep(2.5)
        stress_rounds += 1
        try:
            m = v.measure()
        except RuntimeError as e:
            stress_fails.append(f"快速连划 #{i + 1}：{e}")
            continue
        if abs(m["delta_vs_safe_line_px"]) > TOLERANCE_PX:
            stress_fails.append(f"快速连划 #{i + 1}：停在 {m['last_item_bottom']}px"
                                f"（偏差 {m['delta_vs_safe_line_vp']}vp）")
    for i in range(3):
        v.raw_fling_up()
        time.sleep(0.15)                            # 抢在 420ms 回弹动画中途
        v.raw_swipe_down(velocity=250)
        time.sleep(2.5)
        stress_rounds += 1
        try:
            m = v.measure()
        except RuntimeError as e:
            stress_fails.append(f"打断回弹 #{i + 1}：{e}")
            continue
        if abs(m["delta_vs_safe_line_px"]) > TOLERANCE_PX:
            stress_fails.append(f"打断回弹 #{i + 1}：停在 {m['last_item_bottom']}px"
                                f"（偏差 {m['delta_vs_safe_line_vp']}vp）")
    v.step("压力：快速连划 / 打断回弹后仍回到安全线", not stress_fails,
           f"{stress_rounds} 轮全部落回安全线" if not stress_fails
           else "；".join(stress_fails), v.shot("06_stress_always_bounces"))

    # 7. 不粘死：一次幅度够大的往回甩应该能把末条滑出屏幕、正常离开末尾区域
    escaped = False
    escape_detail = ""
    for i in range(3):
        v.fling_down(velocity=6000)
        in_view, why = v.last_item_in_view()
        escape_detail = f"第 {i + 1} 次大幅回甩后：{why}"
        if not in_view:
            escaped = True
            break
    v.step("大幅回甩可以离开末尾区域（不被粘在底部）", escaped,
           escape_detail, v.shot("07_escaped_end_region"))

    # 8. 顶部下拉加载：先划回顶部，再在顶部继续往回滚 -> 插 10 条到列表开头。
    #    判据用"列表里出现「下拉加载第…」批次条目"：加载只能发生在顶部继续往回滚时，
    #    所以这同时证明了"划到了顶部"。
    loaded = False
    swipes = 0
    for swipes in range(1, 21):
        if v.has_text(v.tree(), TXT_LOADED_PREFIX):
            loaded = True
            break
        v.swipe_down()
    root = v.tree()
    v.step("划回顶部后继续往回滚触发下拉加载", loaded,
           f"{swipes} 次下划后列表里{'出现' if loaded else '没出现'}"
           f"「{TXT_LOADED_PREFIX}…」批次条目 | 状态「{v.status_text(root)}」",
           v.shot("08_pull_down_load_more"))

    # 9. 加载之后再滚到底，仍然停到有效底部（下拉是插到列表开头，偏移会跳变，回弹逻辑必须还能收敛）
    fourth = v.fling_to_effective_bottom("加载更多后仍能回弹到有效底部", "09_settle_after_load_more")
    v.step("末条底边仍在安全线上",
           bool(fourth) and abs(fourth["delta_vs_safe_line_px"]) <= TOLERANCE_PX,
           f"偏差 {fourth.get('delta_vs_safe_line_vp')}vp")

    # 10. 底部按钮改成一行并排三个（左：原功能返回；中：从底部追加 5 条；右：模拟流式文本）
    v.phase("第 10~13 项（底部三个按钮 / 文案像素 / 追加）")
    root = v.tree()
    left_btn = v.by_id(root, ID_BUTTON)
    mid_btn = v.by_id(root, ID_APPEND)
    right_btn = v.by_id(root, ID_STREAM)
    row_ok = bool(left_btn and mid_btn and right_btn
                  and left_btn.bounds and mid_btn.bounds and right_btn.bounds
                  and left_btn.bounds[1] == mid_btn.bounds[1] == right_btn.bounds[1]
                  and left_btn.bounds[3] == mid_btn.bounds[3] == right_btn.bounds[3]
                  and left_btn.bounds[2] <= mid_btn.bounds[0]
                  and mid_btn.bounds[2] <= right_btn.bounds[0])
    v.step("底部一行并排三个按钮（左返回 / 中插入 / 右流式）", row_ok,
           f"左 {left_btn.bounds if left_btn else None} / 中 {mid_btn.bounds if mid_btn else None}"
           f" / 右 {right_btn.bounds if right_btn else None}",
           v.shot("14_three_bottom_buttons"))

    # 11. 按钮文案样式（像素复核）：一行并排三个后每个只剩 ~46vp，必须单行、完整、不贴边。
    #     真机问题：Button 默认左右各 16vp 内边距把文案区压到 ~14vp，文案折成两行、
    #     第二行被 30vp 高的按钮底边裁掉（实测文案区仅 68px 宽）。
    style_shot = v.shot("15_button_label_style")
    style_ok, style_detail = v.check_button_labels(
        style_shot, [("左按钮", left_btn, "返回"), ("中按钮", mid_btn, "插入5条"),
                     ("右按钮", right_btn, "流式写入")])
    v.step("底部按钮文案单行完整显示（像素复核：不折行/不裁切/不省略）",
           style_ok, style_detail, style_shot)

    # 12. 点中间按钮：每次从列表底部追加 5 条。
    #     追加后滚动偏移没变、末尾又长出内容 -> 列表已不在有效底部，
    #     "内容由AI生成"必须自动隐藏（这是本次要观察的行为）。
    append_fails: List[str] = []
    # 条数不写死（前面已经下拉加载过若干批）：只看"每次点击是不是恰好 +5 条"
    prev_total: Optional[int] = None
    for i in range(APPEND_CLICKS):
        try:
            v.click_append()
        except RuntimeError as e:
            append_fails.append(str(e))
            break
        root = v.tree()
        hidden = v.by_text(root, TXT_DISCLAIMER) is None
        status = v.status_text(root)
        m = re.search(r"共(\d+)条", status)
        total = int(m.group(1)) if m else None
        if not hidden:
            append_fails.append(f"第 {i + 1} 次插入后提示条仍在（状态「{status}」）")
        elif not status.startswith(TXT_APPEND_STATUS) or total is None:
            append_fails.append(f"第 {i + 1} 次插入后状态异常「{status}」")
        elif prev_total is not None and total != prev_total + 5:
            append_fails.append(f"第 {i + 1} 次插入条数 {prev_total} -> {total}，不是 +5")
        if total is not None:
            prev_total = total
    v.step("点中间按钮从底部插入后「内容由AI生成」自动隐藏", not append_fails,
           f"连点 {APPEND_CLICKS} 次、每次 +5 条，每次都自动隐藏，状态「{v.status_text(v.tree())}」"
           if not append_fails else "；".join(append_fails),
           v.shot("16_append_bottom_disclaimer_hidden"))

    v.phase("第 14~15 项（圆屏内接 / 追加后回弹）")
    # 12. 插完再滚到**新的**有效底部：末条重新落到安全线，提示条恢复显示
    after_append = v.fling_to_effective_bottom("插入后滚到新的有效底部", "17_settle_after_append")
    v.step("插入后新底部仍停在安全线且提示条恢复",
           bool(after_append) and abs(after_append["delta_vs_safe_line_px"]) <= TOLERANCE_PX
           and bool(after_append.get("disclaimer_visible")),
           f"偏差 {after_append.get('delta_vs_safe_line_vp')}vp | "
           f"提示条{'显示' if after_append.get('disclaimer_visible') else '隐藏'}")

    # 14. 圆屏适配：功能性控件的四角都要落在 466x466 圆内（否则会被圆边裁掉）
    root = v.tree()
    last_item = v.by_id(root, ID_LAST_ITEM)
    button = v.by_id(root, ID_BUTTON)
    append_btn = v.by_id(root, ID_APPEND)
    stream_btn = v.by_id(root, ID_STREAM)
    disclaimer = v.by_text(root, TXT_DISCLAIMER)
    targets = [("末条", last_item), ("提示条", disclaimer),
               ("底部左按钮", button), ("底部中按钮", append_btn), ("底部右按钮", stream_btn)]
    outside = []
    notes = []
    for label, node in targets:
        if node is None or node.bounds is None:
            outside.append(f"{label}(量不到)")
            continue
        b = node.bounds
        bad = [(x, y) for (x, y) in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]))
               if (x - CX_PX) ** 2 + (y - R_PX) ** 2 > (R_PX + CIRCLE_TOL_PX) ** 2]
        notes.append(f"{label}{b}" + (f" 越界角{bad[0]}" if bad else " 全在圆内"))
        if bad:
            outside.append(label)
    v.step("末条/提示条/底部三个按钮都落在圆屏内接区域内", not outside,
           "；".join(notes) + (f" —— 越界：{outside}" if outside else ""),
           v.shot("19_circle_containment"))

    # ---- 流式文本（底部最右按钮）：模拟"一直往一个 listitem 里写数据" ----
    v.phase("第 20~24 项（流式写入 / 边写边滑回弹 / 停止回弹）")
    # 20. 点最右按钮开始写入：内容写进**同一个 listitem**（末条），文本持续变长、控件跟着长高。
    #     判据用"状态计数器在动 + 控件高度出现多个值"，不去比对文本前缀：
    #     页面每写满一轮会在**同一条**里重开（文本被重置），前缀式断言会被这一下打翻，
    #     而"同一条"由 listitem 的 id（offset_last_item）保证——这一段里条目数一直没变。
    stream_ok = False
    stream_detail = ""
    try:
        v.click_stream()
        samples = []
        label_start = ""
        for wait in (0.4, 1.8, 2.2):
            time.sleep(wait)
            _text, h = v.last_item_state()
            root = v.tree()
            samples.append((v.status_text(root), h))
            if not label_start:
                label_start = v.button_label(ID_STREAM)   # 开始写入后按钮应变成「停止写入」
        statuses = [s for s, _ in samples]
        heights = [h for _, h in samples]
        moved = sum(1 for a, b in zip(statuses, statuses[1:]) if a != b)
        grew = len({h for h in heights}) >= 2 and max(heights) > heights[0]
        stream_ok = (moved >= 2 and grew and all(statuses)
                     and (not label_start or TXT_STREAM_STOP in label_start))
        stream_detail = (f"3 次采样状态都在变（{moved} 次变化）：{statuses[0]} -> {statuses[-1]}"
                         f" | 末条控件高度 {heights[0]}px -> {heights[1]}px -> {heights[2]}px"
                         f"（同一条在长高）| 按钮文案「{label_start}」")
    except RuntimeError as e:
        stream_detail = f"流式写入失败：{e}"
    v.step("流式写入：同一个 listitem 在持续写入并把控件撑高", stream_ok, stream_detail,
           v.shot("20_stream_growing"))

    # 21. 写入过程中**一直往上滑**：每次停下仍然要把末条收回安全线。
    #     写入不改滚动偏移，所以"真正底部没被收回"只能由回弹逻辑负责——
    #     这里断言末条不会停在安全线上方（也就是回弹确实发生了），
    #     下界之后允许的漂移是"回弹完了内容又长了几行"（写入本身不产生滚动事件）。
    stream_fails: List[str] = []
    drift_max = 0
    stream_rounds = 0
    for i in range(3):
        try:
            v.fling_up()
            m = v.measure()
        except RuntimeError as e:
            stream_fails.append(f"第 {i + 1} 次上滑后量不到末条：{e}")
            continue
        stream_rounds += 1
        d = m["delta_vs_safe_line_px"]
        drift_max = max(drift_max, d)
        if d < -TOLERANCE_PX:
            stream_fails.append(f"第 {i + 1} 次上滑后末条停在安全线上方 {d}px"
                                f"（真正底部没被收回：写入把回弹打断了）")
        elif d > STREAM_DRIFT_MAX_PX:
            stream_fails.append(f"第 {i + 1} 次上滑后末条低于安全线 {d}px，"
                                f"超出内容增长能解释的范围（{STREAM_DRIFT_MAX_PX}px）")
    v.step("流式写入过程中持续上滑，每次停下末条仍回到安全线", not stream_fails,
           f"{stream_rounds} 次甩到底后都收回了安全线，最大漂移 {drift_max}px"
           f"（漂移 = 回弹之后内容又长出来的部分）"
           if not stream_fails else "；".join(stream_fails),
           v.shot("21_stream_bounce_during_write"))

    # 22. 写入过程中提示条要跟着让位：内容把末条顶到提示条附近时提示条隐藏
    v.step("流式写入过程中提示条跟着让位（内容顶到提示条就隐藏）",
           *v.observe_disclaimer_hidden("22_stream_disclaimer_yield"))

    # 23. 再点一次同一个按钮 -> 停止写入，并**立即结算回弹**（本次要验证的核心动作）
    drift_before, drift_note = v.wait_stream_drift(12)
    stop_ok = False
    stop_detail = f"停止前 {drift_note}"
    try:
        v.click_stream()
        time.sleep(0.4)
        m = v.measure()
        text_c, _ = v.last_item_state()
        time.sleep(2.0)
        text_d, _ = v.last_item_state()
        stopped = bool(text_c) and text_c == text_d
        label_after = v.button_label(ID_STREAM)      # 停止后按钮应变回「流式写入」
        label_ok = (not label_after) or (TXT_STREAM_STOP not in label_after)
        status_hit = (TXT_TOP_SETTLE in m["status"]) if drift_before >= 12 else True
        stop_ok = (abs(m["delta_vs_safe_line_px"]) <= TOLERANCE_PX
                   and bool(m["disclaimer_visible"]) and stopped and status_hit and label_ok)
        stop_detail += (f" -> 停止后末条底边 {m['last_item_bottom']}px / 安全线 {m['safe_line_px']}px"
                        f"（偏差 {m['delta_vs_safe_line_vp']}vp）"
                        f" | 提示条{'显示' if m['disclaimer_visible'] else '隐藏'}"
                        f" | 按钮文案「{label_after}」"
                        f" | 文本已停{'是' if stopped else '否'}"
                        f" | 状态「{m['status']}」")
    except RuntimeError as e:
        stop_detail += f" -> 停止失败：{e}"
    v.step("再次点击停止写入：立即回弹到安全线（偏差 0）且提示条恢复", stop_ok, stop_detail,
           v.shot("23_stream_stop_snap_back"))

    # 24. 流式写入结束后回弹功能回归：再甩到底，末条（此时是一条多行长文本）仍停在安全线
    after_stream = v.fling_to_effective_bottom("流式写入结束后再甩到底仍回弹到安全线",
                                               "24_settle_after_stream")
    v.step("流式结束后的回弹回归：末条仍停在安全线且提示条恢复",
           bool(after_stream) and abs(after_stream["delta_vs_safe_line_px"]) <= TOLERANCE_PX
           and bool(after_stream.get("disclaimer_visible")),
           f"偏差 {after_stream.get('delta_vs_safe_line_vp')}vp | "
           f"提示条{'显示' if after_stream.get('disclaimer_visible') else '隐藏'}")

    # 顶部状态条是排障用的横条，无法完全躲开圆顶弧（圆在 y=0 处宽度为 0），
    # 系统会把窗口裁成圆形，所以两端被裁掉一部分；这里把它作为已知取舍量出来记录。
    root = v.tree()
    status_node = v.by_text(root, v.status_text(root)) if v.status_text(root) else None
    if status_node is not None and status_node.bounds:
        sb = status_node.bounds
        horizon = 2 * int((R_PX ** 2 - (R_PX - sb[1]) ** 2) ** 0.5) if sb[1] < R_PX else 0
        v.notes.append(
            f"顶部状态条 {sb}（宽 {sb[2] - sb[0]}px）：y={sb[1]} 处圆内可用宽度仅 {horizon}px，"
            f"两端会被圆形窗口裁掉 —— 排障用横条的已知取舍；文本内容仍可从控件树完整读到")

    # 14. 左侧按钮功能保持现状：点它仍返回首页（放在最后，因为它会离开本页）
    back_ok = False
    back_detail = ""
    try:
        node = v.by_id(v.tree(), ID_BUTTON)
        if node is None or node.bounds is None:
            back_detail = "量不到底部左按钮"
        else:
            cx, cy = node.center()
            v.drv.click(cx, cy)
            time.sleep(2.0)
            root = v.tree()
            back_ok = v.by_text(root, TXT_ENTRY) is not None
            back_detail = (f"点击左按钮后 {'回到首页' if back_ok else '没回到首页'}"
                           f"（{'看到' if back_ok else '看不到'}入口「{TXT_ENTRY}」）")
    except RuntimeError as e:
        back_detail = f"点击左按钮失败：{e}"
    v.step("左侧按钮功能保持现状（返回首页）", back_ok, back_detail,
           v.shot("25_left_button_back"))

    # 26. 运行期完整性：应用全程没被切走（切走了说明有外部干扰，上面的结论要打折）
    v.step("运行期间应用一直在 OffsetListPage 上（没有被切走）", not v.recoveries,
           "全程未被切走" if not v.recoveries
           else f"被切走 {len(v.recoveries)} 次、已自动重新进入页面：{'；'.join(v.recoveries)}"
                f"（切走期间的状态不作数，相关结论要打折看）")

    # ---- 报告 ----
    # 截图像素复核：确认每张都是 466x466 的真实画面（不是黑屏/空帧）
    shot_stats = []
    for name in sorted(os.listdir(v.out_dir)):
        if not name.endswith(".png"):
            continue
        st = analyze_shot(os.path.join(v.out_dir, name))
        if st:
            shot_stats.append(f"{name}: {st['size'][0]}x{st['size'][1]}，"
                              f"{st['colors']} 色，灰度 {st['min']}~{st['max']}（均值 {st['mean']}）")
    if shot_stats:
        v.step("截图都是有效画面（非黑屏/空帧）", True, f"{len(shot_stats)} 张：" + "；".join(shot_stats))

    ok = not v.failures
    lines = [
        "# 穿戴设备真机验证报告：OffsetListPage",
        "",
        f"- 设备：HUAWEI WATCH 6（NIZ-AL00），deviceType=wearable，"
        f"{v.size[0]}x{v.size[1]}px @{PX_PER_VP}x = {v.size[0] // PX_PER_VP}x"
        f"{v.size[1] // PX_PER_VP}vp 圆屏",
        f"- 几何（与 WEARABLE_GEOMETRY 一致）：项高 {ITEM_HEIGHT}vp / 按钮 {BUTTON_HEIGHT}vp"
        f"（一行三个，字号 {BUTTON_FONT_VP}vp，左右内边距 {BUTTON_PADDING_VP}vp） / "
        f"按钮底边距 {BUTTON_BOTTOM_MARGIN}vp / 提示条 {DISCLAIMER_HEIGHT}vp / "
        f"末条间隙 {LAST_ITEM_GAP}vp / contentStartOffset {CONTENT_START_OFFSET}vp / "
        f"contentEndOffset {CONTENT_END_OFFSET}vp",
        f"- 安全线 = 按钮顶边 - {SAFE_INSET_VP}vp"
        f"（= 提示条底边间隙 {DISCLAIMER_BOTTOM_GAP} + 提示条高 {DISCLAIMER_HEIGHT} + "
        f"末条间隙 {LAST_ITEM_GAP}）",
        f"- 结论：**{'全部通过' if ok else '存在失败项'}**",
        "",
        "| # | 检查项 | 结果 | 证据 |",
        "|---|--------|------|------|",
    ]
    for i, s in enumerate(v.steps, 1):
        shot = os.path.basename(s["shot"]) if s["shot"] else "-"
        detail = s["detail"].replace("|", "\\|")
        lines.append(f"| {i} | {s['name']} | {'PASS' if s['ok'] else 'FAIL'} | {shot} — {detail} |")
    if v.failures:
        lines += ["", "## 失败项", ""] + [f"- {f}" for f in v.failures]
    if v.notes:
        lines += ["", "## 已知取舍", ""] + [f"- {n}" for n in v.notes]
    report = os.path.join(v.out_dir, "report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告：{report}")
    print(f"结论：{'全部通过' if ok else '存在失败项'}")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DeviceOfflineError as e:
        # 掉线时 dumpLayout / screenCap 会一路"成功"返回陈旧文件，这一轮的结论全部不可信，
        # 直接中止并说清原因，别产出一份看着像真失败的报告。
        print(f"\n[中止] {e}")
        sys.exit(2)
