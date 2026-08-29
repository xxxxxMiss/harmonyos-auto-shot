"""导航器：按场景步骤驱动设备到达目标页面。

步骤语法（YAML 列表，每项一个动作）：
  - launch: com.example.app            # 启动应用（可加 ability=xxx）
  - deeplink: scheme://path            # deeplink 直跳
  - click: text=登录 | key=login_btn   # 按文案/控件 key 点击
  - expect: text=设置                  # 等待文案出现（带超时）
  - swipe: "0.5,0.8 -> 0.5,0.4"       # 比例坐标滑动
  - input: text=xxx                    # 向当前焦点输入文本
  - back / home / wait: 1.5
导航过程中自动处理权限弹窗（grant_texts）与可配置的打扰弹窗（dismiss_texts）。
"""
from __future__ import annotations

import re
import time
from typing import Optional, Tuple

from .layout import find_by_text, parse_tree
from .shooter import DriverProtocol, wait_idle

_STEP_RE = re.compile(r"^\s*(\w+)\s*=\s*(.+)$")
_SWIPE_RE = re.compile(r"^\s*([\d.]+)\s*,\s*([\d.]+)\s*->\s*([\d.]+)\s*,\s*([\d.]+)\s*$")


class StepError(RuntimeError):
    pass


class Navigator:
    def __init__(self, driver: DriverProtocol, cfg):
        self.driver = driver
        self.cfg = cfg

    # ---- 弹窗自动处理 ----
    def _handle_popups(self, tree) -> bool:
        for texts in (self.cfg.grant_texts, self.cfg.dismiss_texts):
            for t in texts:
                for m in find_by_text(tree, [t]):
                    if m.node.clickable and m.node.center():
                        self.driver.click(*m.node.center())
                        time.sleep(0.4)
                        return True
                    # 节点本身不可点时尝试点其中心（部分容器拦截）
                    c = m.node.center()
                    if c:
                        self.driver.click(*c)
                        time.sleep(0.4)
                        return True
        return False

    def _dump_with_popups(self):
        tree = parse_tree(self.driver.dump_layout())
        if self._handle_popups(tree):
            time.sleep(0.5)
            tree = parse_tree(self.driver.dump_layout())
        return tree

    # ---- 点击目标解析 ----
    def _resolve_target(self, spec: str, tree):
        m = _STEP_RE.match(spec)
        kind, value = (m.group(1), m.group(2).strip()) if m else ("text", spec.strip())
        from .layout import iter_nodes
        if kind in ("text", "contains"):
            hits = find_by_text(tree, [value])
            return hits[0].node if hits else None
        if kind in ("key", "id"):
            for n in iter_nodes(tree):
                if n.visible and n.key == value:
                    return n
            return None
        raise StepError(f"不支持的定位方式: {spec}")

    def _expect(self, spec: str, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        last_err: Optional[StepError] = None
        while time.time() < deadline:
            tree = self._dump_with_popups()
            node = self._resolve_target(spec, tree)
            if node:
                return
            last_err = StepError(f"expect 未命中: {spec}")
            time.sleep(self.cfg.poll_interval)
        raise last_err or StepError(f"expect 未命中: {spec}")

    # ---- 步骤执行 ----
    def run(self, steps) -> None:
        size = self.driver.display_size()
        for i, step in enumerate(steps, start=1):
            if not isinstance(step, dict) or len(step) != 1:
                raise StepError(f"第 {i} 步格式错误（应为单键 dict）: {step}")
            action, param = next(iter(step.items()))
            self._execute(i, action, param, size)

    def _execute(self, i: int, action: str, param, size: Tuple[int, int]) -> None:
        w, h = size
        action = str(action).strip().lower()
        if action == "launch":
            bundle = param if isinstance(param, str) else param.get("bundle")
            ability = param.get("ability") if isinstance(param, dict) else None
            # 重启式启动：清掉上个场景遗留的弹窗/页面状态，保证场景隔离
            # （force-stop 后立即 start 会被系统限流吞掉，需等 ~2.5s）
            try:
                self.driver.app_stop(bundle)
                time.sleep(2.5)
            except Exception:
                pass
            self.driver.app_start(bundle, ability)
            time.sleep(1.5)
        elif action == "deeplink":
            self.driver.open_uri(str(param))
            time.sleep(1.5)
        elif action == "click":
            self._expect(str(param), timeout=8.0)
            tree = self._dump_with_popups()
            node = self._resolve_target(str(param), tree)
            if not node or not node.center():
                raise StepError(f"第 {i} 步 click 未找到可点目标: {param}")
            self.driver.click(*node.center())
            wait_idle(self.driver, self.cfg)
        elif action == "expect":
            self._expect(str(param))
        elif action == "swipe":
            m = _SWIPE_RE.match(str(param))
            if not m:
                raise StepError(f"第 {i} 步 swipe 格式应为 '0.5,0.8 -> 0.5,0.4': {param}")
            fx, fy, tx, ty = (float(v) for v in m.groups())
            self.driver.swipe(int(fx * w), int(fy * h), int(tx * w), int(ty * h))
            wait_idle(self.driver, self.cfg)
        elif action == "input":
            text = param.get("text") if isinstance(param, dict) else str(param)
            self.driver.input_text(w // 2, h // 2, text)
        elif action == "back":
            self.driver.key_event("Back")
            time.sleep(0.5)
        elif action == "home":
            self.driver.key_event("Home")
            time.sleep(0.5)
        elif action == "wait":
            time.sleep(float(param))
        else:
            raise StepError(f"第 {i} 步不支持的动作: {action}")
