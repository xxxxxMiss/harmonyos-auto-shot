"""无障碍树（uitest dumpLayout JSON）解析与文本匹配。

dumpLayout 的节点结构在不同系统版本上字段命名略有差异，
这里对 attributes 做宽容读取（text/content、key/id 等），
bounds 形如 "[x1,y1][x2,y2]"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

_BOUNDS_RE = re.compile(r"\[(-?\d+)\s*,\s*(-?\d+)\]\s*\[(-?\d+)\s*,\s*(-?\d+)\]")
_ELLIPSIS = "\u2026\u2027\u22ef"  # … ‧ ⋯ 截断省略号


def parse_bounds(text: str) -> Optional[Tuple[int, int, int, int]]:
    if not text:
        return None
    m = _BOUNDS_RE.search(str(text))
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def normalize_text(text: str) -> str:
    """CJK 场景下去掉全部空白后比较，避免换行/空格差异。"""
    return re.sub(r"\s+", "", str(text or "")).strip(_ELLIPSIS)


def make_pattern(value: str) -> Optional[re.Pattern]:
    """资源值含格式占位符（%s/%d/%1$s/{n}）时，生成宽松正应用于匹配渲染后的文本。"""
    if not re.search(r"%[sd@]|%\d+\$s|\{\d+\}", value):
        return None
    parts = re.split(r"(%[sd@]|%\d+\$s|\{\d+\})", value)
    expr = "".join(p if i % 2 == 0 else ".*?" for i, p in enumerate(parts))
    return re.compile("^" + re.escape(expr).replace(re.escape(".*?"), ".*?") + "$", re.S)


@dataclass
class Node:
    attrs: Dict[str, object]
    children: List["Node"]
    raw: Dict

    @property
    def text(self) -> str:
        for k in ("text", "content", "value"):
            v = self.attrs.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    @property
    def key(self) -> str:
        for k in ("key", "id"):
            v = self.attrs.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    @property
    def bounds(self) -> Optional[Tuple[int, int, int, int]]:
        return parse_bounds(str(self.attrs.get("bounds", "") or ""))

    @property
    def visible(self) -> bool:
        return str(self.attrs.get("visible", "true")).lower() == "true"

    @property
    def clickable(self) -> bool:
        return str(self.attrs.get("clickable", "false")).lower() == "true"

    @property
    def scrollable(self) -> bool:
        return str(self.attrs.get("scrollable", "false")).lower() == "true"

    def center(self) -> Optional[Tuple[int, int]]:
        b = self.bounds
        if not b:
            return None
        return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)

    def size(self) -> Optional[Tuple[int, int]]:
        b = self.bounds
        if not b:
            return None
        return (b[2] - b[0], b[3] - b[1])


def parse_tree(data) -> Node:
    """递归解析；对根节点格式差异（dict / list / 包 instanceId 的包装）宽容处理。"""
    if isinstance(data, list):
        # 多窗口：合并为虚拟根
        return Node({}, [parse_tree(d) for d in data], {"children": data})
    attrs = data.get("attributes") or {}
    children = data.get("children") or []
    return Node(attrs, [parse_tree(c) for c in children], data)


def iter_nodes(root: Optional[Node]) -> Iterable[Node]:
    if root is None:
        return
    stack = [root]
    while stack:
        n = stack.pop()
        if n.attrs:
            yield n
        stack.extend(reversed(n.children))


@dataclass
class Match:
    node: Node
    score: int          # 3=精确 2=含占位符正则/包含 1=弱包含
    matched_text: str


def find_by_text(root: Optional[Node], candidates: Iterable[str]) -> List[Match]:
    """在控件树中查找展示目标文案的节点，按得分降序。

    匹配降级链：精确 -> 占位符正则 -> 包含（归一化后）。
    """
    pats = [(v, make_pattern(v)) for v in candidates if v]
    matches: List[Match] = []
    for n in iter_nodes(root):
        if not n.visible:
            continue
        t = normalize_text(n.text)
        if not t:
            continue
        for value, pat in pats:
            v = normalize_text(value)
            if not v:
                continue
            if t == v:
                matches.append(Match(n, 3, value))
                break
            if pat is not None and pat.match(t):
                matches.append(Match(n, 2, value))
                break
            if v in t or (len(t) >= 2 and t in v):
                matches.append(Match(n, 1, value))
                break
    matches.sort(key=lambda m: -m.score)
    return matches


@dataclass
class Viewport:
    width: int
    height: int
    margin_top: int = 96
    margin_bottom: int = 96
    margin_side: int = 16

    def contains(self, bounds: Tuple[int, int, int, int]) -> bool:
        x1, y1, x2, y2 = bounds
        return (x1 >= self.margin_side and x2 <= self.width - self.margin_side
                and y1 >= self.margin_top and y2 <= self.height - self.margin_bottom)

    def safe_center(self) -> Tuple[int, int]:
        return (self.width // 2, (self.margin_top + self.height - self.margin_bottom) // 2)

    def clamp(self, x: int, y: int) -> Tuple[int, int]:
        x = max(self.margin_side + 1, min(x, self.width - self.margin_side - 1))
        y = max(self.margin_top + 1, min(y, self.height - self.margin_bottom - 1))
        return (x, y)


def scroll_delta_to_center(bounds: Tuple[int, int, int, int], vp: Viewport) -> int:
    """目标需要移动的像素距离（>0 表示内容需上移，即手指上滑）。"""
    cy = (bounds[1] + bounds[3]) // 2
    return cy - vp.safe_center()[1]


def guess_screen_size(root: Optional[Node]) -> Optional[Tuple[int, int]]:
    """自动探测兜底：取全部可见节点 bounds 的最大外接（多数页面铺满屏幕时近似成立）。"""
    if root is None:
        return None
    max_x = max_y = 0
    for n in iter_nodes(root):
        b = n.bounds
        if b:
            max_x, max_y = max(max_x, b[2]), max(max_y, b[3])
    if max_x > 0 and max_y > 0:
        return (max_x, max_y)
    return None
