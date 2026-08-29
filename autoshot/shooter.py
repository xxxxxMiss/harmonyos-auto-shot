"""截图主流程：滚动到视野 -> idle 稳定 -> 视口校验 -> 截屏保存。

核心循环（对应设计文档 C 层）：
  dump 树 → 查目标文案
    ├─ 命中且在视口内 → idle 等待后截屏
    ├─ 命中但在视口外 → 按 bounds 与视口中心偏差小步修正滚动
    └─ 未命中（懒加载列表目标尚未渲染）→ 按当前方向整页滚动
  超过步数上限后自动反向再找一轮。
"""
from __future__ import annotations

import io
import os
import re
import time
from typing import Iterable, List, Optional, Protocol, Tuple

from .layout import (Node, Viewport, find_by_text, parse_tree, scroll_delta_to_center)


class DriverProtocol(Protocol):
    def dump_layout(self) -> dict: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> None: ...
    def click(self, x: int, y: int) -> None: ...
    def key_event(self, key: str = "Back") -> None: ...
    def input_text(self, x: int, y: int, text: str) -> None: ...
    def screenshot(self) -> bytes: ...
    def display_size(self) -> Tuple[int, int]: ...


class TargetNotFound(RuntimeError):
    pass


def tree_signature(tree) -> str:
    """取可见节点的 文本+bounds 指纹，用于 idle 判断。"""
    parts = []
    for n in _walk(parse_tree(tree)):
        b = n.bounds
        parts.append(f"{n.text}|{b}")
    return "\n".join(sorted(parts))


def _walk(root: Node):
    stack = [root]
    while stack:
        n = stack.pop()
        if n.attrs and n.visible and (n.text or n.bounds):
            yield n
        stack.extend(n.children)


def wait_idle(driver: DriverProtocol, cfg, max_wait: Optional[float] = None) -> None:
    """连续两次 dump 指纹一致即认为 UI 稳定（动画/加载结束）。"""
    deadline = time.time() + (max_wait or cfg.idle_timeout)
    prev = None
    while time.time() < deadline:
        try:
            cur = tree_signature(driver.dump_layout())
        except Exception:
            time.sleep(cfg.poll_interval)
            continue
        if cur == prev and cur is not None:
            return
        prev = cur
        time.sleep(cfg.poll_interval)


def sniff_format(data: bytes) -> Optional[str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    return None


def save_screenshot(data: bytes, out_path: str, fmt: str) -> str:
    """按目标格式保存；源格式与目标不一致时用 Pillow 转码（一致则直写零损耗）。"""
    src = sniff_format(data)
    if src is None:
        raise RuntimeError(f"截屏数据非 PNG/JPEG（前 8 字节: {data[:8].hex()}）")
    if src == fmt:
        with open(out_path, "wb") as f:
            f.write(data)
        return out_path
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError(
            f"截屏为 {src}，目标 {fmt}，转码需要 Pillow：pip3 install Pillow（或改用 --format {src}）") from None
    with Image.open(io.BytesIO(data)) as img:
        if fmt == "jpeg":
            img.convert("RGB").save(out_path, "JPEG", quality=92)
        else:
            img.save(out_path, "PNG")
    return out_path


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_\-.]")


def safe_name(key: str) -> str:
    return _SAFE_NAME.sub("_", key)[:120] or "unnamed"


class Shooter:
    def __init__(self, driver: DriverProtocol, cfg):
        self.driver = driver
        self.cfg = cfg

    # ---- 视口 ----
    def _viewport(self, tree) -> Viewport:
        w, h = self.driver.display_size()
        return Viewport(w, h, self.cfg.viewport_margin_top,
                        self.cfg.viewport_margin_bottom, self.cfg.viewport_margin_side)

    # ---- 滚动 ----
    def _page_scroll(self, vp: Viewport, direction: str) -> None:
        w, h = vp.width, vp.height
        if direction == "up":       # 手指上滑，看下方内容
            self.driver.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.32))
        else:
            self.driver.swipe(w // 2, int(h * 0.32), w // 2, int(h * 0.72))

    def _center_scroll(self, delta: int, vp: Viewport) -> None:
        """内容需要移动 delta 像素（>0 上移）→ 手指反向滑动 delta。"""
        delta = max(24, min(abs(int(delta)), int(vp.height * 0.8)))
        x = vp.width // 2
        y_from = vp.safe_center()[1]
        y_to = y_from - delta if delta > 0 else y_from + delta
        x, y_from = vp.clamp(x, y_from)
        _, y_to = vp.clamp(x, y_to)
        self.driver.swipe(x, y_from, x, y_to)

    # ---- 主流程 ----
    def capture_texts(self, name: str, candidates: Iterable[str]) -> str:
        """把展示 candidates 任一文案的 UI 滚入视野并截屏，返回产物路径。"""
        cands = [c for c in candidates if c]
        if not cands:
            raise TargetNotFound(f"{name}: 无候选文案")
        directions = [self.cfg.scroll_direction]
        reverse = "down" if self.cfg.scroll_direction == "up" else "up"
        if reverse not in directions:
            directions.append(reverse)

        last_tree = None
        for direction in directions:
            for step in range(self.cfg.max_scroll_steps):
                tree = self.driver.dump_layout()
                last_tree = tree
                matches = find_by_text(parse_tree(tree), cands)
                vp = self._viewport(tree)
                if matches:
                    m = matches[0]
                    b = m.node.bounds
                    if b and vp.contains(b):
                        # 命中且在视口内：短暂稳定后立即截图。
                        # （瞬态 UI 如 Toast 只存活数秒，不能走长 wait_idle+二次确认链路；
                        #   匹配本身来自一次完整 dump，静态页此时画面已稳定）
                        time.sleep(min(0.6, self.cfg.poll_interval + 0.2))
                        data = self.driver.screenshot()
                        out = os.path.join(self.cfg.output_dir,
                                           f"{safe_name(name)}.{self.cfg.image_format}")
                        os.makedirs(self.cfg.output_dir, exist_ok=True)
                        return save_screenshot(data, out, self.cfg.image_format)
                    if b:
                        self._center_scroll(scroll_delta_to_center(b, vp), vp)
                        time.sleep(self.cfg.poll_interval)
                        continue
                # 未命中：整页滚动寻找（懒加载列表目标可能尚未渲染）
                self._page_scroll(vp, direction)
                time.sleep(self.cfg.poll_interval)
        raise TargetNotFound(
            f"{name}: 滚动 {self.cfg.max_scroll_steps}x2 步后仍未在控件树中找到 {cands[:3]}...")
