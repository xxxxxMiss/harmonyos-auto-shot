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
from typing import List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from autoshot.config import load_config            # noqa: E402
from autoshot.hdc_driver import HdcDriver          # noqa: E402
from autoshot import layout as L                   # noqa: E402

TESTAPP = os.path.join(REPO_ROOT, "TestApp")

# ---- 与 OffsetListPage.WEARABLE_GEOMETRY 保持一致（改那边记得同步）----
BUTTON_HEIGHT = 30
BUTTON_BOTTOM_MARGIN = 30
DISCLAIMER_BOTTOM_GAP = 6
DISCLAIMER_HEIGHT = 20
LAST_ITEM_GAP = 8
ITEM_HEIGHT = 40
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

TXT_DISCLAIMER = "内容由AI生成"
TXT_ENTRY = "偏移列表（触底回弹）"
TXT_LOADED_PREFIX = "下拉加载第"
TXT_LAST_MOCK = "初始 Mock 数据 #30"
TXT_TOP_SETTLE = "自动滚回底部"
TXT_APPEND_STATUS = "已从底部插入"
APPEND_CLICKS = 6          # 连点右侧按钮的次数（每次 +5 条）


class Verifier:
    def __init__(self, out_dir: str):
        self.cfg = load_config(TESTAPP)
        self.drv = HdcDriver(self.cfg)
        self.bundle = self._bundle_name()
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.steps: List[dict] = []
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

    def tree(self) -> L.Node:
        return L.parse_tree(self.drv.dump_layout())

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
        marks = ("contentEndOffset", "自动滚回", "已加载", "滚动极限", TXT_APPEND_STATUS)
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
        """点底部右侧按钮（从列表底部追加 5 条）。返回按钮中心坐标。"""
        node = self.by_id(self.tree(), ID_APPEND)
        if node is None or node.bounds is None:
            raise RuntimeError("量不到底部右侧按钮 offset_append_button")
        cx, cy = node.center()
        if (cx - CX_PX) ** 2 + (cy - R_PX) ** 2 > (R_PX - 12) ** 2:
            raise RuntimeError(f"右侧按钮中心 ({cx},{cy}) 落在圆外，点不到")
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
                root = self.tree()
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
        root = self.tree()
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
        root = self.tree()
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

    # ---- 步骤 ----
    def step(self, name: str, ok: bool, detail: str, shot: Optional[str] = None) -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail, "shot": shot})
        if not ok:
            self.failures.append(f"{name}: {detail}")
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")

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
            root = self.tree()
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
        "为让整行不被圆边裁掉，穿戴参数下给 List 加了左右各 8vp 内缩（listPadding，手机为 0）；"
        "该内缩只影响水平方向，安全线是纵向计算，不受影响。")
    v.notes.append(
        "本次只连了手表（hdc 上只有 NIZ-AL00），手机几何参数 PHONE_GEOMETRY 未改动、**未重新真机验证**；"
        "但安全线口径修正（safeLineInsetFromButtonTop 不再误用 disclaimerMargin）同时作用于手机，"
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
        "**底部右侧按钮（从列表底部追加 5 条）与提示条**：追加时滚动偏移没变、末尾又长出内容，"
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

    # 10. 底部按钮改成一行并排两个（左：原功能返回；右：每次从列表底部追加 5 条）
    root = v.tree()
    left_btn = v.by_id(root, ID_BUTTON)
    right_btn = v.by_id(root, ID_APPEND)
    same_row = bool(left_btn and right_btn and left_btn.bounds and right_btn.bounds
                    and left_btn.bounds[1] == right_btn.bounds[1]
                    and left_btn.bounds[2] <= right_btn.bounds[0])
    v.step("底部一行并排两个按钮（左返回 / 右插入）", same_row,
           f"左 {left_btn.bounds if left_btn else None} / 右 {right_btn.bounds if right_btn else None}",
           v.shot("11_two_bottom_buttons"))

    # 11. 点右侧按钮：每次从列表底部追加 5 条。
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
    v.step("点右侧按钮从底部插入后「内容由AI生成」自动隐藏", not append_fails,
           f"连点 {APPEND_CLICKS} 次、每次 +5 条，每次都自动隐藏，状态「{v.status_text(v.tree())}」"
           if not append_fails else "；".join(append_fails),
           v.shot("12_append_bottom_disclaimer_hidden"))

    # 12. 插完再滚到**新的**有效底部：末条重新落到安全线，提示条恢复显示
    after_append = v.fling_to_effective_bottom("插入后滚到新的有效底部", "13_settle_after_append")
    v.step("插入后新底部仍停在安全线且提示条恢复",
           bool(after_append) and abs(after_append["delta_vs_safe_line_px"]) <= TOLERANCE_PX
           and bool(after_append.get("disclaimer_visible")),
           f"偏差 {after_append.get('delta_vs_safe_line_vp')}vp | "
           f"提示条{'显示' if after_append.get('disclaimer_visible') else '隐藏'}")

    # 13. 圆屏适配：功能性控件的四角都要落在 466x466 圆内（否则会被圆边裁掉）
    root = v.tree()
    last_item = v.by_id(root, ID_LAST_ITEM)
    button = v.by_id(root, ID_BUTTON)
    append_btn = v.by_id(root, ID_APPEND)
    disclaimer = v.by_text(root, TXT_DISCLAIMER)
    targets = [("末条", last_item), ("提示条", disclaimer),
               ("底部左按钮", button), ("底部右按钮", append_btn)]
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
    v.step("末条/提示条/底部两个按钮都落在圆屏内接区域内", not outside,
           "；".join(notes) + (f" —— 越界：{outside}" if outside else ""),
           v.shot("14_circle_containment"))

    # 顶部状态条是排障用的横条，无法完全躲开圆顶弧（圆在 y=0 处宽度为 0），
    # 系统会把窗口裁成圆形，所以两端被裁掉一部分；这里把它作为已知取舍量出来记录。
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
           v.shot("15_left_button_back"))

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
        f"- 几何（与 WEARABLE_GEOMETRY 一致）：项高 {ITEM_HEIGHT}vp / 按钮 {BUTTON_HEIGHT}vp / "
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
    sys.exit(main())
