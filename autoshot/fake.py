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
