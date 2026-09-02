"""运行时探索兜底（Explorer）：观察 -> 决策 -> 动作 闭环。

静态场景（navigator 步骤 + shooter 滚动）要求"导航推导完全正确"且"页面已到达"。
当静态 fast path 失败（动态路由、文案变化、前置状态、bfs 推导不出）时，
Explorer 把"跨页点击导航"与"页内滚动"合并成一个循环：每一步都重新 dump 控件树、
检查目标文案，再决定下一步（点哪个 / 往哪滚 / 回退 / 放弃）。

降级链：静态 fast path -> Explorer 启发式 ->（预留）LLM / 白盒注入 -> 失败报告。
详见仓库根目录《auto-shot运行时探索兜底设计.md》。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, Tuple

from .layout import (Node, Viewport, find_by_text, iter_nodes, normalize_text,
                     parse_tree, scroll_delta_to_center)
from .shooter import (Shooter, TargetNotFound, safe_name, save_screenshot,
                      tree_signature, wait_idle)

# 语义锚点：探索无图引导时用于继续深入的通用入口文案
DEFAULT_ANCHORS = ["设置", "更多", "我的", "登录", "菜单", "个人中心", "关于", "更多设置"]


@dataclass
class Action:
    """一次可执行动作。kind: click | scroll | back | stuck。"""
    kind: str
    node: Optional[Node] = None        # click 目标
    direction: str = "up"              # scroll 方向
    reason: str = ""                   # stuck 原因 / 调试信息

    def signature(self) -> str:
        if self.kind == "click" and self.node is not None:
            c = self.node.center()
            return f"click:{normalize_text(self.node.text)}@{c}"
        if self.kind == "scroll":
            return f"scroll:{self.direction}"
        if self.kind == "back":
            return "back"
        return f"stuck:{self.reason}"


class Policy(Protocol):
    """决策策略。返回 None 表示"该策略无动作/未启用，交给策略链下一层"。"""

    def decide(self, snap: "Snapshot", candidates: Iterable[str]) -> Optional[Action]: ...


@dataclass
class Snapshot:
    """一次 dump 的剪枝视图：可见且（有文本 / 可点 / 可滚）的节点。"""
    root: Node
    nodes: List[Node]                 # 剪枝后的全部
    clickable: List[Node]             # 剪枝后可点
    scrollable: List[Node]            # 剪枝后可滚
    sig: str
    viewport: Viewport


def prune_nodes(root: Node, limit: int = 80) -> List[Node]:
    """剪枝：保留有文本或可点/可滚的节点，按 (文本, bounds) 去重，丢弃纯容器。"""
    out: List[Node] = []
    seen = set()
    for n in iter_nodes(root):
        if not n.visible:
            continue
        has_text = bool(normalize_text(n.text))
        if not (has_text or n.clickable or n.scrollable):
            continue
        key = (normalize_text(n.text), n.bounds)
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
        if len(out) >= limit:
            break
    return out


def _snapshot(root: Node, vp: Viewport, sig: str) -> Snapshot:
    nodes = prune_nodes(root)
    clickable = [n for n in nodes if n.clickable]
    scrollable = [n for n in nodes if n.scrollable]
    return Snapshot(root=root, nodes=nodes, clickable=clickable,
                    scrollable=scrollable, sig=sig, viewport=vp)


def _find_clickable(snap: Snapshot, text: str) -> Optional[Node]:
    """在可点节点里按文案（归一化后相等或包含）找第一个。"""
    t = normalize_text(text)
    if not t:
        return None
    for n in snap.clickable:
        nt = normalize_text(n.text)
        if not nt:
            continue
        if nt == t or t in nt or nt in t:
            return n
    return None


class HeuristicPolicy:
    """图引导 + 文本相似 + 语义锚点的启发式决策。

    优先级：弹窗 -> 导航图边标签 -> 候选/锚点相似 -> 滚动 -> 语义锚点 -> 放弃。
    回退（back）由 Explorer 依据导航深度在 stuck 时处理。
    """

    def __init__(self, cfg, page_adj: Optional[dict] = None, index=None):
        self.cfg = cfg
        self.page_adj = page_adj or {}
        self.index = index
        self._edge_labels = self._collect_edge_labels()
        self._anchors = list(getattr(cfg, "anchor_texts", None) or DEFAULT_ANCHORS)

    def _collect_edge_labels(self) -> List[str]:
        """导航图里所有边的触发文案（运行时核对这些按钮是否真的在屏幕上）。"""
        labels: List[str] = []
        for edges in self.page_adj.values():
            for e in edges:
                if e.get("viaText"):
                    labels.append(e["viaText"])
                for it in (e.get("viaItems") or []):
                    labels.append(it)
                if e.get("viaKey"):
                    from .auto_scene import resolve_label
                    lbl = resolve_label(e["viaKey"], None, self.index)
                    if lbl:
                        labels.append(lbl)
        return sorted(set(labels))

    def decide(self, snap: Snapshot, candidates: Iterable[str]) -> Optional[Action]:
        # 1. 权限 / 打扰弹窗：先点掉，否则会挡住后续点击
        for texts in (self.cfg.grant_texts, self.cfg.dismiss_texts):
            for t in texts:
                n = _find_clickable(snap, t)
                if n:
                    return Action("click", node=n, reason=f"popup:{t}")
        # 2. 导航图边标签：跟着静态推导出的可达路径走（运行时纠偏）
        for lbl in self._edge_labels:
            n = _find_clickable(snap, lbl)
            if n:
                return Action("click", node=n, reason=f"edge:{lbl}")
        # 3. 候选文案本身出现在可点节点上（如"关于"入口）→ 点进去继续找
        for cand in candidates:
            if not cand:
                continue
            n = _find_clickable(snap, cand)
            if n:
                return Action("click", node=n, reason=f"candidate:{cand}")
        # 4. 有可滚容器 → 继续翻
        if snap.scrollable:
            return Action("scroll", direction=self.cfg.scroll_direction, reason="scroll")
        # 5. 语义锚点
        for a in self._anchors:
            n = _find_clickable(snap, a)
            if n:
                return Action("click", node=n, reason=f"anchor:{a}")
        # 6. 无动作：返回 None，交给策略链下一层（LLM / 白盒注入），或由 Explorer 判定放弃
        return None


class Explorer:
    """观察-决策-动作主循环。可复用 Shooter 的滚动/截屏能力。"""

    def __init__(self, driver, cfg, policy: Optional[HeuristicPolicy] = None,
                 policies: Optional[List[Policy]] = None, shooter: Optional[Shooter] = None):
        self.driver = driver
        self.cfg = cfg
        self.shooter = shooter or Shooter(driver, cfg)
        if policies is not None:
            self.policies = list(policies)
        elif policy is not None:
            self.policies = [policy]
        else:
            self.policies = [HeuristicPolicy(cfg)]
        self._depth = 0              # 粗略导航深度（点击 +1，回退 -1）
        self._seen_sigs: dict = {}   # 状态指纹计数，用于环路检测

    def _decide(self, snap: Snapshot, cands: Iterable[str]) -> Optional[Action]:
        """依次询问策略链，返回第一个非 None 动作；全部放弃返回 None。"""
        for p in self.policies:
            try:
                a = p.decide(snap, cands)
            except Exception:
                continue
            if a is not None:
                return a
        return None

    # ---- 视口 ----
    def _viewport(self, tree) -> Viewport:
        return self.shooter._viewport(tree)

    # ---- 动作执行 ----
    def _apply(self, action: Action, vp: Viewport) -> None:
        if action.kind == "click":
            c = action.node.center() if action.node else None
            if not c:
                return
            self.driver.click(*c)
            self._depth += 1
            wait_idle(self.driver, self.cfg)
        elif action.kind == "scroll":
            self.shooter._page_scroll(vp, action.direction)
            time.sleep(self.cfg.poll_interval)
        elif action.kind == "back":
            self.driver.key_event("Back")
            self._depth = max(0, self._depth - 1)
            time.sleep(self.cfg.poll_interval)

    def _shoot(self, name: str) -> str:
        time.sleep(min(0.6, self.cfg.poll_interval + 0.2))
        data = self.driver.screenshot()
        out = os.path.join(self.cfg.output_dir, f"{safe_name(name)}.{self.cfg.image_format}")
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        return save_screenshot(data, out, self.cfg.image_format)

    def _cycle_guard(self, sig: str) -> None:
        """同一状态反复出现 → 判定环路，避免无限点击。"""
        self._seen_sigs[sig] = self._seen_sigs.get(sig, 0) + 1
        if self._seen_sigs[sig] >= 3:
            raise TargetNotFound("探索检测到状态循环（同一画面反复出现），已中止")

    # ---- 主循环 ----
    def reach_and_shoot(self, name: str, candidates: Iterable[str]) -> str:
        cands = [c for c in candidates if c]
        if not cands:
            raise TargetNotFound(f"{name}: 无候选文案")
        for step in range(self.cfg.max_explore_steps):
            tree = self.driver.dump_layout()
            root = parse_tree(tree)
            vp = self._viewport(tree)
            sig = tree_signature(tree)
            self._cycle_guard(sig)
            snap = _snapshot(root, vp, sig)
            matches = find_by_text(root, cands)

            # 内容优先：非可点节点是真实展示内容
            content = [m for m in matches if not m.node.clickable]
            if content:
                m = content[0]
                if m.node.bounds and vp.contains(m.node.bounds):
                    return self._shoot(name)
                if m.node.bounds:
                    self.shooter._center_scroll(scroll_delta_to_center(m.node.bounds, vp), vp)
                    time.sleep(self.cfg.poll_interval)
                    continue
            # 可点节点：目标文案本身是按钮标签 → 视口内直接截，否则滚进视口
            buttons = [m for m in matches if m.node.clickable]
            if buttons:
                m = buttons[0]
                if m.node.bounds and vp.contains(m.node.bounds):
                    return self._shoot(name)
                if m.node.bounds:
                    self.shooter._center_scroll(scroll_delta_to_center(m.node.bounds, vp), vp)
                    time.sleep(self.cfg.poll_interval)
                    continue

            # 无匹配 → 策略链决定下一步
            action = self._decide(snap, cands)
            if action is None or action.kind == "stuck":
                if self._depth > 0:
                    self._apply(Action("back", reason="策略链无动作 回退"), vp)
                    continue
                raise TargetNotFound(
                    f"{name}: 探索 {step} 步后无可用动作")
            self._apply(action, vp)
        raise TargetNotFound(
            f"{name}: 探索 {self.cfg.max_explore_steps} 步后仍未找到 {cands[:3]}...")
