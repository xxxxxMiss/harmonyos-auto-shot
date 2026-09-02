"""FakeDriver：用内存模型模拟设备（懒加载列表滚动），无真机即可验证/演示核心循环。

模型：N 行等高 item，视口每次只渲染 window 行（模拟懒加载：未滚到的节点
根本不在无障碍树里），swipe 按像素换算滚动行数。
"""
from __future__ import annotations

import io
from typing import List, Tuple


def _tiny_jpeg() -> bytes:
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (30, 30, 30)).save(buf, "JPEG")
        return buf.getvalue()
    except ImportError:
        return b"\xff\xd8\xff\xe0" + b"\x00" * 200  # JPEG 魔数 + 占位


class FakeDriver:
    def __init__(self, items: List[str], window: int = 5, screen: Tuple[int, int] = (1080, 2340),
                 row_height: int = 160):
        self.items = items
        self.window = window
        self.screen = screen
        self.row_height = row_height
        self.offset = 0                       # 视口内第一行的下标
        self.shots: List[bytes] = []
        self.clicks: List[Tuple[int, int]] = []
        self.swipes: List[Tuple[int, int, int, int]] = []
        self.launched: List[str] = []

    # ---- DriverProtocol 实现 ----
    def dump_layout(self) -> dict:
        children = []
        for i in range(self.offset, min(self.offset + self.window, len(self.items))):
            y = 150 + (i - self.offset) * self.row_height
            children.append({
                "attributes": {
                    "text": self.items[i], "type": "Text", "visible": "true",
                    "clickable": "true", "bounds": f"[80,{y}][1000,{y + self.row_height - 20}]",
                },
                "children": [],
            })
        return {"attributes": {}, "children": children}

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.swipes.append((x1, y1, x2, y2))
        dy = y1 - y2                          # 手指上滑(dy>0) => 内容上移 => offset 增大
        self.offset = max(0, min(self.offset + round(dy / self.row_height),
                                 max(0, len(self.items) - self.window)))

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def key_event(self, key: str = "Back") -> None:
        pass

    def input_text(self, x: int, y: int, text: str) -> None:
        pass

    def screenshot(self) -> bytes:
        data = _tiny_jpeg()
        self.shots.append(data)
        return data

    def display_size(self) -> Tuple[int, int]:
        return self.screen

    # ---- 测试辅助 ----
    def visible_texts(self) -> List[str]:
        return [c["attributes"]["text"] for c in self.dump_layout()["children"]]


class AppDriver:
    """多页假应用：模拟"点按钮进页面 / 滚动列表"，用于验证 Explorer 的运行时兜底。

    页面由静态节点（按钮/文本，可点按钮带 target 跳转或 toggle 翻转状态）与
    可选的懒加载列表（scrollable）组成。满足 DriverProtocol，可直接交给
    Navigator / Shooter / Explorer。
    """

    def __init__(self, screen: Tuple[int, int] = (1080, 2340), row_height: int = 160):
        self.screen = screen
        self.row_height = row_height
        self.window = 5
        self.current: Optional[str] = None
        self.pages: dict = {}      # name -> list of node dicts（静态）
        self.lists: dict = {}      # name -> list of item strings（懒加载列表）
        self.offset = 0
        self.state: dict = {}      # toggle 状态
        self.clicks: List[Tuple[int, int]] = []
        self.swipes: List[Tuple[int, int, int, int]] = []
        self.shots: List[bytes] = []

    def add_page(self, name: str, nodes: List[dict]) -> "AppDriver":
        """nodes: [{"text", "clickable"?, "target"?, "toggle"?}]"""
        self.pages[name] = nodes
        if self.current is None:
            self.current = name
        return self

    def set_list(self, name: str, items: List[str], window: int = 5) -> "AppDriver":
        self.lists[name] = items
        self.window = window
        if self.current is None:
            self.current = name
        return self

    def go(self, name: str) -> None:
        self.current = name
        self.offset = 0

    # ---- DriverProtocol ----
    def _bounds(self, y: int) -> str:
        return f"[80,{y}][1000,{y + self.row_height - 20}]"

    def _static_children(self):
        out = []
        y = 150
        for nd in self.pages.get(self.current, []):
            out.append({
                "attributes": {
                    "text": nd["text"], "type": "Button" if nd.get("clickable") else "Text",
                    "visible": "true", "clickable": "true" if nd.get("clickable") else "false",
                    "bounds": self._bounds(y),
                },
                "children": [],
            })
            y += self.row_height
        return out, y

    def dump_layout(self) -> dict:
        children, y = self._static_children()
        items = self.lists.get(self.current)
        if items:
            for i in range(self.offset, min(self.offset + self.window, len(items))):
                ry = y + (i - self.offset) * self.row_height
                children.append({
                    "attributes": {
                        "text": items[i], "type": "ListItem", "visible": "true",
                        "clickable": "true", "scrollable": "true",
                        "bounds": self._bounds(ry),
                    },
                    "children": [],
                })
        return {"attributes": {}, "children": children}

    def _node_at(self, y: int):
        """由点击 y 坐标反查当前页静态节点 / 列表项。"""
        static_nodes = self.pages.get(self.current, [])
        for i, nd in enumerate(static_nodes):
            node_y = 150 + i * self.row_height
            if node_y <= y < node_y + self.row_height - 20:
                return nd
        items = self.lists.get(self.current)
        if items:
            list_top = 150 + len(static_nodes) * self.row_height
            for i in range(self.offset, min(self.offset + self.window, len(items))):
                ry = list_top + (i - self.offset) * self.row_height
                if ry <= y < ry + self.row_height - 20:
                    return {"text": items[i], "clickable": True}
        return None

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        nd = self._node_at(y)
        if not nd:
            return
        if nd.get("target"):
            self.current = nd["target"]
            self.offset = 0
        elif nd.get("toggle"):
            key = nd["toggle"]
            self.state[key] = not self.state.get(key, False)

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> None:
        self.swipes.append((x1, y1, x2, y2))
        items = self.lists.get(self.current)
        if not items:
            return
        dy = y1 - y2
        self.offset = max(0, min(self.offset + round(dy / self.row_height),
                                 max(0, len(items) - self.window)))

    def key_event(self, key: str = "Back") -> None:
        # 简化：Back 回到首页
        if self.current != "home" and "home" in self.pages:
            self.current = "home"
            self.offset = 0

    def input_text(self, x: int, y: int, text: str) -> None:
        pass

    def screenshot(self) -> bytes:
        data = _tiny_jpeg()
        self.shots.append(data)
        return data

    def display_size(self) -> Tuple[int, int]:
        return self.screen

